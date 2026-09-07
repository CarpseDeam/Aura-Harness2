"""The task log travels through real individual, workflow and helper owners."""
from __future__ import annotations

import threading

from test_agent_delegation import _entry
from test_agent_team_tool import _context, _payload
from test_agent_workflow_runtime import AGENT_IDS, _dag_graph, _freeze, _graph, _with_helper
from test_turn_updates import call, done

from aura.agents.runtime import AgentDelegationRunner
from aura.agents.workflow_runner import WorkflowRunner, WorkflowRunStatus
from aura.conversation.tools.registry import ToolRegistry
from aura.conversation.turn_updates import TurnUpdates


def corrections(request):
    return [m["content"] for m in request["messages"] if m["role"] == "user"][1:]


def test_individual_agent_reconsiders_while_root_is_still_delegating(tmp_path, monkeypatch):
    monkeypatch.setattr("aura.config.has_usable_provider_configuration", lambda _: True)
    updates = TurnUpdates()
    requests = []

    class Backend:
        def stream(self, **kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                updates.submit("Use the existing helper instead.")
            yield done()

    runner = AgentDelegationRunner(
        workspace_root=tmp_path, inherited_provider="deepseek", inherited_model="deepseek-chat",
        backend_factory=lambda _: Backend(),
    )
    result = runner.run(_entry(), "Investigate", turn_updates=updates)
    assert result.status.value == "completed"
    assert len(requests) == 2
    assert corrections(requests[1]) == ["Use the existing helper instead."]
    assert updates.submit("Root is still active") is not None


def test_child_terminal_response_cannot_hide_pending_update_after_teardown_error(tmp_path, monkeypatch):
    monkeypatch.setattr("aura.config.has_usable_provider_configuration", lambda _: True)
    updates, cancel = TurnUpdates(), threading.Event()

    class Backend:
        def stream(self, **kwargs):
            yield done()
            updates.submit("More work is required")
            cancel.set()
            raise RuntimeError("Transport closed during Stop")

    runner = AgentDelegationRunner(
        workspace_root=tmp_path, inherited_provider="deepseek", inherited_model="deepseek-chat",
        backend_factory=lambda _: Backend(),
    )
    result = runner.run(_entry(), "Investigate", cancel_event=cancel, turn_updates=updates)
    assert result.status.value == "cancelled"


def test_automatic_team_receives_updates_inside_native_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr("aura.config.has_usable_provider_configuration", lambda _: True)
    updates = TurnUpdates()
    requests = []

    class Backend:
        def stream(self, **kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                updates.submit("Use the existing helper instead.")
            yield done()

    registry = ToolRegistry(tmp_path)
    context = _context()
    registry.set_agent_turn_context(context)
    registry.turn_updates = updates
    registry.set_agent_workflow_runner(WorkflowRunner(workspace_root=tmp_path, backend_factory=lambda _: Backend()))
    result = registry.execute("run_agent_team", _payload(), approval_cb=lambda _: None)
    assert result.ok, result.payload
    assert len(requests) == 2
    assert corrections(requests[1]) == ["Use the existing helper instead."]
    assert registry.turn_agent_context is context


def test_parallel_workflow_agents_each_receive_ordered_updates(tmp_path, monkeypatch):
    graph = _dag_graph(("a", "b"), (("task", "a"), ("task", "b"), ("a", "result"), ("b", "result")))
    plan = _freeze(monkeypatch, graph)
    updates = TurnUpdates()
    started = threading.Barrier(3)
    release = threading.Event()
    backends = []

    class Backend:
        def __init__(self):
            self.requests = []

        def stream(self, **kwargs):
            self.requests.append(kwargs)
            if len(self.requests) == 1:
                started.wait(timeout=5)
                assert release.wait(5)
            yield done()

    def factory(_provider):
        backend = Backend()
        backends.append(backend)
        return backend

    runner = WorkflowRunner(workspace_root=tmp_path, backend_factory=factory)
    results = []
    thread = threading.Thread(target=lambda: results.append(runner.run(plan, "Inspect in parallel", turn_updates=updates)))
    thread.start()
    try:
        started.wait(timeout=5)
        updates.submit("First correction")
        updates.submit("Second correction")
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert results[0].status is WorkflowRunStatus.COMPLETED
    assert len(backends) == 2
    for backend in backends:
        assert len(backend.requests) == 2
        assert corrections(backend.requests[1]) == ["First correction", "Second correction"]
        assert backend.requests[0]["tools"] == backend.requests[1]["tools"]


def test_saved_workflow_and_later_nested_helpers_inherit_task_updates(tmp_path, monkeypatch):
    graph = _with_helper(_graph(1), "step1", node_id="helper", agent_id=AGENT_IDS[1])
    graph = _with_helper(graph, "helper", node_id="nested", agent_id=AGENT_IDS[2])
    plan = _freeze(monkeypatch, graph)
    updates = TurnUpdates()
    backends = []

    class Backend:
        def __init__(self, index):
            self.index = index
            self.requests = []

        def stream(self, **kwargs):
            self.requests.append(kwargs)
            round_number = len(self.requests)
            if self.index == 0 and round_number == 1:
                updates.submit("First correction")
                updates.submit("Second correction")
                yield done()
            elif self.index == 0 and round_number == 2:
                yield done(call("h", "delegate_agent", helper_node_id="helper", task="Focused check"))
            elif self.index == 1 and round_number == 1:
                yield done(call("n", "delegate_agent", helper_node_id="nested", task="Deeper check"))
            elif self.index == 2 and round_number == 1:
                updates.submit("Correction while the nested helper is active")
                yield done()
            else:
                yield done()

    def factory(_provider):
        backend = Backend(len(backends))
        backends.append(backend)
        return backend

    registry = ToolRegistry(tmp_path)
    registry.turn_updates = updates
    registry.set_turn_workflow_plan(plan)
    registry.set_agent_workflow_runner(WorkflowRunner(workspace_root=tmp_path, backend_factory=factory))
    result = registry.execute(
        "run_agent_workflow", {"workflow_id": plan.graph_id, "task": "Investigate"}, approval_cb=lambda _: None,
    )
    assert result.ok, result.payload
    assert len(backends) == 3
    assert corrections(backends[0].requests[1]) == ["First correction", "Second correction"]
    for backend in backends[1:]:
        assert corrections(backend.requests[0]) == ["First correction", "Second correction"]
    for backend in backends:
        assert corrections(backend.requests[-1]) == [
            "First correction", "Second correction", "Correction while the nested helper is active"
        ]
    assert len(result.payload["helper_invocations"]) == 2
