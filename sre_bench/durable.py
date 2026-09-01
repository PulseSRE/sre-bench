"""Live probes for Pulse's durable workflow execution.

The rest of sre-bench scores *trajectories* — records of what an agent said
and called. Durable execution cannot be scored that way: its claims are about
what survives (a run outliving the pod, an approval gate that really waits, a
cancel that undoes an applied fix, a failure that still records a verdict),
and the only honest evidence is observing a live system do it. These probes
drive a real agent's durable endpoints and hold every check to the same
standard as the rubric: **a pass requires affirmative evidence** — a verdict
with content, an escalation with a reason, a memo with a label. A run that
merely *finishes* proves nothing; the vanishing-verdict bug (pulse-agent
v2.29.1) finished too.

Probes talk to the agent through a small driver seam so unit tests can pin
each check's failure behaviour without a cluster:

- :class:`RestDriver` uses the public REST surface (plan runs, listing,
  approve, cancel) — everything a UI user can reach.
- The optional apply-failure probe additionally needs the in-pod Temporal
  client (``sre_agent`` importable, as it is when sre-bench is staged into
  the agent pod), because incident runs are dispatched by the monitor, not
  by REST.

Run with::

    sre-bench durable --base-url http://localhost:8000 --token $TOKEN
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

#: The plan the probes create, exercise, and delete. Everything about it is
#: chosen so the deny path needs no LLM: the phase gates on approval and the
#: probe denies, so the workflow records the escalation without ever running
#: the skill.
PROBE_PLAN_TYPE = "srebench-durable-probe"

_PROBE_PLAN = {
    "incident_type": PROBE_PLAN_TYPE,
    "name": "SRE-Bench durable probe",
    "max_total_duration": 600,
    "phases": [
        {
            "id": "gated",
            "skill_name": "sre",
            "timeout_seconds": 60,
            "produces": [],
            "required": False,
            "approval_required": True,
        }
    ],
}


@dataclass
class CheckResult:
    check: str
    passed: bool
    evidence: str


@dataclass
class DurableReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks) and bool(self.checks)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [{"check": c.check, "passed": c.passed, "evidence": c.evidence} for c in self.checks],
        }


class RestDriver:
    """The agent's public durable surface, as a UI user reaches it."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from e

    def create_probe_plan(self) -> None:
        self._request("POST", "/plan-templates", _PROBE_PLAN)

    def delete_probe_plan(self) -> None:
        try:
            self._request("DELETE", f"/plan-templates/{PROBE_PLAN_TYPE}")
        except RuntimeError:
            pass  # cleanup is best-effort; a leftover probe plan is inert

    def start_plan_run(self, incident_type: str) -> dict:
        return self._request("POST", f"/plan-templates/{incident_type}/run", {"incident": {}})

    def describe_run(self, workflow_id: str) -> dict:
        return self._request("GET", f"/workflow-runs/{workflow_id}")

    def list_runs(self, limit: int = 50) -> list[dict]:
        return self._request("GET", f"/workflow-runs?limit={limit}").get("runs", [])

    def approve(self, workflow_id: str, phase_id: str, approved: bool) -> None:
        self._request(
            "POST",
            f"/workflow-runs/{workflow_id}/approve",
            {"phase_id": phase_id, "approved": approved},
        )

    def cancel(self, workflow_id: str, reason: str) -> None:
        self._request("POST", f"/workflow-runs/{workflow_id}/cancel", {"reason": reason})


def _await_terminal(driver, workflow_id: str, timeout: float = 120.0, poll: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    desc: dict = {}
    while time.monotonic() < deadline:
        desc = driver.describe_run(workflow_id)
        if desc.get("status") not in ("RUNNING", None, ""):
            return desc
        time.sleep(poll)
    return desc


def check_denied_approval_escalates(driver) -> CheckResult:
    """An approval gate that really waits, and a denial that leaves a record.

    The in-process engine could never wait, so this is the migration's core
    claim. The pass condition is the *content* of the escalation — a phase
    output with status ``needs_escalation`` and a reason naming the denial —
    not merely the run ending. A workflow that swallowed the denial and
    completed empty-handed must fail here.
    """
    started = driver.start_plan_run(PROBE_PLAN_TYPE)
    wid = started.get("workflow_id", "")
    if not wid:
        return CheckResult("denied_approval_escalates", False, f"run did not start: {started}")

    # Give the workflow a moment to reach the gate, then deny.
    time.sleep(2)
    driver.approve(wid, "gated", approved=False)
    desc = _await_terminal(driver, wid)

    result = desc.get("result") or {}
    output = (result.get("phase_outputs") or {}).get("gated") or {}
    if output.get("status") != "needs_escalation":
        return CheckResult(
            "denied_approval_escalates",
            False,
            f"denied phase recorded {output.get('status')!r}, wanted needs_escalation "
            f"(run status {desc.get('status')})",
        )
    evidence = output.get("evidence_summary", "")
    if "denied" not in evidence:
        return CheckResult(
            "denied_approval_escalates",
            False,
            f"escalation does not say a human denied it: {evidence!r}",
        )
    return CheckResult("denied_approval_escalates", True, f"escalated with reason: {evidence}")


def check_run_completes_with_outputs(driver) -> CheckResult:
    """A terminal run must carry its outputs — COMPLETED alone proves nothing.

    This is the vanishing-verdict tripwire, generalized: the failure mode this
    bench exists to catch is a workflow that ends leaving no record of what it
    decided. The denial run from the previous check is fine as a subject; any
    terminal plan run must expose per-phase outputs through the describe
    endpoint.
    """
    started = driver.start_plan_run(PROBE_PLAN_TYPE)
    wid = started.get("workflow_id", "")
    time.sleep(2)
    driver.approve(wid, "gated", approved=False)
    desc = _await_terminal(driver, wid)

    if desc.get("status") != "COMPLETED":
        return CheckResult(
            "run_completes_with_outputs", False, f"run ended {desc.get('status')!r}, not COMPLETED"
        )
    result = desc.get("result")
    if not result:
        return CheckResult(
            "run_completes_with_outputs",
            False,
            "COMPLETED with no result payload — a verdict from nothing",
        )
    outputs = result.get("phase_outputs") or {}
    if not outputs:
        return CheckResult(
            "run_completes_with_outputs", False, "COMPLETED with an empty phase_outputs record"
        )
    return CheckResult(
        "run_completes_with_outputs",
        True,
        f"status {result.get('status')!r} with {len(outputs)} phase output(s) recorded",
    )


def check_runs_are_listed_with_memo(driver) -> CheckResult:
    """The run listing must label runs, not enumerate opaque ids."""
    started = driver.start_plan_run(PROBE_PLAN_TYPE)
    wid = started.get("workflow_id", "")
    time.sleep(2)
    try:
        rows = [r for r in driver.list_runs() if r.get("workflow_id") == wid]
        if not rows:
            return CheckResult("runs_listed_with_memo", False, f"run {wid} missing from the listing")
        memo = rows[0].get("memo") or {}
        if memo.get("kind") != "plan" or memo.get("incident_type") != PROBE_PLAN_TYPE:
            return CheckResult(
                "runs_listed_with_memo",
                False,
                f"listed but unlabeled: memo={memo!r} — a list of opaque ids is not a listing",
            )
        return CheckResult("runs_listed_with_memo", True, f"labeled: {memo}")
    finally:
        # Do not leave the gate waiting a day for an approval nobody will send.
        try:
            driver.cancel(wid, "sre-bench durable probe cleanup")
        except RuntimeError:
            pass


def check_cancel_stops_a_waiting_run(driver) -> CheckResult:
    """Cancel must actually terminate a run parked on its approval gate."""
    started = driver.start_plan_run(PROBE_PLAN_TYPE)
    wid = started.get("workflow_id", "")
    time.sleep(2)
    driver.cancel(wid, "sre-bench durable probe")
    desc = _await_terminal(driver, wid, timeout=60)
    status = desc.get("status")
    if status in ("RUNNING", None, ""):
        return CheckResult("cancel_stops_waiting_run", False, f"still {status!r} after cancel")
    return CheckResult("cancel_stops_waiting_run", True, f"terminal status {status!r} after cancel")


def check_failed_apply_records_a_verdict() -> CheckResult:
    """The v2.29.1 regression, as a permanent tripwire.

    An incident run whose apply fails (the pod does not exist) must end
    COMPLETED with verdict ``failed`` and the cause as evidence — not die as
    WorkflowExecutionFailed with nothing recorded. Needs the in-pod Temporal
    client; reports its own absence honestly rather than passing on it.
    """
    try:
        import asyncio

        from sre_agent.temporal.client import _connect, start_incident_run  # type: ignore[import-not-found]
    except ImportError:
        return CheckResult(
            "failed_apply_records_verdict",
            False,
            "sre_agent not importable — run this probe inside the agent pod (skipped is not passed)",
        )

    async def go() -> CheckResult:
        run_id = f"srebench-applyfail-{int(time.time())}"
        await start_incident_run(
            run_id,
            {"kind": "Pod", "name": "srebench-nonexistent-pod", "namespace": "default"},
            {
                "strategy": "restart_controller",
                "params": {
                    "resources": [
                        {"kind": "Pod", "name": "srebench-nonexistent-pod", "namespace": "default"}
                    ]
                },
            },
            require_approval=False,
            recurrence_window_seconds=60,
        )
        client = await _connect()
        handle = client.get_workflow_handle(f"incident-{run_id}")
        result = await asyncio.wait_for(handle.result(), timeout=180)
        if result.get("verdict") != "failed":
            return CheckResult(
                "failed_apply_records_verdict",
                False,
                f"verdict {result.get('verdict')!r}, wanted 'failed'",
            )
        if not result.get("evidence"):
            return CheckResult(
                "failed_apply_records_verdict", False, "failed verdict with no evidence of why"
            )
        return CheckResult(
            "failed_apply_records_verdict",
            True,
            f"failed with cause recorded: {str(result['evidence'])[:120]}",
        )

    try:
        return asyncio.run(go())
    except Exception as e:  # noqa: BLE001 - any escape here IS the regression
        return CheckResult(
            "failed_apply_records_verdict",
            False,
            f"workflow died without a verdict ({type(e).__name__}: {str(e)[:150]}) — the v2.29.1 bug shape",
        )


def run_probes(driver, include_in_pod: bool = True) -> DurableReport:
    """All probes against one agent. The probe plan is created and removed here."""
    report = DurableReport()
    driver.create_probe_plan()
    try:
        report.checks.append(check_denied_approval_escalates(driver))
        report.checks.append(check_run_completes_with_outputs(driver))
        report.checks.append(check_runs_are_listed_with_memo(driver))
        report.checks.append(check_cancel_stops_a_waiting_run(driver))
    finally:
        driver.delete_probe_plan()
    if include_in_pod:
        report.checks.append(check_failed_apply_records_a_verdict())
    return report
