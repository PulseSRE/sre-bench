"""sre-bench command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from pathlib import Path

from .loader import load_submission, load_suite, suite_names
from .scoring import score_reference, score_submission
from .types import SuiteResult, Task, Trajectory


def _print_result(r: SuiteResult) -> None:
    gate = "PASS" if r.gate_passed else "FAIL"
    print(f"suite={r.suite_name} gate={gate} avg={r.average_overall:.4f} passed={r.passed_count}/{r.scenario_count}")
    if r.lanes is not None:
        agent = r.lanes["agent"]
        verification = r.lanes["rubric_verification"]
        if agent["scenario_count"]:
            print(
                f"  agent lane  {agent['average_overall']:.4f} avg, "
                f"{agent['passed_count']}/{agent['scenario_count']} passed"
            )
        if verification["scenario_count"]:
            print(f"  rubric-verification  {verification['caught_count']}/{verification['scenario_count']} caught")
    for k, v in r.dimension_averages.items():
        print(f"  {k:<11} {v:.4f}")
    if r.blocker_counts:
        print(f"  blockers    {r.blocker_counts}")
    if r.missing_scenarios:
        print(f"  MISSING     {len(r.missing_scenarios)}: {', '.join(r.missing_scenarios[:5])}"
              + (" …" if len(r.missing_scenarios) > 5 else ""))


def _tasks_payload(tasks: list[Task], suite: str, description: str) -> dict:
    return {
        "suite_name": suite,
        "description": description,
        "tasks": [
            {
                "scenario_id": t.scenario_id,
                "category": t.category,
                "task": t.task,
                "negative_example": t.negative_example,
                "expected_behavior": t.expected_behavior,
                **({"call_budget": list(t.call_budget)} if t.call_budget is not None else {}),
            }
            for t in tasks
        ],
    }


def cmd_list(_: argparse.Namespace) -> int:
    total = 0
    for name in suite_names():
        tasks, _refs, description = load_suite(name)
        total += len(tasks)
        print(f"{name:<18} {len(tasks):>3} tasks  {description}")
    print(f"{'TOTAL':<18} {total:>3} tasks across {len(suite_names())} suites")
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    tasks, _refs, description = load_suite(args.suite)
    payload = _tasks_payload(tasks, args.suite, description)
    text = json.dumps(payload, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {len(tasks)} tasks to {args.out}")
    else:
        print(text)
    return 0


async def _judge_result(result: SuiteResult, tasks: list[Task], trajectories: dict[str, Trajectory]) -> None:
    from .judge import judge_response

    task_by_id = {t.scenario_id: t for t in tasks}
    for scored in result.scenarios:
        task = task_by_id[scored.scenario_id]
        trajectory = trajectories[scored.scenario_id]
        scored.judge = await judge_response(task.task, trajectory.final_response, trajectory.tool_calls)


def _score_one(args: argparse.Namespace, suite: str, trajectories: dict[str, Trajectory]) -> SuiteResult:
    tasks, _refs, _desc = load_suite(suite)
    result = score_submission(suite, tasks, trajectories)
    if args.judge:
        asyncio.run(_judge_result(result, tasks, trajectories))
    return result


def cmd_score(args: argparse.Namespace) -> int:
    trajectories = load_submission(args.submission)
    suites = suite_names() if args.all else [args.suite]
    if not args.all and not args.suite:
        print("error: pass --suite <name> or --all", file=sys.stderr)
        return 2
    results = [_score_one(args, s, trajectories) for s in suites]
    if args.json or args.out:
        payload = {"results": [r.to_dict() for r in results]}
        text = json.dumps(payload, indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(f"Wrote results to {args.out}")
        else:
            print(text)
    else:
        for r in results:
            _print_result(r)
    if args.fail_on_gate and not all(r.gate_passed for r in results):
        return 1
    return 0


def cmd_reference_scores(args: argparse.Namespace) -> int:
    suites = suite_names() if args.suite is None else [args.suite]
    results = []
    for s in suites:
        tasks, refs, _desc = load_suite(s)
        results.append(score_reference(s, tasks, refs))
    if args.out:
        payload = {"results": [r.to_dict() for r in results]}
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote reference scores to {args.out}")
    else:
        for r in results:
            _print_result(r)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .adapter import load_adapter, run_tasks

    agent = load_adapter(args.adapter)
    suites = suite_names() if args.all else [args.suite]
    if not args.all and not args.suite:
        print("error: pass --suite <name> or --all", file=sys.stderr)
        return 2
    all_trajectories: list[Trajectory] = []
    for s in suites:
        tasks, _refs, _desc = load_suite(s)
        all_trajectories.extend(run_tasks(agent, tasks))
    payload = {"trajectories": [dataclasses.asdict(t) for t in all_trajectories]}
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(all_trajectories)} trajectories to {args.out}")
    if args.score:
        by_id = {t.scenario_id: t for t in all_trajectories}
        for s in suites:
            tasks, _refs, _desc = load_suite(s)
            _print_result(score_submission(s, tasks, by_id))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sre-bench", description="SRE-Bench: a benchmark for SRE agents.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List suites and task counts").set_defaults(func=cmd_list)

    p_tasks = sub.add_parser("tasks", help="Export a suite's tasks (what your harness consumes)")
    p_tasks.add_argument("--suite", required=True, choices=suite_names())
    p_tasks.add_argument("--out", help="Write JSON to a file instead of stdout")
    p_tasks.set_defaults(func=cmd_tasks)

    p_score = sub.add_parser("score", help="Score a submission (trajectory file) against a suite")
    p_score.add_argument("submission", help="Path to submission JSON")
    p_score.add_argument("--suite", choices=suite_names())
    p_score.add_argument("--all", action="store_true", help="Score against every suite")
    p_score.add_argument("--judge", action="store_true", help="Also run the LLM judge (needs ANTHROPIC_API_KEY)")
    p_score.add_argument("--json", action="store_true", help="Print JSON instead of a summary")
    p_score.add_argument("--out", help="Write JSON results to a file")
    p_score.add_argument("--fail-on-gate", action="store_true", help="Exit 1 if any suite gate fails")
    p_score.set_defaults(func=cmd_score)

    p_ref = sub.add_parser("reference-scores", help="Score the bundled reference trajectories")
    p_ref.add_argument("--suite", choices=suite_names())
    p_ref.add_argument("--out", help="Write JSON results to a file")
    p_ref.set_defaults(func=cmd_reference_scores)

    p_run = sub.add_parser("run", help="Run an adapter over a suite and record trajectories")
    p_run.add_argument("--adapter", required=True, help="'module.path:factory' returning an Agent")
    p_run.add_argument("--suite", choices=suite_names())
    p_run.add_argument("--all", action="store_true", help="Run every suite")
    p_run.add_argument("--out", required=True, help="Where to write the trajectory file")
    p_run.add_argument("--score", action="store_true", help="Score immediately after running")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
