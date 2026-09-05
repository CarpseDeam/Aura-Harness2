"""Saved cards survive chat replay and follow current native Workflow state."""

from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QInputDialog
from test_workflow_authoring import authoring_setup, editable_spec, review_spec

from aura.agents.local_state import AgentPermission
from aura.agents.models import AgentThinking
from aura.agents.team_spec import HandoffSpec, NewAgentSpec, OccurrenceSpec, WorkflowSpec
from aura.conversation.chat_transcript import normalize_chat_item, workflow_item
from aura.conversation.history import History
from aura.conversation.persistence import load_conversation, save_conversation
from aura.gui.cards.workflow_card import WorkflowCard
from aura.gui.chat_view import ChatView
from aura.gui.conv_persistence import ConversationPersistence
from aura.gui.widgets.aura_glow import AuraPhaseDriver
from aura.gui.workflow_chat_controller import WorkflowChatController


class Signals(QObject):
    workflowAuthored = Signal(object)
    started = Signal()
    finished = Signal()
    workflows_changed = Signal()
    requested_read_only = False

    def is_running(self):
        return False


def test_saved_reference_replays_current_graph_and_grants_without_execution(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    service, _ = authoring_setup(tmp_path)
    signals = Signals()
    owner = SimpleNamespace(
        workflows_changed=signals.workflows_changed,
        refresh=lambda: None,
        capture_workflow_authoring=lambda: service,
    )
    chat = ChatView(AuraPhaseDriver(app))
    runs = []
    controller = WorkflowChatController(
        bridge=signals, chat=chat, owner=owner, submit_run=lambda *args: runs.append(args)
    )
    chat.add_user("Create a reusable Workflow")
    saved = service.create(review_spec())
    signals.workflowAuthored.emit(saved)
    chat.append_content("Saved.")
    chat.assistant_done()
    chat.begin_assistant()
    updated = service.update(
        saved.document.graph.graph_id,
        saved.document.revision,
        replace(editable_spec(saved.document), name="Refined review"),
    )
    signals.workflowAuthored.emit(updated)
    chat.append_content("Updated.")
    chat.assistant_done()
    assert len([item for item in chat.chat_items if item["kind"] == "workflow"]) == 1

    history = History()
    history.append_user_text("Create a reusable Workflow")
    path = save_conversation(history, tmp_path, model="model", thinking="off", chat_items=chat.chat_items)
    loaded = load_conversation(path)
    entry = saved.document.agents[0]
    service.local_state.set_permission(entry.agent_id, AgentPermission.READ_ONLY)
    service.agents.update(replace(entry.definition, thinking=AgentThinking.OFF))
    service.workflows.save(updated.document.graph.with_name("Current saved name"))
    chat.reset()
    replay = SimpleNamespace(_chat=chat, _active_replay_id=0)
    ConversationPersistence._render_chat_items(replay, loaded.chat_items)
    assert runs == []
    assert len(controller.cards) == 1
    card = controller.cards[0]
    assert card.title.text() == "Current saved name"
    assert "Thinking: Off" in card.details.text()
    assert "Read only" in card.details.text()
    assert not card.saved.can_undo  # external change invalidated the old undo history
    prompts = []

    def task_dialog(dialog):
        prompts.append(dialog.textValue())
        assert dialog.width() >= 680
        dialog.setTextValue(f"Fresh task {len(prompts)}")
        return QInputDialog.DialogCode.Accepted

    monkeypatch.setattr(QInputDialog, "exec", task_dialog)
    card.run_button.click()
    card.run_button.click()
    assert prompts == ["", ""]
    assert runs == [(saved.document.graph.graph_id, "Fresh task 1"), (saved.document.graph.graph_id, "Fresh task 2")]
    chat.reset()
    missing = workflow_item("missingworkflow")
    ConversationPersistence._render_chat_items(replay, [missing])
    assert controller.cards == ()
    assert len(runs) == 2
    assert not any(item["kind"] == "error" for item in chat.chat_items)
    assert normalize_chat_item({"kind": "workflow", "workflow_id": "../unsafe"}) is None
    chat.close()


def test_preview_shrinks_after_undo_without_resizing_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    service, _ = authoring_setup(tmp_path)

    def spec(count):
        aliases = [f"agent{i}" for i in range(count)]
        chain = ["task", *aliases, "result"]
        return WorkflowSpec(
            f"Review {count}",
            "Review stages",
            new_agents=tuple(NewAgentSpec(a, a, "Review changes", "Review the task") for a in aliases),
            occurrences=tuple(OccurrenceSpec(a, a, "Review the task") for a in aliases),
            handoffs=tuple(HandoffSpec(a, b) for a, b in zip(chain, chain[1:])),
        )

    card = WorkflowCard(service.create(spec(4)))
    card.resize(1200, 700)
    card.show()
    app.processEvents()
    tall = card.preview.height()
    card.set_saved(service.create(spec(3)))
    app.processEvents()
    assert card.preview.height() < tall / 2
    assert card.preview.height() == card.preview.heightForWidth(card.preview.width())
    card.close()
