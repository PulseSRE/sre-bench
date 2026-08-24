"""Core types: tasks, trajectories, and scores.

A *task* is what the agent under test is asked to do. A *trajectory* is the
record of what an agent actually did — tool calls, timing, safety flags, and
its final response. Scoring consumes (task, trajectory) pairs; it never runs
the agent itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Expected:
    """Reference-calibration metadata attached to a task.

    These assertions describe how the *reference trajectory* must score (used
    to verify the rubric catches known-bad behavior). They are not applied to
    agent submissions — a submission is scored by the pure rubric.
    """

    min_overall: float | None = None
    max_overall: float | None = None
    should_block_release: bool | None = None
    required_blockers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Task:
    """One benchmark task: the prompt an agent under test must attempt."""

    scenario_id: str
    category: str
    task: str  # the prompt / situation description
    negative_example: bool = False  # reference trajectory is a known-bad run
    expected: Expected | None = None


@dataclass
class Trajectory:
    """One agent run on one task, as recorded by the submitting harness.

    Flag fields (had_policy_violation, hallucinated_tool, missing_confirmation,
    verification_passed) must be set by the harness that observed the run,
    never self-reported by the model under test. See docs/RUNNING.md.
    """

    scenario_id: str
    tool_calls: list[str] = field(default_factory=list)
    rejected_tools: int = 0
    duration_seconds: float = 0.0
    user_confirmed_resolution: bool | None = None
    final_response: str = ""
    had_policy_violation: bool = False
    hallucinated_tool: bool = False
    missing_confirmation: bool = False
    verification_passed: bool | None = None
    rollback_available: bool = False
    retry_attempts: int = 0
    transient_failures: int = 0
    completed: bool = True


@dataclass
class ScenarioScore:
    scenario_id: str
    category: str
    overall: float
    dimensions: dict[str, float]
    blockers: list[str]
    passed_gate: bool
    judge: dict | None = None  # optional LLM-judge result (0-100 scale)


@dataclass
class SuiteResult:
    suite_name: str
    scenario_count: int
    passed_count: int
    gate_passed: bool
    average_overall: float
    dimension_averages: dict[str, float]
    blocker_counts: dict[str, int]
    scenarios: list[ScenarioScore]
    missing_scenarios: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "suite_name": self.suite_name,
            "scenario_count": self.scenario_count,
            "passed_count": self.passed_count,
            "gate_passed": self.gate_passed,
            "average_overall": self.average_overall,
            "dimension_averages": self.dimension_averages,
            "blocker_counts": self.blocker_counts,
            "missing_scenarios": self.missing_scenarios,
            "scenarios": [
                {
                    "scenario_id": s.scenario_id,
                    "category": s.category,
                    "overall": s.overall,
                    "dimensions": s.dimensions,
                    "blockers": s.blockers,
                    "passed_gate": s.passed_gate,
                    **({"judge": s.judge} if s.judge is not None else {}),
                }
                for s in self.scenarios
            ],
        }
