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


def _variance_payload(suites: list[str], per_run: list[list[SuiteResult]]) -> dict:
    import statistics

    payload: dict = {"runs": len(per_run), "suites": []}
    for idx, suite in enumerate(suites):
        results = [run[idx] for run in per_run]
        overalls = [r.average_overall for r in results]
        scenario_stats = []
        by_id: dict[str, list[float]] = {}
        for r in results:
            for s in r.scenarios:
                by_id.setdefault(s.scenario_id, []).append(s.overall)
        for sid, vals in sorted(by_id.items()):
            stdev = statistics.stdev(vals) if len(vals) > 1 else 0.0
            scenario_stats.append(
                {
                    "scenario_id": sid,
                    "runs": len(vals),
                    "mean": round(statistics.mean(vals), 4),
                    "stdev": round(stdev, 4),
                }
            )
        payload["suites"].append(
            {
                "suite_name": suite,
                "gate_passed_runs": sum(1 for r in results if r.gate_passed),
                "average_overall": {
                    "mean": round(statistics.mean(overalls), 4),
                    "stdev": round(statistics.stdev(overalls), 4) if len(overalls) > 1 else 0.0,
                    "min": min(overalls),
                    "max": max(overalls),
                },
                "scenarios": scenario_stats,
            }
        )
    return payload


def _print_variance(payload: dict) -> None:
    for suite in payload["suites"]:
        avg = suite["average_overall"]
        print(
            f"suite={suite['suite_name']} runs={payload['runs']} "
            f"gate={suite['gate_passed_runs']}/{payload['runs']} PASS "
            f"avg mean={avg['mean']:.4f} stdev={avg['stdev']:.4f} min={avg['min']:.4f} max={avg['max']:.4f}"
        )
        unstable = [s for s in suite["scenarios"] if s["stdev"] > 0.05]
        for s in sorted(unstable, key=lambda s: -s["stdev"]):
            print(f"  unstable    {s['scenario_id']}  mean={s['mean']:.4f} stdev={s['stdev']:.4f}")


def cmd_score(args: argparse.Namespace) -> int:
    suites = suite_names() if args.all else [args.suite]
    if not args.all and not args.suite:
        print("error: pass --suite <name> or --all", file=sys.stderr)
        return 2
    per_run = [[_score_one(args, s, load_submission(path)) for s in suites] for path in args.submission]

    if len(per_run) > 1:
        payload = _variance_payload(suites, per_run)
        if args.json or args.out:
            text = json.dumps(payload, indent=2)
            if args.out:
                Path(args.out).write_text(text + "\n", encoding="utf-8")
                print(f"Wrote variance report to {args.out}")
            else:
                print(text)
        else:
            _print_variance(payload)
        if args.fail_on_gate and any(s["gate_passed_runs"] < payload["runs"] for s in payload["suites"]):
            return 1
        return 0

    results = per_run[0]
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
    skipped: list[str] = []
    for s in suites:
        tasks, _refs, _desc = load_suite(s)
        trajectories = run_tasks(agent, tasks, sim=args.sim)
        if args.sim:
            covered = {t.scenario_id for t in trajectories}
            skipped.extend(t.scenario_id for t in tasks if t.scenario_id not in covered)
        all_trajectories.extend(trajectories)
    payload: dict = {"trajectories": [dataclasses.asdict(t) for t in all_trajectories]}
    if args.sim:
        payload = {"environment": "sim", **payload}
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(all_trajectories)} trajectories to {args.out}")
    if skipped:
        more = " …" if len(skipped) > 5 else ""
        print(f"Skipped {len(skipped)} tasks with no fixture yet: {', '.join(skipped[:5])}" + more)
    if args.score:
        by_id = {t.scenario_id: t for t in all_trajectories}
        for s in suites:
            tasks, _refs, _desc = load_suite(s)
            _print_result(score_submission(s, tasks, by_id))
    return 0


def cmd_fixtures(_: argparse.Namespace) -> int:
    from .fixtures import fixture_ids

    have = set(fixture_ids())
    total_tasks = 0
    total_covered = 0
    for name in suite_names():
        tasks, _refs, _desc = load_suite(name)
        covered = sum(1 for t in tasks if t.scenario_id in have)
        total_tasks += len(tasks)
        total_covered += covered
        marker = "full" if covered == len(tasks) else ("partial" if covered else "-")
        print(f"{name:<18} {covered:>3}/{len(tasks):<3} {marker}")
    print(f"{'TOTAL':<18} {total_covered:>3}/{total_tasks:<3}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .verify import VIOLATION, submission_environment, verify_submission

    trajectories = load_submission(args.submission)
    sim_env = submission_environment(args.submission) == "sim"
    suites = suite_names() if args.all else [args.suite]
    if not args.all and not args.suite:
        print("error: pass --suite <name> or --all", file=sys.stderr)
        return 2
    findings = []
    for s in suites:
        tasks, _refs, _desc = load_suite(s)
        findings.extend(verify_submission(tasks, trajectories, sim_env))
    if args.json:
        payload = {"environment": "sim" if sim_env else "external", "findings": [f.to_dict() for f in findings]}
        print(json.dumps(payload, indent=2))
    else:
        if not findings:
            print("verify: clean — no findings")
        for f in findings:
            print(f"{f.level.upper():<10} {f.scenario_id}  [{f.check}] {f.detail}")
    if any(f.level == VIOLATION for f in findings):
        return 1
    return 0


def cmd_durable(args: argparse.Namespace) -> int:
    import os

    from .durable import RestDriver, run_probes

    base_url = args.base_url or os.environ.get("PULSE_AGENT_URL", "")
    token = args.token or os.environ.get("PULSE_AGENT_TOKEN", "")
    if not base_url or not token:
        print("error: pass --base-url/--token or set PULSE_AGENT_URL/PULSE_AGENT_TOKEN", file=sys.stderr)
        return 2

    report = run_probes(RestDriver(base_url, token), include_in_pod=not args.rest_only)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        for c in report.checks:
            print(f"{'PASS' if c.passed else 'FAIL':<6} {c.check:<32} {c.evidence}")
        print(f"\ndurable: {'all checks passed' if report.passed else 'FAILED'}")
    return 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sre-bench", description="SRE-Bench: a benchmark for SRE agents.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List suites and task counts").set_defaults(func=cmd_list)

    p_tasks = sub.add_parser("tasks", help="Export a suite's tasks (what your harness consumes)")
    p_tasks.add_argument("--suite", required=True, choices=suite_names())
    p_tasks.add_argument("--out", help="Write JSON to a file instead of stdout")
    p_tasks.set_defaults(func=cmd_tasks)

    p_score = sub.add_parser("score", help="Score a submission (trajectory file) against a suite")
    p_score.add_argument(
        "submission",
        nargs="+",
        help="Path(s) to submission JSON. Multiple files = repeated runs; reports mean/stdev and unstable scenarios",
    )
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
    p_run.add_argument(
        "--sim",
        action="store_true",
        help="Run against the bundled simulated cluster fixtures; flag fields come from the observing backend",
    )
    p_run.set_defaults(func=cmd_run)

    sub.add_parser("fixtures", help="Show simulated-fixture coverage per suite").set_defaults(func=cmd_fixtures)

    p_verify = sub.add_parser("verify", help="Audit a submission's self-reported flags for structural violations")
    p_verify.add_argument("submission", help="Path to submission JSON")
    p_verify.add_argument("--suite", choices=suite_names())
    p_verify.add_argument("--all", action="store_true", help="Verify against every suite")
    p_verify.add_argument("--json", action="store_true", help="Print findings as JSON")
    p_verify.set_defaults(func=cmd_verify)

    p_durable = sub.add_parser(
        "durable",
        help="Probe a live agent's durable workflow execution (approval waits, cancel, verdicts, listing)",
    )
    p_durable.add_argument("--base-url", help="Agent base URL (or PULSE_AGENT_URL)")
    p_durable.add_argument("--token", help="Agent bearer token (or PULSE_AGENT_TOKEN)")
    p_durable.add_argument(
        "--rest-only",
        action="store_true",
        help="Skip probes that need the in-pod Temporal client (they FAIL, not skip, when unavailable)",
    )
    p_durable.add_argument("--json", action="store_true", help="Print the report as JSON")
    p_durable.set_defaults(func=cmd_durable)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
