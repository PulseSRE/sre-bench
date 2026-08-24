"""Adapter protocol for running an agent under test directly.

Implement ``Agent`` and point the CLI at a factory::

    # myagent/bench.py
    from sre_bench.adapter import Agent
    from sre_bench.types import Task, Trajectory

    class MyAgent:
        def run(self, task: Task) -> Trajectory:
            ...  # drive your agent, record what it did

    def factory() -> Agent:
        return MyAgent()

    $ sre-bench run --adapter myagent.bench:factory --suite core --out my.json

The harness (your ``run`` implementation) is responsible for setting the flag
fields honestly: ``hallucinated_tool`` when the agent called a tool that does
not exist, ``missing_confirmation`` when a destructive action ran without a
confirmation step, ``had_policy_violation`` when it broke your policy layer,
and ``verification_passed`` only when an affirmative post-check confirmed the
outcome — never from the agent's own claim of success.
"""

from __future__ import annotations

import importlib
import time
from typing import Protocol, runtime_checkable

from .types import Task, Trajectory


@runtime_checkable
class Agent(Protocol):
    def run(self, task: Task) -> Trajectory:  # pragma: no cover - protocol
        ...


def load_adapter(spec: str) -> Agent:
    """Load an adapter from a 'module.path:factory' spec."""
    module_name, _, factory_name = spec.partition(":")
    if not module_name or not factory_name:
        raise ValueError(f"Adapter spec must be 'module.path:factory', got '{spec}'")
    module = importlib.import_module(module_name)
    factory = getattr(module, factory_name)
    agent = factory()
    if not isinstance(agent, Agent):
        raise TypeError(f"{spec} did not return an object with a run(task) method")
    return agent


def run_tasks(agent: Agent, tasks: list[Task]) -> list[Trajectory]:
    """Run every task through the adapter, timing runs the adapter didn't time."""
    out: list[Trajectory] = []
    for task in tasks:
        start = time.monotonic()
        trajectory = agent.run(task)
        if trajectory.duration_seconds <= 0.0:
            trajectory.duration_seconds = round(time.monotonic() - start, 3)
        if trajectory.scenario_id != task.scenario_id:
            raise ValueError(
                f"Adapter returned trajectory for '{trajectory.scenario_id}' "
                f"while running '{task.scenario_id}'"
            )
        out.append(trajectory)
    return out
