"""UI and project setup failures encountered in the live Workflow walkthrough."""

import subprocess

from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication, QLabel

from aura.git_ops import ensure_aura_gitignored
from aura.gui.cards.user_card import UserCard


def test_task_placeholder_does_not_hide_the_rest_of_the_request():
    app = QApplication.instance() or QApplication([])
    text = 'Return "Hello from <name>!". Add tests and retain the changes.'
    card = UserCard(text)
    body = [label for label in card.findChildren(QLabel) if label.text() != "You"][0]
    document = QTextDocument()
    document.setHtml(body.text())
    assert document.toPlainText() == text
    card.close()
    app.processEvents()


def test_hazard_database_is_ignored_but_project_workflows_are_trackable(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    ensure_aura_gitignored(tmp_path)
    private = [".aura/hazards.db", ".aura/hazards.db-wal", ".aura/hazards.db-shm"]
    for name in private + [".aura/agents/workflows/saved.json"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test")
    result = subprocess.run(["git", "check-ignore", *private], cwd=tmp_path, check=True, capture_output=True, text=True)
    assert set(result.stdout.splitlines()) == set(private)
    tracked = subprocess.run(
        ["git", "check-ignore", ".aura/agents/workflows/saved.json"], cwd=tmp_path, capture_output=True
    )
    assert tracked.returncode == 1
