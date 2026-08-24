import json

import pytest

from sre_bench.cli import main
from sre_bench.loader import load_submission, load_suite, suite_names


def test_all_suites_load():
    total = 0
    for name in suite_names():
        tasks, refs, description = load_suite(name)
        assert tasks, name
        assert description
        assert set(refs) == {t.scenario_id for t in tasks}
        total += len(tasks)
    assert total == 121


def test_unknown_suite_rejected():
    with pytest.raises(ValueError):
        load_suite("nope")


def test_submission_roundtrip(tmp_path):
    sub = tmp_path / "sub.json"
    sub.write_text(
        json.dumps(
            {
                "trajectories": [
                    {
                        "scenario_id": "sre_crashloop_resolution",
                        "tool_calls": ["list_pods", "get_pod_logs", "describe_pod"],
                        "duration_seconds": 40,
                        "final_response": "Crashloop was caused by a bad env var; restart verified healthy." * 3,
                        "verification_passed": True,
                    }
                ]
            }
        )
    )
    trajectories = load_submission(sub)
    assert "sre_crashloop_resolution" in trajectories


def test_duplicate_trajectory_rejected(tmp_path):
    sub = tmp_path / "dup.json"
    row = {"scenario_id": "x", "final_response": "y"}
    sub.write_text(json.dumps([row, row]))
    with pytest.raises(ValueError):
        load_submission(sub)


def test_cli_list_and_tasks(tmp_path, capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "121 tasks" in out

    task_file = tmp_path / "tasks.json"
    assert main(["tasks", "--suite", "core", "--out", str(task_file)]) == 0
    payload = json.loads(task_file.read_text())
    assert payload["suite_name"] == "core"
    assert all("task" in t and "tool_calls" not in t for t in payload["tasks"])


def test_cli_score_partial_submission(tmp_path, capsys):
    sub = tmp_path / "sub.json"
    sub.write_text(
        json.dumps(
            [
                {
                    "scenario_id": "sre_crashloop_resolution",
                    "tool_calls": ["list_pods", "get_pod_logs", "describe_pod"],
                    "duration_seconds": 40,
                    "final_response": "Crashloop caused by bad env var; fix applied and verified healthy again." * 2,
                    "verification_passed": True,
                }
            ]
        )
    )
    assert main(["score", str(sub), "--suite", "core"]) == 0
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert main(["score", str(sub), "--suite", "core", "--fail-on-gate"]) == 1


def test_cli_reference_scores(capsys):
    assert main(["reference-scores", "--suite", "release"]) == 0
    out = capsys.readouterr().out
    assert "gate=PASS" in out
