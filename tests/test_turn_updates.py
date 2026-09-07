"""Deterministic steering at the real shared model/tool loop boundaries."""
from __future__ import annotations

import concurrent.futures
import json
import threading

import pytest

from aura.client import ApiError, ContentDelta, Done, ToolResult
from aura.conversation.agent_loop import AgentLoop, LoopStop
from aura.conversation.history import History, is_real_user_message
from aura.conversation.manager import ConversationManager
from aura.conversation.manager_tool_round import ToolRoundRunner
from aura.conversation.persistence import load_conversation, save_conversation
from aura.conversation.tool_runner import ToolRunner
from aura.conversation.tools import ToolRegistry
from aura.conversation.tools._types import ApprovalDecision
from aura.conversation.turn_updates import IN_CONTEXT, NOT_DELIVERED, UPDATE_KEY, TurnUpdates
from aura.model_streams import PRODUCTION_STREAM_HOOK, model_streams


def call(call_id, name, **args):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def done(*calls, text="done"):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = list(calls)
    return Done(finish_reason="tool_calls" if calls else "stop", full_message=message)


def run_loop(tmp_path, stream, updates, *, registry=None, cancel=None, on_event=None):
    history = History()
    history.append_user_text("Read, then update the file.", literal_composer_text="Read, then update the file.")
    registry = registry or ToolRegistry(tmp_path)
    runner = ToolRunner(history, tmp_path)
    tool_round = ToolRoundRunner(history=history, tools=registry, tool_runner=runner)
    loop = AgentLoop(history=history, stream=stream, tool_round=tool_round)
    events = []
    try:
        outcome = loop.run(
            on_event=on_event or events.append,
            approval_cb=lambda _request: ApprovalDecision(action="approve"),
            cancel_event=cancel or threading.Event(), model="frozen-model", thinking="off",
            tool_defs=registry.tool_defs(), updates=updates.cursor(root=True),
        )
        return history, outcome, events
    finally:
        runner.close()


@pytest.mark.parametrize("proposed_tools", [False, True])
def test_update_during_stream_is_considered_before_tools_or_completion(tmp_path, proposed_tools):
    updates = TurnUpdates()
    requests = []

    def stream(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            yield ContentDelta("First thought")
            updates.submit("Use the existing helper instead.")
            calls = [call("old-write", "apply_patch", operation="create", path="wrong.txt", content="wrong")] if proposed_tools else []
            yield done(*calls, text="Old answer")
        else:
            yield done(text="Reconsidered answer")

    history, outcome, _ = run_loop(tmp_path, stream, updates)
    assert outcome.completed
    assert len(requests) == 2
    assert requests[1]["messages"][-1] == {"role": "user", "content": "Use the existing helper instead."}
    assert requests[0]["tools"] == requests[1]["tools"]
    assert requests[1]["model"] == "frozen-model"
    assert not (tmp_path / "wrong.txt").exists()
    if proposed_tools:
        result = history.messages[2]
        assert result["role"] == "tool"
        assert json.loads(result["content"])["reason"] == "user_updated_task"
    assert history.messages[-1]["content"] == "Reconsidered answer"
    assert updates.submit("too late") is None


def test_read_settles_and_now_stale_write_is_not_executed(tmp_path, monkeypatch):
    (tmp_path / "note.txt").write_text("real contents", encoding="utf-8")
    updates = TurnUpdates()
    registry = ToolRegistry(tmp_path)
    execute = registry.execute
    ran = []

    def execute_and_update(name, *args, **kwargs):
        ran.append(name)
        result = execute(name, *args, **kwargs)
        updates.submit("Use the existing helper instead.")
        return result

    monkeypatch.setattr(registry, "execute", execute_and_update)
    requests = []

    def stream(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            yield done(call("read", "read_file", path="note.txt"), call("write", "apply_patch", operation="replace", path="note.txt", content="bad"))
        else:
            yield done()

    history, _, events = run_loop(tmp_path, stream, updates, registry=registry)
    assert ran == ["read_file"]
    assert (tmp_path / "note.txt").read_text() == "real contents"
    results = [m for m in history.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in results] == ["read", "write"]
    assert "real contents" in results[0]["content"]
    assert json.loads(results[1]["content"])["execution_status"] == "not_executed"
    assert [event.ok for event in events if isinstance(event, ToolResult)] == [True, False]
    assert [m["role"] for m in history.messages] == ["user", "assistant", "tool", "tool", "user", "assistant"]


def test_update_from_read_receipt_prevents_next_serial_tool(tmp_path):
    (tmp_path / "note.txt").write_text("read result", encoding="utf-8")
    updates, requests = TurnUpdates(), []

    def stream(**kwargs):
        requests.append(kwargs)
        yield done(call("read", "read_file", path="note.txt"), call("write", "apply_patch", operation="replace", path="note.txt", content="bad")) if len(requests) == 1 else done()

    def on_event(event):
        if isinstance(event, ToolResult) and event.tool_call_id == "read":
            updates.submit("Do not change the file")

    history, _, _ = run_loop(tmp_path, stream, updates, on_event=on_event)
    assert (tmp_path / "note.txt").read_text() == "read result"
    assert json.loads(history.messages[3]["content"])["reason"] == "user_updated_task"


def test_started_approved_write_keeps_its_real_effect_and_result(tmp_path, monkeypatch):
    updates = TurnUpdates()
    registry = ToolRegistry(tmp_path)
    execute = registry.execute

    def update_after_write(name, *args, **kwargs):
        result = execute(name, *args, **kwargs)
        assert result.ok
        updates.submit("Preserve that edit and reconsider the rest.")
        return result

    monkeypatch.setattr(registry, "execute", update_after_write)
    requests = []

    def stream(**kwargs):
        requests.append(kwargs)
        yield done(call("write", "apply_patch", operation="create", path="kept.txt", content="real effect"), call("read", "read_file", path="kept.txt")) if len(requests) == 1 else done()

    history, outcome, events = run_loop(tmp_path, stream, updates, registry=registry)
    assert outcome.completed
    assert (tmp_path / "kept.txt").read_text() == "real effect"
    assert [e.ok for e in events if isinstance(e, ToolResult)] == [True, False]
    assert json.loads(history.messages[2]["content"])["ok"]
    assert json.loads(history.messages[3]["content"])["reason"] == "user_updated_task"


def test_model_backend_stays_frozen_when_steering_continues(tmp_path):
    updates, requests = TurnUpdates(), []
    history = History()
    history.append_user_text("Original request")
    manager = ConversationManager(history, ToolRegistry(tmp_path))
    previous = model_streams.get_handler(PRODUCTION_STREAM_HOOK)

    def forbidden(**kwargs):
        pytest.fail("A mid-task provider change reached the active task")

    def stream(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            model_streams.unregister(PRODUCTION_STREAM_HOOK)
            model_streams.register(PRODUCTION_STREAM_HOOK, forbidden)
            updates.submit("Continue with the frozen model")
        yield done()

    model_streams.unregister(PRODUCTION_STREAM_HOOK)
    model_streams.register(PRODUCTION_STREAM_HOOK, stream)
    try:
        manager.send(on_event=lambda _: None, approval_cb=lambda _: None, cancel_event=threading.Event(), model="frozen-model", thinking="off", turn_updates=updates)
        assert len(requests) == 2
        assert all(r["model"] == "frozen-model" for r in requests)
    finally:
        manager.close()
        model_streams.unregister(PRODUCTION_STREAM_HOOK)
        if previous is not None:
            model_streams.register(PRODUCTION_STREAM_HOOK, previous)


def test_parallel_observations_settle_but_executor_queue_is_held(tmp_path, monkeypatch):
    (tmp_path / "note.txt").write_text("observed", encoding="utf-8")
    updates = TurnUpdates()
    started = threading.Barrier(3)
    release = threading.Event()
    queued = threading.Event()
    pool_type = concurrent.futures.ThreadPoolExecutor

    class Pool(pool_type):
        def __init__(self, **kwargs):
            super().__init__(max_workers=2)
            self.submissions = 0

        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            self.submissions += 1
            if self.submissions == 3:
                queued.set()
            return future

    monkeypatch.setattr("aura.conversation.manager_tool_round.concurrent.futures.ThreadPoolExecutor", Pool)
    registry = ToolRegistry(tmp_path)
    execute = registry.execute
    ran = []

    def blocked(name, *args, **kwargs):
        ran.append(name)
        started.wait(timeout=5)
        assert release.wait(5)
        return execute(name, *args, **kwargs)

    monkeypatch.setattr(registry, "execute", blocked)
    requests = []

    def stream(**kwargs):
        requests.append(kwargs)
        yield done(*(call(str(i), "read_file", path="note.txt") for i in range(3))) if len(requests) == 1 else done()

    with pool_type(max_workers=1) as worker:
        future = worker.submit(run_loop, tmp_path, stream, updates, registry=registry)
        try:
            started.wait(timeout=5)
            assert queued.wait(5)
            updates.submit("Inspect only the helper.")
        finally:
            release.set()
        history, outcome, _ = future.result(timeout=5)
    assert outcome.completed
    assert len(ran) == 2
    results = [m for m in history.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in results] == ["0", "1", "2"]
    assert all("observed" in m["content"] for m in results[:2])
    assert json.loads(results[2]["content"])["reason"] == "user_updated_task"


def test_independent_ordered_cursors_and_completion_race():
    updates = TurnUpdates()
    root, first, second = updates.cursor(root=True), updates.cursor(), updates.cursor()
    histories = [History(), History(), History()]
    updates.submit("First correction")
    first.append_to(histories[1])
    updates.submit("Second correction")
    assert not root.finish()  # acceptance won the race with completion
    root.append_to(histories[0])
    second.append_to(histories[2])
    first.append_to(histories[1])
    later = History()
    updates.cursor().append_to(later)
    for history in [*histories, later]:
        assert [m["content"] for m in history.messages] == ["First correction", "Second correction"]
    assert root.finish()
    assert updates.submit("Closed task") is None  # completion won this race


@pytest.mark.parametrize("ending", ["stop", "error", "raise", "no_response"])
def test_manager_retains_accepted_but_undelivered_updates_on_failure(tmp_path, ending):
    updates = TurnUpdates()
    history = History()
    history.append_user_text("Original request")
    manager = ConversationManager(history, ToolRegistry(tmp_path))
    cancel = threading.Event()

    def stream(**kwargs):
        yield ContentDelta("Working")
        updates.submit("Correction that must survive")
        if ending == "stop":
            cancel.set()
            updates.close()
        elif ending == "error":
            yield ApiError(status_code=500, message="Provider failed")
        elif ending == "raise":
            raise RuntimeError("Provider exploded")

    manager._loop._stream = stream
    kwargs = dict(on_event=lambda _: None, approval_cb=lambda _: None, cancel_event=cancel, model="test", thinking="off", turn_updates=updates)
    try:
        if ending == "raise":
            with pytest.raises(RuntimeError):
                manager.send(**kwargs)
        else:
            manager.send(**kwargs)
        assert history.messages[-1]["content"] == "Correction that must survive"
        assert history.messages[-1][UPDATE_KEY]["status"] == NOT_DELIVERED
        assert updates.submit("Next task?") is None
    finally:
        manager.close()


def test_stop_after_terminal_response_with_pending_correction_is_cancellation(tmp_path):
    updates, cancel = TurnUpdates(), threading.Event()

    def stream(**kwargs):
        updates.submit("Still have work to do")
        yield done()
        cancel.set()
        updates.close()

    _, outcome, _ = run_loop(tmp_path, stream, updates, cancel=cancel)
    assert outcome.stop is LoopStop.CANCELLED


def test_reset_discards_old_delivery_and_does_not_leak_into_new_history():
    updates = TurnUpdates()
    cursor = updates.cursor()
    updates.submit("Old workspace instruction")
    updates.invalidate()
    history = History()
    history.append_user_text("New task")
    cursor.append_to(history)
    updates.persist(history)
    assert len(history.messages) == 1
    assert not TurnUpdates().cursor().pending()


def test_history_retry_keeps_corrections_real_effects_and_local_metadata_off_wire(tmp_path):
    history = History()
    history.append_user_text("Original request", literal_composer_text="Original request", explicit_installed_skill_ids=("frozen",))
    history.append_assistant(done(call("write", "write_file", path="x", content="x")).full_message)
    history.append_tool_result("write", '{"ok": true, "applied": true}')
    updates = TurnUpdates()
    updates.submit("Use the existing helper instead.")
    updates.cursor(root=True).append_to(history)
    history.append_assistant(done(text="Corrected answer").full_message)
    updates.close()
    path = save_conversation(history, tmp_path, "test", "off")
    loaded = load_conversation(path)
    assert loaded.chat_items[-2]["kind"] == "user_update"
    assert loaded.history.rewind_to_last_user_turn()
    assert loaded.history.messages[-1]["content"] == "Use the existing helper instead."
    assert is_real_user_message(loaded.history.messages[-1])
    assert loaded.history.latest_real_user_text() == "Original request"
    assert loaded.history.latest_real_user_explicit_installed_skill_ids() == ("frozen",)
    assert loaded.history.messages[2]["content"] == '{"ok": true, "applied": true}'
    assert all(not any(key.startswith("aura_") for key in m) for m in loaded.history.for_api())
    retried = TurnUpdates(preceding=loaded.history.latest_task_updates())
    root_cursor = retried.cursor(root=True)
    root_cursor.append_to(loaded.history)
    assert len(loaded.history.latest_task_updates()) == 1
    helper = History()
    retried.cursor().append_to(helper)
    assert helper.messages[0][UPDATE_KEY]["status"] == IN_CONTEXT


def test_retry_updates_the_receipt_of_a_previously_undelivered_correction():
    history = History()
    updates = TurnUpdates()
    update = updates.submit("Correction before Stop")
    updates.close()
    updates.persist(history)
    receipts = []
    retry = TurnUpdates(lambda *args: receipts.append(args), preceding=history.messages)
    retry.cursor(root=True).append_to(history)
    assert receipts == [(update.id, IN_CONTEXT)]
    assert len(history.messages) == 1


def test_unexpected_tool_round_failure_cannot_put_update_inside_unfinished_block(tmp_path, monkeypatch):
    history, updates = History(), TurnUpdates()
    history.append_user_text("Original request")
    manager = ConversationManager(history, ToolRegistry(tmp_path))
    manager._loop._stream = lambda **_: iter([done(call("read", "read_file", path="note.txt"))])

    def fail(**kwargs):
        updates.submit("Retain this correction")
        raise RuntimeError("Executor unavailable")

    monkeypatch.setattr(manager._tool_round_runner, "run", fail)
    try:
        with pytest.raises(RuntimeError):
            manager.send(on_event=lambda _: None, approval_cb=lambda _: None, cancel_event=threading.Event(), model="test", thinking="off", turn_updates=updates)
        assert [m["role"] for m in history.messages] == ["user", "assistant", "tool", "user"]
        result = json.loads(history.messages[2]["content"])
        assert not result["ok"]
        assert result["failure_class"] == "harness_error"
        assert "cancelled" not in result
        assert history.messages[-1]["content"] == "Retain this correction"
    finally:
        manager.close()
