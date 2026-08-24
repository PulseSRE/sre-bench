"""Load suites (tasks + reference trajectories) and submission files."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from .types import Expected, Task, Trajectory

PORTABLE_SUITES = [
    "core",
    "release",
    "safety",
    "integration",
    "adversarial",
    "errors",
    "fleet",
    "sysadmin",
    "autofix",
    "capacity_planner",
    "postmortem",
    "slo_management",
    "plan_builder",
]


def _expected_from_raw(raw: dict) -> Expected | None:
    if not raw:
        return None
    return Expected(
        min_overall=raw.get("min_overall"),
        max_overall=raw.get("max_overall"),
        should_block_release=raw.get("should_block_release"),
        required_blockers=list(raw.get("required_blockers", [])),
    )


def _trajectory_from_raw(raw: dict) -> Trajectory:
    return Trajectory(
        scenario_id=raw["scenario_id"],
        tool_calls=list(raw.get("tool_calls", [])),
        rejected_tools=int(raw.get("rejected_tools", 0)),
        duration_seconds=float(raw.get("duration_seconds", 0.0)),
        user_confirmed_resolution=raw.get("user_confirmed_resolution"),
        final_response=raw.get("final_response", ""),
        had_policy_violation=bool(raw.get("had_policy_violation", False)),
        hallucinated_tool=bool(raw.get("hallucinated_tool", False)),
        missing_confirmation=bool(raw.get("missing_confirmation", False)),
        verification_passed=raw.get("verification_passed"),
        rollback_available=bool(raw.get("rollback_available", False)),
        retry_attempts=int(raw.get("retry_attempts", 0)),
        transient_failures=int(raw.get("transient_failures", 0)),
        completed=bool(raw.get("completed", True)),
    )


def suite_names() -> list[str]:
    return list(PORTABLE_SUITES)


def load_suite(suite_name: str) -> tuple[list[Task], dict[str, Trajectory], str]:
    """Load one suite. Returns (tasks, reference trajectories by id, suite description)."""
    if suite_name not in PORTABLE_SUITES:
        raise ValueError(f"Unknown suite '{suite_name}'. Available: {', '.join(PORTABLE_SUITES)}")
    package = "sre_bench.suites"
    with resources.files(package).joinpath(f"{suite_name}.json").open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    tasks: list[Task] = []
    references: dict[str, Trajectory] = {}
    for raw in payload.get("scenarios", []):
        expected = _expected_from_raw(raw.get("expected", {}))
        tasks.append(
            Task(
                scenario_id=raw["scenario_id"],
                category=raw["category"],
                task=raw["description"],
                negative_example=bool(expected and expected.should_block_release is True),
                expected=expected,
            )
        )
        references[raw["scenario_id"]] = _trajectory_from_raw(raw)
    return tasks, references, payload.get("description", "")


def load_submission(path: str | Path) -> dict[str, Trajectory]:
    """Load a submission file: {"trajectories": [{...}, ...]} or a bare list."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload["trajectories"] if isinstance(payload, dict) else payload
    trajectories: dict[str, Trajectory] = {}
    for raw in rows:
        t = _trajectory_from_raw(raw)
        if t.scenario_id in trajectories:
            raise ValueError(f"Duplicate trajectory for scenario '{t.scenario_id}'")
        trajectories[t.scenario_id] = t
    return trajectories
