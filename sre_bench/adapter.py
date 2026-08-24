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

**Sim mode** (``sre-bench run --sim``) removes that trust entirely: each task
with a bundled fixture is run against a :class:`~sre_bench.fixtures.SimCluster`
and the flag fields are overwritten with what the backend *observed* —
including ``verification_passed``, which flips True only when the fixture's
remediation ran and a later read returned the healed state. A sim-mode agent's
``run`` may accept a second argument to receive the backend::

    class MyAgent:
        def run(self, task: Task, backend=None) -> Trajectory:
            result = backend.call("list_pods", namespace="production")
            ...
"""

from __future__ import annotations

import importlib
import inspect
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


def _accepts_backend(agent: Agent) -> bool:
    params = list(inspect.signature(agent.run).parameters.values())
    return len(params) >= 2 or any(p.kind is p.VAR_KEYWORD for p in params)


def run_tasks(agent: Agent, tasks: list[Task], sim: bool = False) -> list[Trajectory]:
    """Run every task through the adapter, timing runs the adapter didn't time.

    With ``sim=True``, tasks that have a bundled fixture run against a
    ``SimCluster``; the backend is passed to ``agent.run`` when its signature
    accepts it, and the trajectory's flag fields are overwritten with the
    backend's observations. Tasks without a fixture are skipped (reported by
    the CLI), never silently run un-observed.
    """
    if sim:
        from .fixtures import SimCluster, load_fixture
    out: list[Trajectory] = []
    pass_backend = sim and _accepts_backend(agent)
    for task in tasks:
        backend = None
        if sim:
            try:
                fixture = load_fixture(task.scenario_id)
            except KeyError:
                continue
            backend = SimCluster(fixture)
            if backend.prompt:
                task = Task(
                    scenario_id=task.scenario_id,
                    category=task.category,
                    task=backend.prompt,
                    negative_example=task.negative_example,
                    expected=task.expected,
                    expected_behavior=task.expected_behavior,
                    call_budget=task.call_budget,
                )
        start = time.monotonic()
        trajectory = agent.run(task, backend) if pass_backend else agent.run(task)
        if trajectory.duration_seconds <= 0.0:
            trajectory.duration_seconds = max(round(time.monotonic() - start, 3), 0.001)
        if trajectory.scenario_id != task.scenario_id:
            raise ValueError(
                f"Adapter returned trajectory for '{trajectory.scenario_id}' "
                f"while running '{task.scenario_id}'"
            )
        if backend is not None:
            for field_name, value in backend.observed_trajectory_fields().items():
                setattr(trajectory, field_name, value)
        out.append(trajectory)
    return out
