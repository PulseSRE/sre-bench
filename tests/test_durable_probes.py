"""The durable probes' failure behaviour, pinned against fake drivers.

Each probe's job is to refuse a pass without affirmative evidence. So the
tests here are mostly tripwires: a driver that reproduces a known-bad shape
(COMPLETED with no result, an unlabeled listing, a denial that leaves no
record) must turn the corresponding check red. A probe suite whose checks
cannot fail would be the same absence-as-evidence bug sre-bench exists to
catch — this file is where that property is enforced on the probes
themselves.
"""

from __future__ import annotations

from unittest.mock import patch

from sre_bench.durable import (
    PROBE_PLAN_TYPE,
    check_cancel_stops_a_waiting_run,
    check_denied_approval_escalates,
    check_run_completes_with_outputs,
    check_runs_are_listed_with_memo,
    run_probes,
)


class FakeDriver:
    """A healthy agent: approval gates wait, denials record, memo labels runs."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.counter = 0
        self.plan_created = False
        self.plan_deleted = False

    # -- surface ---------------------------------------------------------
    def create_probe_plan(self):
        self.plan_created = True

    def delete_probe_plan(self):
        self.plan_deleted = True

    def start_plan_run(self, incident_type):
        self.counter += 1
        wid = f"plan-{incident_type}-{self.counter}"
        self.runs[wid] = {"status": "RUNNING", "incident_type": incident_type}
        return {"workflow_id": wid}

    def describe_run(self, wid):
        run = self.runs[wid]
        out = {"workflow_id": wid, "status": run["status"]}
        if run["status"] == "COMPLETED":
            out["result"] = run["result"]
        return out

    def list_runs(self, limit=50):
        return [
            {
                "workflow_id": wid,
                "status": run["status"],
                "memo": {"kind": "plan", "incident_type": run["incident_type"]},
            }
            for wid, run in self.runs.items()
        ]

    def approve(self, wid, phase_id, approved):
        if not approved:
            self.runs[wid]["status"] = "COMPLETED"
            self.runs[wid]["result"] = {
                "status": "partial",
                "phase_outputs": {
                    phase_id: {
                        "status": "needs_escalation",
                        "evidence_summary": f"Phase '{phase_id}' was denied by a human",
                    }
                },
            }

    def cancel(self, wid, reason):
        self.runs[wid]["status"] = "CANCELED"


def _fast(fn, driver):
    """Run a check without its real-time sleeps."""
    with patch("sre_bench.durable.time.sleep"):
        return fn(driver)


class TestHealthyAgentPasses:
    def test_all_rest_checks_pass(self):
        driver = FakeDriver()
        with patch("sre_bench.durable.time.sleep"):
            report = run_probes(driver, include_in_pod=False)
        assert report.passed, [c.evidence for c in report.checks if not c.passed]
        assert driver.plan_created and driver.plan_deleted, "the probe plan must be cleaned up"
        assert len(report.checks) == 4


class TestTripwires:
    def test_completed_without_a_result_fails(self):
        """The vanishing-verdict shape: terminal status, nothing recorded."""

        class Amnesiac(FakeDriver):
            def approve(self, wid, phase_id, approved):
                self.runs[wid]["status"] = "COMPLETED"
                self.runs[wid]["result"] = None

            def describe_run(self, wid):
                return {"workflow_id": wid, "status": self.runs[wid]["status"], "result": None}

        result = _fast(check_run_completes_with_outputs, Amnesiac())
        assert not result.passed
        assert "verdict from nothing" in result.evidence

    def test_denial_that_leaves_no_escalation_fails(self):
        class Swallows(FakeDriver):
            def approve(self, wid, phase_id, approved):
                self.runs[wid]["status"] = "COMPLETED"
                self.runs[wid]["result"] = {"status": "complete", "phase_outputs": {}}

        result = _fast(check_denied_approval_escalates, Swallows())
        assert not result.passed
        assert "needs_escalation" in result.evidence

    def test_escalation_without_the_denial_reason_fails(self):
        """Recording *that* it escalated is not recording *why*."""

        class Vague(FakeDriver):
            def approve(self, wid, phase_id, approved):
                self.runs[wid]["status"] = "COMPLETED"
                self.runs[wid]["result"] = {
                    "status": "partial",
                    "phase_outputs": {phase_id: {"status": "needs_escalation", "evidence_summary": "skipped"}},
                }

        result = _fast(check_denied_approval_escalates, Vague())
        assert not result.passed

    def test_unlabeled_listing_fails(self):
        class Opaque(FakeDriver):
            def list_runs(self, limit=50):
                return [{"workflow_id": wid, "status": r["status"], "memo": {}} for wid, r in self.runs.items()]

        result = _fast(check_runs_are_listed_with_memo, Opaque())
        assert not result.passed
        assert "unlabeled" in result.evidence

    def test_missing_from_the_listing_fails(self):
        class Forgetful(FakeDriver):
            def list_runs(self, limit=50):
                return []

        result = _fast(check_runs_are_listed_with_memo, Forgetful())
        assert not result.passed

    def test_cancel_that_does_not_stop_the_run_fails(self):
        class Ignores(FakeDriver):
            def cancel(self, wid, reason):
                pass  # run stays RUNNING

        with patch("sre_bench.durable.time.sleep"), patch("sre_bench.durable._await_terminal") as awaits:
            awaits.return_value = {"status": "RUNNING"}
            result = check_cancel_stops_a_waiting_run(Ignores())
        assert not result.passed

    def test_in_pod_probe_fails_rather_than_skips_when_unavailable(self):
        """Outside the agent pod the tripwire must report FAIL, not silently
        pass — a skipped regression check is the regression check that never
        existed."""
        from sre_bench.durable import check_failed_apply_records_a_verdict

        result = check_failed_apply_records_a_verdict()
        assert not result.passed
        assert "skipped is not passed" in result.evidence


class TestProbePlanShape:
    def test_probe_plan_needs_no_llm_on_the_deny_path(self):
        """The gated phase is the only phase, and denial means it never runs —
        the whole probe suite must be cheap enough to run on every release."""
        from sre_bench.durable import _PROBE_PLAN

        assert PROBE_PLAN_TYPE == _PROBE_PLAN["incident_type"]
        assert len(_PROBE_PLAN["phases"]) == 1
        assert _PROBE_PLAN["phases"][0]["approval_required"] is True
        assert _PROBE_PLAN["phases"][0]["required"] is False
