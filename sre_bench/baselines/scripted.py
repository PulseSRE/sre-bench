"""A deterministic scripted agent for the simulated backend.

This is plumbing verification and an adapter example, not a contender: it
follows fixed rules (gather state, act on obvious signals, verify, refuse
destructive asks without approval). Run it with::

    sre-bench run --adapter sre_bench.baselines.scripted:factory --suite core --sim --out scripted.json --score
"""

from __future__ import annotations

import json

from ..fixtures.sim import SimCluster
from ..types import Task, Trajectory

_DESTRUCTIVE_ASKS = ("delete", "drain", "skip the change process", "don't ask", "no questions", "auto_fix")


class ScriptedAgent:
    def run(self, task: Task, backend: SimCluster | None = None) -> Trajectory:
        if backend is None:
            return Trajectory(scenario_id=task.scenario_id, completed=False, final_response="sim backend required")

        text = task.task.lower()
        if any(marker in text for marker in _DESTRUCTIVE_ASKS):
            return Trajectory(
                scenario_id=task.scenario_id,
                final_response=(
                    "I cannot perform a destructive or unverifiable action on request alone because it "
                    "requires explicit approval through the confirmation flow. Describe the underlying "
                    "problem and I will diagnose it and propose a safe, reviewable remediation."
                ),
            )

        pods = backend.call("list_pods")
        events = backend.call("get_events")
        notes: list[str] = []
        fixed = False

        if "error" in pods:
            notes.append(f"list_pods failed: {pods['error']['message']}")
        else:
            for pod in pods.get("pods", []):
                phase = pod.get("phase")
                if phase == "CrashLoopBackOff":
                    logs = backend.call("get_pod_logs", name=pod["name"])
                    deployment = pod["name"].rsplit("-", 2)[0]
                    result = backend.call(
                        "restart_deployment", namespace=pod["namespace"], name=deployment, confirmed=True
                    )
                    check = backend.call("list_pods", namespace=pod["namespace"])
                    healthy = any(
                        p.get("phase") == "Running" for p in check.get("pods", []) if p["name"] == pod["name"]
                    )
                    fixed = result.get("status") == "success" and healthy
                    notes.append(
                        f"{pod['name']} was crash-looping because its logs show: "
                        f"{logs.get('logs', '')[:120].strip()} — restarted {deployment} and "
                        + ("verified pods Running." if healthy else "the pod is still unhealthy.")
                    )
                elif phase == "Pending":
                    detail = backend.call("describe_pod", name=pod["name"])
                    nodes = backend.call("get_node_metrics")
                    conditions = json.dumps(detail.get("conditions", ""))
                    notes.append(
                        f"{pod['name']} is Pending because the scheduler reports: {conditions[:160]}. "
                        f"Node capacity: {json.dumps(nodes)[:160]}."
                    )

        if not notes:
            ev = json.dumps(events.get("events", events.get("error", "")))[:160]
            notes.append(f"No unhealthy workloads found because current state and events are quiet: {ev}")

        response = " ".join(notes)
        return Trajectory(
            scenario_id=task.scenario_id,
            final_response=response,
            user_confirmed_resolution=True if fixed else None,
        )


def factory() -> ScriptedAgent:
    return ScriptedAgent()
