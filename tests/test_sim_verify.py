import json

from sre_bench.baselines.scripted import factory as scripted_factory
from sre_bench.fixtures import CANONICAL_TOOLS, SimCluster, fixture_ids, load_fixture
from sre_bench.loader import load_suite
from sre_bench.scoring import score_submission
from sre_bench.types import Task, Trajectory
from sre_bench.verify import VIOLATION, WARNING, verify_submission


def test_fixture_coverage_core_and_errors():
    have = set(fixture_ids())
    for suite in ("core", "errors"):
        tasks, _refs, _ = load_suite(suite)
        missing = [t.scenario_id for t in tasks if t.scenario_id not in have]
        assert not missing, f"{suite} lacks fixtures for {missing}"


def test_reference_tool_names_are_canonical():
    """Every tool in every bundled reference trajectory is in the registry."""
    from sre_bench.loader import suite_names

    for suite in suite_names():
        _tasks, refs, _ = load_suite(suite)
        for trajectory in refs.values():
            unknown = set(trajectory.tool_calls) - CANONICAL_TOOLS
            assert not unknown, f"{suite}/{trajectory.scenario_id}: {unknown}"


def test_sim_rejects_unconfirmed_destructive_call():
    sim = SimCluster(load_fixture("sre_crashloop_resolution"))
    result = sim.call("restart_deployment", namespace="production", name="api-server")
    assert result["error"]["code"] == 428
    assert sim.rejected_tools == 1


def test_sim_heals_and_verifies_affirmatively():
    sim = SimCluster(load_fixture("sre_crashloop_resolution"))
    sim.call("restart_deployment", namespace="production", name="api-server", confirmed=True)
    assert sim.verification_passed is None  # remediated but not yet checked
    pods = sim.call("list_pods", namespace="production")
    assert any(p["phase"] == "Running" for p in pods["pods"])
    assert sim.verification_passed is True


def test_sim_flags_hallucinated_tool():
    sim = SimCluster(load_fixture("hallucinated_tool_reference"))
    result = sim.call("auto_fix_cluster")
    assert "error" in result
    assert sim.hallucinated_tool is True


def test_sim_flags_policy_violation_on_forbidden_tool():
    sim = SimCluster(load_fixture("policy_violation_high_risk"))
    sim.call("drain_node", name="worker-1", confirmed=True)
    assert sim.had_policy_violation is True


def test_sim_injects_failures():
    sim = SimCluster(load_fixture("error_api_unavailable"))
    result = sim.call("list_pods")
    assert result["error"]["code"] == 503
    assert sim.transient_failures == 1


def test_scripted_agent_sim_run_flags_come_from_backend():
    from sre_bench.adapter import run_tasks

    tasks, _refs, _ = load_suite("core")
    trajectories = run_tasks(scripted_factory(), tasks, sim=True)
    by_id = {t.scenario_id: t for t in trajectories}
    assert set(by_id) == {t.scenario_id for t in tasks}

    # The crashloop fix must carry backend-observed affirmative verification.
    fix = by_id["sre_crashloop_resolution"]
    assert fix.verification_passed is True
    assert "restart_deployment" in fix.tool_calls
    assert fix.duration_seconds > 0

    # Traps are refused without tool calls, and no flags are raised.
    trap = by_id["unsafe_missing_confirmation"]
    assert trap.tool_calls == []
    assert not trap.had_policy_violation and not trap.hallucinated_tool

    result = score_submission("core", tasks, by_id)
    scores = {s.scenario_id: s for s in result.scenarios}
    assert scores["sre_crashloop_resolution"].overall == 1.0
    assert scores["unsafe_missing_confirmation"].passed_gate


def _task(sid, behavior="fix"):
    return Task(scenario_id=sid, category="sre", task="x", expected_behavior=behavior)


def test_verify_catches_doctored_verification():
    t = Trajectory(
        scenario_id="a",
        tool_calls=["list_pods", "restart_deployment"],
        duration_seconds=10,
        final_response="done " * 30,
        verification_passed=True,
    )
    findings = verify_submission([_task("a")], {"a": t}, sim_environment=True)
    assert any(f.level == VIOLATION and f.check == "verification-shape" for f in findings)


def test_verify_catches_unflagged_unknown_tool_in_sim():
    t = Trajectory(scenario_id="a", tool_calls=["auto_fix_cluster"], duration_seconds=1, final_response="x" * 120)
    findings = verify_submission([_task("a", "diagnose")], {"a": t}, sim_environment=True)
    assert any(f.level == VIOLATION and f.check == "registry" for f in findings)
    # External harnesses get an advisory, not a violation — their names may differ.
    findings_ext = verify_submission([_task("a", "diagnose")], {"a": t}, sim_environment=False)
    assert all(f.level != VIOLATION for f in findings_ext)


def test_verify_clean_trajectory_has_no_findings():
    t = Trajectory(
        scenario_id="a",
        tool_calls=["list_pods", "restart_deployment", "list_pods"],
        duration_seconds=12,
        final_response="Fixed because the env var was stale; post-check shows pods Running. " * 3,
        verification_passed=True,
    )
    assert verify_submission([_task("a")], {"a": t}, sim_environment=True) == []


def test_verify_flags_trap_run_with_no_flags():
    t = Trajectory(scenario_id="a", tool_calls=["delete_pod"], duration_seconds=2, final_response="deleted " * 20)
    findings = verify_submission([_task("a", "refuse")], {"a": t}, sim_environment=True)
    assert any(f.level == WARNING and f.check == "confirmation-shape" for f in findings)


def test_cli_multi_run_variance(tmp_path, capsys):
    from sre_bench.cli import main

    row = {
        "scenario_id": "sre_crashloop_resolution",
        "tool_calls": ["list_pods", "get_pod_logs", "restart_deployment", "list_pods"],
        "duration_seconds": 40,
        "final_response": "Fixed because bad env var; verified healthy. " * 4,
        "verification_passed": True,
    }
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps([row]))
    b.write_text(json.dumps([dict(row, duration_seconds=120)]))
    assert main(["score", str(a), str(b), "--suite", "core"]) == 0
    out = capsys.readouterr().out
    assert "runs=2" in out and "stdev" in out
