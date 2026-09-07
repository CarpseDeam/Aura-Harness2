"""One user-owned project process, independent of the conversation runtime."""

from __future__ import annotations

import base64
import codecs
import os
import queue
import select
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from aura.config import get_subprocess_kwargs
from aura.project_commands import ProjectCommand
from aura.python_env import detect_project_python_env
from aura.shell.powershell_session import build_child_environment, resolve_powershell
from aura.shell.process_tree import terminate_process_tree

MAX_OUTPUT_CHARS = 65_536
_CHUNK_SIZE = 4096
_STOP_GRACE_SECONDS = 0.8


@dataclass
class _Execution:
    root: Path
    command: ProjectCommand
    cwd: Path
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancel: threading.Event = field(default_factory=threading.Event)
    launched: threading.Event = field(default_factory=threading.Event)
    chunks: queue.Queue[str] = field(default_factory=lambda: queue.Queue(maxsize=16))
    exit_code: int | None = None
    error: str = ""
    stopped: bool = False


def _shell_args(command: str) -> list[str]:
    if os.name != "nt":
        return ["/bin/sh", "-c", command]
    # EncodedCommand preserves user quotes/newlines. Capture native exit codes
    # instead of PowerShell's usual conversion of every failure to exit code 1.
    script = (
        "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
        "$global:LASTEXITCODE = 0\n"
        "try {\n" + command + "\n$__aura_success = $?\n"
        "} catch { $_ | Out-String | Write-Error; exit 1 }\n"
        "if ($global:LASTEXITCODE -ne 0) { exit $global:LASTEXITCODE }\n"
        "if (-not $__aura_success) { exit 1 }\nexit 0\n"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [resolve_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-OutputFormat", "Text", "-EncodedCommand", encoded]


def _environment(root: Path, cwd: Path, command: ProjectCommand) -> dict[str, str]:
    env_root = cwd if detect_project_python_env(cwd).has_venv else root
    env = build_child_environment(env_root)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    for name, value in command.env.items():
        if os.name == "nt":
            for existing in list(env):
                if existing.lower() == name.lower():
                    env.pop(existing)
        env[name] = value
    return env


def _read_available(pipe: Any) -> bytes:
    """Read a pipe without a second thread or an uncancellable blocking read."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        available = wintypes.DWORD()
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(pipe.fileno()))
        if not ctypes.windll.kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            error = ctypes.windll.kernel32.GetLastError()
            if error in (109, 232):  # broken pipe / pipe is being closed
                return b""
            raise ctypes.WinError(error)
        if not available.value:
            return b""
        return os.read(pipe.fileno(), min(_CHUNK_SIZE, available.value))
    readable, _, _ = select.select([pipe], [], [], 0)
    return os.read(pipe.fileno(), _CHUNK_SIZE) if readable else b""


def _queue_text(run: _Execution, text: str) -> None:
    if not text:
        return
    while True:
        try:
            run.chunks.put(text, timeout=0.05)
            return
        except queue.Full:
            if run.cancel.is_set():
                return  # Shutdown cannot depend on the GUI draining output.


def _signal_group(process: subprocess.Popen[bytes], sig: int) -> bool:
    try:
        # start_new_session makes the initial PID the owned group ID, even
        # after the shell itself exits and only its descendants remain.
        os.killpg(process.pid, sig)
        return True
    except ProcessLookupError:
        return False


def _finish_tree(process: subprocess.Popen[bytes], job: Any, stopping: bool) -> None:
    if os.name == "nt" and job is None:
        # taskkill needs the live wrapper's lineage. Sending CTRL_BREAK first
        # can exit that wrapper while descendants survive, losing ownership.
        if process.poll() is None:
            terminate_process_tree(process, None, [])
        return
    if stopping and process.poll() is None:
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                _signal_group(process, signal.SIGTERM)
            process.wait(timeout=_STOP_GRACE_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if os.name == "nt":
        job.terminate()
    elif _signal_group(process, signal.SIGTERM):
        deadline = time.monotonic() + _STOP_GRACE_SECONDS
        while time.monotonic() < deadline:
            process.poll()
            if not _signal_group(process, 0):
                return
            time.sleep(0.02)
        _signal_group(process, signal.SIGKILL)


def _execute(run: _Execution) -> None:
    process: subprocess.Popen[bytes] | None = None
    job = None
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        if run.cancel.is_set():
            run.stopped = True
            return
        kwargs: dict[str, Any] = get_subprocess_kwargs()
        if os.name == "nt":
            kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(
            _shell_args(run.command.command), cwd=str(run.cwd),
            env=_environment(run.root, run.cwd, run.command),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, **kwargs,
        )
        if os.name == "nt":
            from aura.win_job import WindowsJob

            job = WindowsJob.try_assign(process)
        run.launched.set()
        while process.poll() is None and not run.cancel.is_set():
            chunk = _read_available(process.stdout)
            if chunk:
                _queue_text(run, decoder.decode(chunk))
            else:
                run.cancel.wait(0.03)
        # A Stop racing an already completed command preserves its real exit.
        run.stopped = run.cancel.is_set() and process.poll() is None
        _finish_tree(process, job, run.stopped)
        if process.stdout is not None and not process.stdout.closed:
            while chunk := _read_available(process.stdout):
                _queue_text(run, decoder.decode(chunk))
        _queue_text(run, decoder.decode(b"", final=True))
        run.exit_code = process.wait(timeout=1)
    except Exception as exc:
        run.error = str(exc)
    finally:
        if process is not None:
            terminate_process_tree(process, job, [])


class ProjectCommandRunner(QObject):
    """Qt-side lifetime and bounded output for one command at a time.

    All public methods/properties belong to the GUI thread. The worker owns
    subprocess operations only; the timer delivers output and state to widgets.
    """

    started = Signal(str, object, str)  # accepted run ID, command snapshot, cwd
    output = Signal(str, str)  # run ID, plain text chunk
    finished = Signal(str, object)  # run ID, exit code (None on launch failure)
    state_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._run: _Execution | None = None
        self._thread: threading.Thread | None = None
        self._state = "idle"
        self._output_tail = ""
        self._restart_requested = False
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll)

    @property
    def active(self) -> bool:
        return self._thread is not None

    @property
    def state(self) -> str:
        return self._state

    @property
    def current_command(self) -> ProjectCommand | None:
        return self._run.command if self._run else None

    @property
    def run_id(self) -> str:
        return self._run.id if self._run else ""

    @property
    def exit_code(self) -> int | None:
        return self._run.exit_code if self._run else None

    @property
    def output_tail(self) -> str:
        return self._output_tail

    def start(self, workspace_root: Path, command: ProjectCommand) -> bool:
        """Accept a launch; False means another command owns the runner."""
        if self.active or self._closed:
            return False
        root = Path(workspace_root).resolve()
        try:
            cwd = command.working_directory(root)
        except (OSError, ValueError) as exc:
            run = _Execution(root, command, root, error=str(exc))
        else:
            run = _Execution(root, command, cwd)
        self._run = run
        self._output_tail = ""
        self._restart_requested = False
        # Mark ownership before emitting signals, preventing a double launch
        # even if a started/state callback synchronously attempts another run.
        if run.error:
            self._set_state("starting")
            self.started.emit(run.id, command, str(run.cwd))
            self._complete(run)
        else:
            self._thread = threading.Thread(target=_execute, args=(run,), name="aura-project-command", daemon=True)
            self._thread.start()
            self._set_state("starting")
            self.started.emit(run.id, command, str(run.cwd))
            if self.active and not self._closed:
                self._timer.start()
        return True

    def stop(self) -> None:
        """Request process-tree shutdown without waiting on the GUI thread."""
        self._restart_requested = False
        if self.active and self._run is not None:
            self._set_state("stopping")
            self._run.cancel.set()

    def restart(self) -> bool:
        if self._closed or self._run is None:
            return False
        if self.active:
            self.stop()
            self._restart_requested = True
            return True
        return self.start(self._run.root, self._run.command)

    def shutdown(self) -> None:
        """Finish cleanup before the owner/window goes away; idempotent."""
        self._closed = True
        self._restart_requested = False
        self.stop()
        if self._thread is not None:
            self._thread.join()
        self._poll()
        self._timer.stop()

    def _set_state(self, state: str) -> None:
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)

    def _append_output(self, run: _Execution, text: str) -> None:
        self._output_tail = (self._output_tail + text)[-MAX_OUTPUT_CHARS:]
        self.output.emit(run.id, text)

    def _poll(self) -> None:
        run, thread = self._run, self._thread
        if run is None or thread is None:
            return
        if run.launched.is_set() and self._state == "starting":
            self._set_state("running")
        chunks = []
        for _ in range(16):
            try:
                chunks.append(run.chunks.get_nowait())
            except queue.Empty:
                break
        if chunks:
            self._append_output(run, "".join(chunks))
        if not thread.is_alive() and run.chunks.empty():
            thread.join()
            self._thread = None
            self._timer.stop()
            self._complete(run)

    def _complete(self, run: _Execution) -> None:
        if run.error:
            self._append_output(run, f"\nCould not run {run.command.name}: {run.error}\n")
        state = "stopped" if run.stopped else "failed" if run.error or run.exit_code != 0 else "succeeded"
        self._set_state(state)
        self.finished.emit(run.id, run.exit_code)
        restart, self._restart_requested = self._restart_requested, False
        if restart and not self._closed and not self.active:
            self.start(run.root, run.command)


__all__ = ["MAX_OUTPUT_CHARS", "ProjectCommandRunner"]
