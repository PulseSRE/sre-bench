from sre_bench.loader import load_suite, suite_names
from sre_bench.scoring import score_reference, score_submission
from sre_bench.types import Task, Trajectory


def _task(scenario_id="t1", **kw):
    return Task(scenario_id=scenario_id, category="sre", task="Diagnose the thing.", **kw)


def _traj(scenario_id="t1", **kw):
    defaults = dict(
        tool_calls=["a", "b", "c"],
        duration_seconds=45.0,
        final_response="The pods failed because of a bad env var; root cause identified and fix verified " * 2,
        verification_passed=True,
    )
    defaults.update(kw)
    return Trajectory(scenario_id=scenario_id, **defaults)


def test_good_trajectory_passes_gate():
    result = score_submission("x", [_task()], {"t1": _traj()})
    assert result.gate_passed
    assert result.scenarios[0].overall >= 0.75


def test_hard_blocker_fails_gate():
    result = score_submission("x", [_task()], {"t1": _traj(hallucinated_tool=True)})
    assert not result.gate_passed
    assert "hallucinated_tool" in result.scenarios[0].blockers


def test_missing_scenario_fails_suite_gate():
    result = score_submission("x", [_task("t1"), _task("t2")], {"t1": _traj()})
    assert not result.gate_passed
    assert result.missing_scenarios == ["t2"]
    assert result.scenario_count == 1  # only attempted scenarios are scored


def test_submission_ignores_expected_inversion():
    """A good run on a negative-example task passes for a submission."""
    from sre_bench.types import Expected

    task = _task(negative_example=True, expected=Expected(should_block_release=True))
    assert score_submission("x", [task], {"t1": _traj()}).gate_passed
    # ...but reference scoring inverts: a good reference on a should-block task fails.
    assert not score_reference("x", [task], {"t1": _traj()}).gate_passed


def test_reference_required_blocker_detected():
    """A promised blocker that IS produced passes reference scoring."""
    from sre_bench.types import Expected

    task = _task(expected=Expected(should_block_release=True, required_blockers=["policy_violation"]))
    result = score_reference("x", [task], {"t1": _traj(had_policy_violation=True)})
    assert result.gate_passed


def test_reference_required_blocker_missing():
    """A promised blocker that is NOT produced fails reference scoring, even
    when the should-block inversion would otherwise pass the scenario."""
    from sre_bench.types import Expected

    task = _task(expected=Expected(should_block_release=True, required_blockers=["hallucinated_tool"]))
    result = score_reference("x", [task], {"t1": _traj(had_policy_violation=True)})
    assert not result.gate_passed


def test_submission_ignores_required_blockers():
    """Submissions are scored on the pure rubric; required_blockers describes
    the reference trajectory, not the agent under test."""
    from sre_bench.types import Expected

    task = _task(expected=Expected(should_block_release=False, required_blockers=["policy_violation"]))
    assert score_submission("x", [task], {"t1": _traj()}).gate_passed


def test_reference_scores_are_reproducible():
    """Scoring the bundled references reproduces the pinned published results."""
    import json
    from pathlib import Path

    pinned_path = Path(__file__).parent.parent / "results" / "reference-scores.json"
    pinned = {r["suite_name"]: r for r in json.loads(pinned_path.read_text())["results"]}
    assert set(pinned) == set(suite_names())
    for name in suite_names():
        tasks, refs, _ = load_suite(name)
        result = score_reference(name, tasks, refs)
        expect = pinned[name]
        assert result.scenario_count == len(tasks) == expect["scenario_count"], name
        assert result.average_overall == expect["average_overall"], name
        assert result.passed_count == expect["passed_count"], name
        assert result.gate_passed == expect["gate_passed"], name
