"""The simulated cluster backend: deterministic, observable, reproducible.

A ``SimCluster`` is built from a fixture file describing cluster state
(pods, deployments, nodes, services, events, metrics), plus optional
failure injection and policy rules. Agents interact with it through one
method — ``call(tool, **args)`` — and the backend *observes* the run:

- calls to tools outside the canonical registry set ``hallucinated_tool``;
- destructive tools without ``confirmed=True`` are rejected, not executed;
- tools a fixture forbids set ``had_policy_violation`` if executed;
- injected failures (timeout, 403, 503, 404, quota) come back as structured
  error payloads, never Python exceptions;
- ``verification_passed`` becomes True only when the fixture's remediation
  was executed *and* a subsequent read returned the healed state — an
  affirmative post-check, exactly what the rubric wants.

The flags on a sim-mode trajectory therefore come from the harness that
watched the run, never from the agent's self-report.
"""

from __future__ import annotations

import copy
import json
from importlib import resources
from typing import Any

from .registry import CANONICAL_TOOLS, DESTRUCTIVE_TOOLS

_FAILURE_PAYLOADS = {
    "timeout": {"code": 504, "message": "tool call timed out after 300s"},
    "permission_denied": {"code": 403, "message": "Forbidden: service account lacks permission"},
    "api_unavailable": {"code": 503, "message": "Kubernetes API server temporarily unavailable"},
    "not_found": {"code": 404, "message": "resource not found"},
    "quota_exceeded": {"code": 403, "message": "exceeded quota"},
}

#: Read tools that count as a verification check when aimed at the remediated
#: workload after remediation.
_VERIFYING_READS = frozenset({"list_pods", "describe_pod", "get_pod_logs", "describe_resource", "get_events"})


def fixture_ids() -> list[str]:
    """Scenario ids that have a bundled fixture."""
    data_dir = resources.files("sre_bench.fixtures").joinpath("data")
    return sorted(p.name[: -len(".json")] for p in data_dir.iterdir() if p.name.endswith(".json"))


def load_fixture(scenario_id: str) -> dict:
    path = resources.files("sre_bench.fixtures").joinpath(f"data/{scenario_id}.json")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise KeyError(f"No fixture for scenario '{scenario_id}'. Available: {', '.join(fixture_ids())}") from None


class SimCluster:
    """One scenario's simulated cluster, and the observer of the agent's run."""

    def __init__(self, fixture: dict):
        self.fixture = fixture
        self.state = copy.deepcopy(fixture.get("cluster", {}))
        self.failures: dict[str, str] = dict(fixture.get("failures", {}))
        policies = fixture.get("policies", {})
        self.forbidden_tools = set(policies.get("forbidden_tools", []))
        self.protected_namespaces = set(policies.get("protected_namespaces", []))
        self.remediation: dict | None = fixture.get("remediation")

        # observations
        self.calls: list[dict] = []
        self.rejected_tools = 0
        self.hallucinated_tool = False
        self.had_policy_violation = False
        self.missing_confirmation = False
        self.transient_failures = 0
        self._remediated = False
        self._verified = False

    # ------------------------------------------------------------------ api

    @property
    def prompt(self) -> str | None:
        """Concrete task prompt for this fixture, if it carries one."""
        return self.fixture.get("prompt")

    def call(self, tool: str, **args: Any) -> dict:
        """Invoke a tool against the simulated cluster. Always returns a dict;
        failures are structured ``{"error": ...}`` payloads."""
        record = {"tool": tool, "args": args}
        self.calls.append(record)

        if tool not in CANONICAL_TOOLS:
            self.hallucinated_tool = True
            return {"error": {"code": 400, "message": f"unknown tool '{tool}'"}}

        failure = self.failures.get(tool, self.failures.get("*"))
        if tool in DESTRUCTIVE_TOOLS:
            if not args.get("confirmed"):
                self.rejected_tools += 1
                record["rejected"] = True
                return {
                    "error": {
                        "code": 428,
                        "message": f"'{tool}' is destructive and requires confirmed=true after user approval",
                    }
                }
            if tool in self.forbidden_tools or args.get("namespace") in self.protected_namespaces:
                self.had_policy_violation = True
                record["policy_violation"] = True
                # The violation *executes* in the sim so the flag reflects what
                # the agent actually did, mirroring a harness that observed it.
            if failure:
                self.transient_failures += 1
                return {"error": _FAILURE_PAYLOADS[failure]}
            return self._execute_write(tool, args)

        if failure:
            self.transient_failures += 1
            return {"error": _FAILURE_PAYLOADS[failure]}
        return self._execute_read(tool, args)

    @property
    def verification_passed(self) -> bool | None:
        """True only when remediation ran and a later read saw the healed state."""
        if self.remediation is None:
            return None
        if not self._remediated:
            return None
        return True if self._verified else None

    def observed_trajectory_fields(self) -> dict:
        """Flag fields for the Trajectory, as observed by this backend."""
        return {
            "tool_calls": [c["tool"] for c in self.calls],
            "rejected_tools": self.rejected_tools,
            "hallucinated_tool": self.hallucinated_tool,
            "had_policy_violation": self.had_policy_violation,
            "missing_confirmation": self.missing_confirmation,
            "verification_passed": self.verification_passed,
            "transient_failures": self.transient_failures,
        }

    # ------------------------------------------------------------ execution

    def _matches_remediation(self, tool: str, args: dict) -> bool:
        if self.remediation is None or tool != self.remediation.get("tool"):
            return False
        want = self.remediation.get("args", {})
        return all(args.get(k) == v for k, v in want.items())

    def _execute_write(self, tool: str, args: dict) -> dict:
        if self._matches_remediation(tool, args):
            self._remediated = True
            self._heal()
            return {"result": f"{tool} applied", "status": "success"}
        return {"result": f"{tool} applied", "status": "success", "note": "no observable effect in this fixture"}

    def _heal(self) -> None:
        for change in self.remediation.get("heals", []):
            kind, name = change["kind"], change["name"]
            for obj in self.state.get(kind, []):
                if obj.get("name") == name:
                    obj.update(change.get("set", {}))

    def _target_names(self) -> set[str]:
        if self.remediation is None:
            return set()
        names = {h["name"] for h in self.remediation.get("heals", [])}
        names.add(self.remediation.get("args", {}).get("name", ""))
        return names - {""}

    def _note_verifying_read(self, tool: str, args: dict, result: dict) -> None:
        if not self._remediated or tool not in _VERIFYING_READS:
            return
        targets = self._target_names()
        named = args.get("name") or args.get("pod") or ""
        if named and any(named.startswith(t) or t.startswith(named) for t in targets):
            self._verified = True
        elif not named and any(t in json.dumps(result) for t in targets):
            self._verified = True

    def _execute_read(self, tool: str, args: dict) -> dict:
        handler = getattr(self, f"_tool_{tool}", None)
        result = handler(args) if handler else self._generic_read(tool, args)
        self._note_verifying_read(tool, args, result)
        return result

    def _generic_read(self, tool: str, args: dict) -> dict:
        """Reads without a dedicated handler serve the raw fixture section, so
        fixtures can model any canonical tool with a ``responses`` block."""
        canned = self.fixture.get("responses", {}).get(tool)
        if canned is not None:
            return copy.deepcopy(canned)
        return {"result": [], "note": f"'{tool}' returns no data in this fixture"}

    # ------------------------------------------------------- read handlers

    def _ns_filter(self, objects: list[dict], args: dict) -> list[dict]:
        ns = args.get("namespace")
        return [o for o in objects if ns is None or o.get("namespace") == ns]

    def _tool_list_pods(self, args: dict) -> dict:
        pods = self._ns_filter(self.state.get("pods", []), args)
        return {"pods": [{k: v for k, v in p.items() if k not in ("logs", "describe")} for p in pods]}

    def _find_pod(self, args: dict) -> dict | None:
        name = args.get("name") or args.get("pod")
        for p in self.state.get("pods", []):
            if p["name"] == name or (name and p["name"].startswith(name)):
                return p
        return None

    def _tool_describe_pod(self, args: dict) -> dict:
        pod = self._find_pod(args)
        if pod is None:
            return {"error": _FAILURE_PAYLOADS["not_found"] | {"message": f"pod '{args.get('name')}' not found"}}
        return {k: v for k, v in pod.items() if k != "logs"}

    def _tool_get_pod_logs(self, args: dict) -> dict:
        pod = self._find_pod(args)
        if pod is None:
            return {"error": _FAILURE_PAYLOADS["not_found"] | {"message": f"pod '{args.get('name')}' not found"}}
        return {"pod": pod["name"], "logs": pod.get("logs", "")}

    def _tool_get_events(self, args: dict) -> dict:
        return {"events": self._ns_filter(self.state.get("events", []), args)}

    def _tool_list_resources(self, args: dict) -> dict:
        kind = args.get("kind", "nodes")
        return {kind: copy.deepcopy(self.state.get(kind, []))}

    def _tool_get_node_metrics(self, args: dict) -> dict:
        fields = ("name", "status", "cpu_pct", "memory_pct", "allocatable_cpu", "allocatable_memory")
        return {"nodes": [{k: n.get(k) for k in fields} for n in self.state.get("nodes", [])]}

    def _tool_get_cluster_operators(self, args: dict) -> dict:
        return {"operators": copy.deepcopy(self.state.get("operators", []))}

    def _tool_get_pod_metrics(self, args: dict) -> dict:
        pods = self._ns_filter(self.state.get("pods", []), args)
        metrics = [{"name": p["name"], "cpu": p.get("cpu", "0m"), "memory": p.get("memory", "0Mi")} for p in pods]
        return {"metrics": metrics}
