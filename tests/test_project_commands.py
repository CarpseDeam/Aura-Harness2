from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from aura.project_commands import (
    ProjectCommand,
    ProjectCommandsState,
    ProjectCommandStore,
    ProjectCommandStoreError,
    discover_project_commands,
)


def test_store_roundtrip_workspace_isolation_and_frozen_environment(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    env = {"PORT": "8000", "API_TOKEN": "local-only"}
    launch = ProjectCommand("Start server", "python -m app", env=env)
    tests = ProjectCommand("Run tests", "python -m pytest")
    state = ProjectCommandsState((launch, tests), tests.id)
    store = ProjectCommandStore(root, state_root=tmp_path / "private")
    store.save(state)
    env["PORT"] = "9000"

    restored = ProjectCommandStore(root / ".", state_root=tmp_path / "private").load()
    assert restored == state
    assert restored.selected == tests
    assert restored.commands[0].env["PORT"] == "8000"
    with pytest.raises(TypeError):
        restored.commands[0].env["PORT"] = "9001"
    assert list(root.iterdir()) == []
    assert ProjectCommandStore(tmp_path / "other", state_root=tmp_path / "private").load().commands == ()


def test_primary_selection_and_stable_edit_identity() -> None:
    first = ProjectCommand("Start", "npm start")
    second = ProjectCommand("Tests", "npm test")
    assert ProjectCommandsState((first, second)).selected == first
    updated = replace(first, command="npm run dev")
    assert updated.id == first.id
    assert ProjectCommandsState((updated, second), first.id).selected == updated
    with pytest.raises(ValueError, match="distinct IDs"):
        ProjectCommandsState((first, first))
    with pytest.raises(ValueError, match="does not exist"):
        ProjectCommandsState((first,), "missing")


@pytest.mark.parametrize("damage", [
    "{bad json", "[]", '{"version": 77}',
    '{"version": 1, "workspace": "/wrong", "commands": []}',
])
def test_invalid_state_is_reported_and_not_overwritten(tmp_path: Path, damage: str) -> None:
    store = ProjectCommandStore(tmp_path, state_root=tmp_path / "private")
    store.path.parent.mkdir(parents=True)
    store.path.write_text(damage, encoding="utf-8")
    with pytest.raises(ProjectCommandStoreError, match="preserved"):
        store.load()
    with pytest.raises(ProjectCommandStoreError):
        store.save(ProjectCommandsState())
    assert store.path.read_text(encoding="utf-8") == damage


@pytest.mark.parametrize("entry", [
    {"name": "Test", "command": "pytest"},
    {"id": "a", "name": "Test", "command": None},
    {"id": "a", "name": "Test", "command": "pytest", "env": {"BAD=NAME": "value"}},
    {"id": "a", "name": "Test", "command": "pytest", "cwd": "../other"},
])
def test_malformed_saved_entry_does_not_become_a_runnable_command(tmp_path: Path, entry: dict) -> None:
    store = ProjectCommandStore(tmp_path, state_root=tmp_path / "private")
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps({"version": 1, "workspace": str(tmp_path), "commands": [entry]}))
    with pytest.raises(ProjectCommandStoreError):
        store.load()


def test_failed_save_preserves_previous_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ProjectCommandStore(tmp_path, state_root=tmp_path / "private")
    original = ProjectCommandsState((ProjectCommand("Tests", "pytest"),))
    store.save(original)

    def fail_write(*args: object) -> None:
        raise PermissionError("File locked")

    monkeypatch.setattr("aura.project_commands.atomic_write_bytes", fail_write)
    with pytest.raises(ProjectCommandStoreError, match="save"):
        store.save(ProjectCommandsState())
    assert store.load() == original


@pytest.mark.parametrize("cwd", ["../other", "/tmp", "C:\\outside", "C:relative", "\\\\host\\share", "a/../../b"])
def test_cwd_must_be_inside_workspace(cwd: str) -> None:
    with pytest.raises(ValueError):
        ProjectCommand("Tests", "pytest", cwd=cwd)


def test_cwd_resolution_and_launch_recheck(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    nested = root / "server"
    nested.mkdir()
    command = ProjectCommand("Start", "python -m app", cwd=".\\server")
    assert command.cwd == "server"
    assert command.working_directory(root) == nested
    nested.rmdir()
    with pytest.raises(ValueError, match="does not exist"):
        command.working_directory(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        nested.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable")
    with pytest.raises(ValueError, match="escape"):
        command.working_directory(root)


@pytest.mark.parametrize("env", [{"BAD=NAME": "x"}, {"": "x"}, {"1BAD": "x"}, {"OK": None}, {"OK": "x\0y"}])
def test_environment_overrides_reject_invalid_process_values(env: dict) -> None:
    with pytest.raises(ValueError):
        ProjectCommand("Tests", "pytest", env=env)


@pytest.mark.parametrize("lock,manager", [("package-lock.json", "npm"), ("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn")])
def test_node_suggestions_reuse_package_scripts_without_running_or_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock: str, manager: str,
) -> None:
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path / "private"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"scripts": {
        "test": "echo tests", "dev": "touch NEVER_EXECUTE", "build": "echo build",
        "custom:check": "echo custom", "bad;name": "echo unsafe argument",
    }}))
    (root / lock).write_text("")
    suggestions = discover_project_commands(root)
    assert [(item.name, item.command) for item in suggestions] == [
        ("Start server", f"{manager} run dev"), ("Run tests", f"{manager} run test"),
        ("Build", f"{manager} run build"), ("custom · check", f"{manager} run custom:check"),
    ]
    assert [item.id for item in discover_project_commands(root)] == [item.id for item in suggestions]
    assert not (root / "NEVER_EXECUTE").exists()
    assert not (tmp_path / "private").exists()


def test_python_launch_requires_real_module_entry_and_omits_placeholder(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n[tool.ruff]\n'
    )
    (tmp_path / "launch.py").write_text("raise RuntimeError('do not run')")
    (tmp_path / "main.py").write_text("raise RuntimeError('do not run')")
    assert all(not item.name.startswith("Launch") for item in discover_project_commands(tmp_path))
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text("raise RuntimeError('do not import')")
    suggestions = discover_project_commands(tmp_path)
    assert [(item.name, item.command) for item in suggestions] == [
        ("Launch sample", "python -m sample"), ("Run tests", "python -m pytest"),
        ("Lint", "python -m ruff check"),
    ]


def test_mixed_project_uses_node_lockfile_even_when_profile_prefers_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "mixed"\n')
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "vite"}}')
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert discover_project_commands(tmp_path)[0].command == "pnpm run dev"


def test_other_supported_project_validation_and_no_launch_guess(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"\n')
    (tmp_path / "go.mod").write_text("module app\n")
    assert [item.command for item in discover_project_commands(tmp_path)] == [
        "cargo test", "cargo build", "go test ./...",
    ]


@pytest.mark.parametrize("contents", ["{bad", "[]", "null"])
def test_broken_manifest_does_not_break_setup(tmp_path: Path, contents: str) -> None:
    (tmp_path / "package.json").write_text(contents)
    assert discover_project_commands(tmp_path) == []
