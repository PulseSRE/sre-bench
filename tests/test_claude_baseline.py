"""The Claude baseline's tool loop, exercised against a mock client.

No network, no credentials: a scripted fake client walks the manual loop
through gather → fix → verify → answer, and the SimCluster observes it.
"""

from types import SimpleNamespace

from sre_bench.baselines.claude_agent import ClaudeBaselineAgent, _tool_definitions
from sre_bench.fixtures import CANONICAL_TOOLS, SimCluster, load_fixture
from sre_bench.types import Task


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(i, name, args):
    return SimpleNamespace(type="tool_use", id=f"tu_{i}", name=name, input=args)


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def test_tool_definitions_cover_registry_and_mark_destructive():
    tools = _tool_definitions()
    assert {t["name"] for t in tools} == set(CANONICAL_TOOLS)
    restart = next(t for t in tools if t["name"] == "restart_deployment")
    assert "DESTRUCTIVE" in restart["description"]
    assert "confirmed" in restart["input_schema"]["properties"]


def test_baseline_loop_drives_sim_to_verified_fix():
    responses = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                _text("Gathering evidence."),
                _tool_use(1, "list_pods", {"namespace": "production"}),
                _tool_use(2, "get_pod_logs", {"name": "api-server"}),
            ],
        ),
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                _tool_use(
                    3, "restart_deployment", {"namespace": "production", "name": "api-server", "confirmed": True}
                ),
            ],
        ),
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use(4, "list_pods", {"namespace": "production"})],
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[_text("Root cause: stale DB port env because of a reverted ConfigMap; restarted and verified.")],
        ),
    ]
    client = FakeClient(responses)
    agent = ClaudeBaselineAgent(client=client)
    backend = SimCluster(load_fixture("sre_crashloop_resolution"))
    task = Task(scenario_id="sre_crashloop_resolution", category="sre", task="fix it", expected_behavior="fix")

    trajectory = agent.run(task, backend)

    assert trajectory.completed
    assert "Root cause" in trajectory.final_response
    observed = backend.observed_trajectory_fields()
    assert observed["tool_calls"] == ["list_pods", "get_pod_logs", "restart_deployment", "list_pods"]
    assert observed["verification_passed"] is True
    assert observed["rejected_tools"] == 0
    assert not observed["hallucinated_tool"]
    # Tool errors are marked is_error in the results the model sees.
    assert all("tools" in r for r in client.messages.requests)


def test_baseline_marks_tool_errors_for_the_model():
    responses = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use(1, "delete_pod", {"namespace": "jobs", "name": "batch-worker-2"})],
        ),
        SimpleNamespace(stop_reason="end_turn", content=[_text("Deletion was rejected pending confirmation.")]),
    ]
    client = FakeClient(responses)
    agent = ClaudeBaselineAgent(client=client)
    backend = SimCluster(load_fixture("error_permission_denied"))
    task = Task(scenario_id="error_permission_denied", category="sre", task="delete it", expected_behavior="degrade")

    agent.run(task, backend)

    second_request = client.messages.requests[1]
    tool_results = second_request["messages"][-1]["content"]
    assert tool_results[0]["is_error"] is True  # unconfirmed destructive call was rejected
    assert backend.rejected_tools == 1


def test_baseline_turn_limit_marks_incomplete():
    looping = SimpleNamespace(
        stop_reason="tool_use",
        content=[_tool_use(1, "list_pods", {})],
    )
    client = FakeClient([looping] * 10)
    agent = ClaudeBaselineAgent(client=client)
    backend = SimCluster(load_fixture("sre_pending_pod_capacity"))
    task = Task(
        scenario_id="sre_pending_pod_capacity", category="sre", task="why pending", expected_behavior="diagnose"
    )

    trajectory = agent.run(task, backend)
    assert trajectory.completed is False
