"""Qt tests of real composer routing, receipts, persistence and completion."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_turn_updates import done

from aura.agents.turn_context import AgentTurnContext
from aura.bridge.qt_bridge import ConversationBridge
from aura.conversation.history import History
from aura.conversation.persistence import load_conversation, save_conversation
from aura.conversation.turn_updates import IN_CONTEXT, PENDING, UPDATE_KEY, TurnUpdates
from aura.gui.chat_view import ChatView
from aura.gui.composer_skills import ComposerSkill
from aura.gui.conv_persistence import ConversationPersistence
from aura.gui.input_panel import InputPanel, SendPayload
from aura.gui.send_handler import SendHandler
from aura.gui.widgets.aura_glow import AuraPhaseDriver
from aura.model_streams import PRODUCTION_STREAM_HOOK, model_streams


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def composer(qapp, tmp_path):
    driver = AuraPhaseDriver(qapp)
    panel, chat = InputPanel(tmp_path), ChatView(driver)
    yield panel, chat
    chat.reset()
    # Existing ChatView layout passes use 0/50/150ms singleShot callbacks.
    # Drain them while the real widget and phase driver are still alive.
    QTest.qWait(200)
    panel.deleteLater()
    chat.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    driver.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def spin_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(1)
    assert predicate()


def test_composer_updates_and_fifo_snapshots_have_separate_routes(composer, tmp_path, monkeypatch):
    monkeypatch.setattr("aura.gui.send_handler.has_usable_provider_configuration", lambda _: True)
    updates = TurnUpdates()
    context = AgentTurnContext.enabled()
    bridge = SimpleNamespace(
        is_running=lambda: True, submit_task_update=updates.submit, request_cancel=updates.close,
    )
    panel, chat = composer
    handler = SendHandler(bridge, chat, panel, SimpleNamespace(provider="deepseek"), tmp_path, agent_context_provider=lambda **_: context)
    panel.sent.connect(lambda payload: handler.handle_send(payload, "queued-model", "high"))
    panel.set_execution_active(True)
    assert panel._send_btn.text() == "Send update"
    assert panel._queue_btn.text() == "Queue next task"
    panel.set_text("Next task")
    panel.select_installed_skill("project:one", "One")
    QTest.mouseClick(panel._queue_btn, Qt.MouseButton.LeftButton)
    item = handler._message_queue[0]
    assert (item.text, item.model, item.thinking, item.agent_context) == ("Next task", "queued-model", "high", context)
    assert item.selected_skills == (ComposerSkill("project:one", "One"),)
    panel.set_text("Use the existing helper instead.")
    QTest.keyClick(panel._editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert handler._message_queue == [item]
    assert chat.chat_items[-1]["kind"] == "user_update"
    assert chat.chat_items[-1]["status"] == PENDING
    history = History()
    updates.cursor().append_to(history)
    assert history.messages[-1]["content"] == "Use the existing helper instead."
    panel.set_text("newer unsent draft")
    updates.close()
    handler.handle_send(SendPayload("late correction", [], send_update=True), "other-model", "off")
    assert panel._editor.toPlainText() == "newer unsent draft"
    assert chat.chat_items[-1]["text"] == "late correction"
    assert "Not sent" in chat.chat_items[-1]["status"]
    assert handler._message_queue == [item]
    handler.handle_stop(preserve_queue=True)
    assert handler._message_queue == [item]
    handler.handle_stop()
    assert handler._message_queue == []
    assert panel._editor.toPlainText() == "newer unsent draft"


def test_bridge_completion_race_returns_update_while_qthread_still_running(composer, tmp_path, monkeypatch):
    previous = model_streams.get_handler(PRODUCTION_STREAM_HOOK)
    bridge = ConversationBridge(None)
    bridge.set_workspace_root(tmp_path)
    bridge.history.append_user_text("Original request")
    runtime_finished, release = threading.Event(), threading.Event()
    send = bridge._manager.send

    def held_send(**kwargs):
        send(**kwargs)
        runtime_finished.set()
        assert release.wait(5)

    monkeypatch.setattr(bridge._manager, "send", held_send)
    bridge._manager._loop._stream = lambda **_: iter([done()])
    panel, chat = composer
    handler = SendHandler(bridge, chat, panel, SimpleNamespace(provider="deepseek"), tmp_path)
    try:
        bridge.send(model="deepseek-chat", thinking="off")
        assert runtime_finished.wait(5)
        assert bridge.is_running()
        handler.handle_send(SendPayload("Race correction", [], send_update=True), "ignored-model", "high")
        assert panel._editor.toPlainText() == "Race correction"
        assert "Not sent" in chat.chat_items[-1]["status"]
        assert not bridge.history.latest_task_updates()
    finally:
        release.set()
        spin_until(lambda: not bridge.is_running())
        bridge.shutdown()
        model_streams.unregister(PRODUCTION_STREAM_HOOK)
        if previous is not None:
            model_streams.register(PRODUCTION_STREAM_HOOK, previous)


def test_chat_update_persistence_and_retry_replay_preserve_user_identity(composer, tmp_path):
    _, chat = composer
    chat.add_user("Original request")
    chat.begin_assistant()
    chat.append_content("Already observed")
    chat.add_task_update("task:1", "Use helper", PENDING)
    chat.set_task_update_status("task:1", IN_CONTEXT)
    chat.append_content("Reconsidered")
    chat.assistant_done()
    assert [m["kind"] for m in chat.chat_items] == ["user", "assistant", "user_update", "assistant"]
    history = History()
    history.append_user_text("Original request")
    history.messages.append({"role": "user", "content": "Use helper", UPDATE_KEY: {"id": "task:1", "status": IN_CONTEXT}})
    path = save_conversation(history, tmp_path, "test", "off", chat_items=chat.chat_items)
    loaded = load_conversation(path)
    chat.reset()
    replay = SimpleNamespace(_chat=chat, _active_replay_id=0)
    ConversationPersistence._render_chat_items(replay, loaded.chat_items)
    assert chat._task_update_cards["task:1"]._header.text() == "You · update to current task"
    assert chat._task_update_cards["task:1"]._update_status.text() == IN_CONTEXT
    chat.reset()
    replay._bridge = SimpleNamespace(history=loaded.history)
    ConversationPersistence.replay_history(replay, synchronous=True)
    assert "task:1" in chat._task_update_cards
    assert chat.chat_items[-1]["kind"] == "user_update"
    chat.reset()
    chat.set_task_update_status("task:1", "stale receipt")
    assert not chat.chat_items


def test_updates_cannot_change_frozen_approval_policy(qapp):
    previous = model_streams.get_handler(PRODUCTION_STREAM_HOOK)
    bridge = ConversationBridge(None)
    try:
        bridge.set_auto_approve(False)
        bridge._turn_active = True
        bridge.set_auto_approve(True)
        assert not bridge._approval_proxy._approve_all_session
        assert bridge._requested_auto_approve
    finally:
        bridge._turn_active = False
        bridge.shutdown()
        model_streams.unregister(PRODUCTION_STREAM_HOOK)
        if previous is not None:
            model_streams.register(PRODUCTION_STREAM_HOOK, previous)


def test_manual_approve_all_session_is_preserved_without_a_toolbar_change(qapp):
    previous = model_streams.get_handler(PRODUCTION_STREAM_HOOK)
    bridge = ConversationBridge(None)
    try:
        bridge.set_auto_approve(False)
        bridge._turn_active = True
        # The explicit approval-dialog decision remains authoritative.
        bridge._approval_proxy.set_approve_all_session(True)
        bridge._on_finished()
        assert bridge._approval_proxy._approve_all_session
    finally:
        bridge.shutdown()
        model_streams.unregister(PRODUCTION_STREAM_HOOK)
        if previous is not None:
            model_streams.register(PRODUCTION_STREAM_HOOK, previous)
