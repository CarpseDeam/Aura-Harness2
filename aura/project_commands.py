"""User-saved project commands and filesystem-only setup suggestions.

Definitions live in Aura's private data directory, alongside other local
workspace state. Discovering a command neither saves it nor executes it.
"""
from __future__ import annotations

import json
import ntpath
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid4, uuid5

from aura.agents.local_state import workspace_key
from aura.conversation.project_profile import ProjectProfile, detect_project_profile
from aura.conversation.tools.fs_write import atomic_write_bytes
from aura.paths import data_dir
from aura.project_env import resolve_workspace_cwd


@dataclass(frozen=True)
class ProjectCommand:
    name: str
    command: str
    cwd: str = "."
    env: Mapping[str, str] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        for key in ("id", "name", "command"):
            value = getattr(self, key)
            if not isinstance(value, str) or not value.strip() or "\0" in value:
                raise ValueError(f"{key.capitalize()} must be nonempty text without null characters.")
            object.__setattr__(self, key, value.strip())
        if not isinstance(self.cwd, str) or "\0" in self.cwd:
            raise ValueError("Working directory must be a workspace-relative path.")
        raw = self.cwd.strip() or "."
        if ntpath.isabs(raw) or ntpath.splitdrive(raw)[0]:
            raise ValueError("Working directory must be relative to the project.")
        relative = posixpath.normpath(raw.replace("\\", "/"))
        if relative == ".." or relative.startswith("../"):
            raise ValueError("Working directory must stay inside the project.")
        object.__setattr__(self, "cwd", relative)
        if not isinstance(self.env, Mapping):
            raise ValueError("Environment overrides must map names to text values.")
        copied = dict(self.env)
        for key, value in copied.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError("Environment names must contain letters, digits, or underscores.")
            if not isinstance(value, str) or "\0" in value:
                raise ValueError("Environment values must be text without null characters.")
        object.__setattr__(self, "env", MappingProxyType(copied))

    def working_directory(self, workspace_root: str | Path) -> Path:
        """Recheck containment at launch, including changed symbolic links."""
        directory = resolve_workspace_cwd(Path(workspace_root), self.cwd)
        if not directory.is_dir():
            raise ValueError(f"Working directory does not exist: {self.cwd}")
        return directory


@dataclass(frozen=True)
class ProjectCommandsState:
    commands: tuple[ProjectCommand, ...] = ()
    selected_id: str | None = None

    def __post_init__(self) -> None:
        commands = tuple(self.commands)
        if any(not isinstance(item, ProjectCommand) for item in commands):
            raise ValueError("Commands must contain project command definitions.")
        ids = {item.id for item in commands}
        if len(ids) != len(commands):
            raise ValueError("Project commands must have distinct IDs.")
        selected_id = self.selected_id
        if selected_id is None and commands:
            selected_id = commands[0].id
        if selected_id is not None and selected_id not in ids:
            raise ValueError("Selected command does not exist.")
        object.__setattr__(self, "commands", commands)
        object.__setattr__(self, "selected_id", selected_id)

    @property
    def selected(self) -> ProjectCommand | None:
        return next((item for item in self.commands if item.id == self.selected_id), None)


class ProjectCommandStoreError(RuntimeError):
    """Saved commands could not be loaded or saved without losing data."""


class ProjectCommandStore:
    def __init__(self, workspace_root: str | Path, *, state_root: Path | None = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        base = Path(state_root) if state_root is not None else data_dir() / "project_commands"
        self.path = base / "workspaces" / f"{workspace_key(self.workspace_root)}.json"

    def load(self) -> ProjectCommandsState:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ProjectCommandsState()
        except (OSError, UnicodeError) as exc:
            raise ProjectCommandStoreError("Could not read saved project commands.") from exc
        try:
            raw = json.loads(text)
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError("Unsupported project command file.")
            if not isinstance(raw.get("workspace"), str) or (
                workspace_key(raw["workspace"]) != workspace_key(self.workspace_root)
            ):
                raise ValueError("Project command file belongs to a different workspace.")
            entries = raw.get("commands")
            if not isinstance(entries, list):
                raise ValueError("Invalid command list.")
            commands = []
            for item in entries:
                if not isinstance(item, dict) or not all(key in item for key in ("id", "name", "command")):
                    raise ValueError("Invalid saved command.")
                commands.append(ProjectCommand(
                    id=item["id"], name=item["name"], command=item["command"],
                    cwd=item.get("cwd", "."), env=item.get("env", {}),
                ))
            return ProjectCommandsState(tuple(commands), raw.get("selected_id"))
        except (ValueError, TypeError, OSError) as exc:
            raise ProjectCommandStoreError(
                "Saved project commands are invalid. The existing file has been preserved."
            ) from exc

    def save(self, state: ProjectCommandsState) -> None:
        # A damaged or newer-format file must never be silently replaced by a
        # blank UI state. Read first so the user can recover the original file.
        self.load()
        for command in state.commands:
            resolve_workspace_cwd(self.workspace_root, command.cwd)
        payload = {
            "version": 1,
            "workspace": str(self.workspace_root),
            "selected_id": state.selected_id,
            "commands": [
                {"id": item.id, "name": item.name, "command": item.command,
                 "cwd": item.cwd, "env": dict(item.env)}
                for item in state.commands
            ],
        }
        try:
            atomic_write_bytes(self.path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        except OSError as exc:
            raise ProjectCommandStoreError("Could not save project commands.") from exc


_SCRIPT_LABELS = {
    "dev": "Start server", "start": "Start app", "test": "Run tests",
    "build": "Build", "lint": "Lint", "typecheck": "Check types",
}
_VALIDATION_LABELS = {
    "pytest": ("Run tests", "python -m pytest"),
    "ruff check": ("Lint", "python -m ruff check"),
    "mypy": ("Check types", "python -m mypy"),
    "cargo test": ("Run tests", "cargo test"),
    "cargo build": ("Build", "cargo build"),
    "go test ./...": ("Run tests", "go test ./..."),
}


def _node_manager(profile: ProjectProfile) -> str:
    if profile.package_manager in {"npm", "pnpm", "yarn"}:
        return profile.package_manager
    # The existing profile reports one primary manager in mixed projects.
    if "pnpm-lock.yaml" in profile.lockfiles:
        return "pnpm"
    if "yarn.lock" in profile.lockfiles:
        return "yarn"
    return "npm"


def discover_project_commands(workspace_root: str | Path) -> list[ProjectCommand]:
    """Offer concrete existing entry points without executing project code."""
    root = Path(workspace_root).resolve()
    try:
        profile = detect_project_profile(root)
    except (OSError, ValueError, TypeError, AttributeError):
        return []

    suggestions: list[ProjectCommand] = []
    seen: set[str] = set()

    def add(name: str, command: str) -> None:
        if command in seen:
            return
        seen.add(command)
        suggestions.append(ProjectCommand(
            id=uuid5(NAMESPACE_URL, f"aura:project-command:{command}").hex,
            name=name, command=command,
        ))

    scripts = list(profile.node_scripts)
    order = list(_SCRIPT_LABELS)
    scripts.sort(key=lambda item: order.index(item[0]) if item[0] in order else len(order))
    for name, body in scripts:
        # Script names become shell arguments; only portable literal names are
        # suggested. Users can still save any explicit command themselves.
        if not body.strip() or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_:.-]*", name):
            continue
        add(_SCRIPT_LABELS.get(name, name.replace(":", " · ")), f"{_node_manager(profile)} run {name}")

    if "python" in profile.project_types:
        try:
            packages = sorted(root.iterdir())
        except OSError:
            packages = []
        for package in packages:
            if package.name.isidentifier() and (package / "__init__.py").is_file() and (
                package / "__main__.py"
            ).is_file():
                add(f"Launch {package.name}", f"python -m {package.name}")
        if (root / "__main__.py").is_file():
            add("Launch app", "python .")

    for command in profile.validation_commands:
        if command in _VALIDATION_LABELS:
            label, concrete = _VALIDATION_LABELS[command]
            add(label, concrete)
    return suggestions
