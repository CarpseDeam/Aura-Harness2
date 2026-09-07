"""Ordered user corrections for one task; no Qt, provider, or execution policy.

The bridge owns submission, each loop owns a cursor, and only the root cursor
can close the task on completion. Submission, tool admission, and completion
share a lock. A tool admitted before an update is already started; executor
jobs must claim admission inside their worker, never when queued.
"""
from __future__ import annotations

import copy
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

UPDATE_KEY = "aura_task_update"
PENDING = "Pending next model boundary"
IN_CONTEXT = "Added to model context · following it is unverified"
NOT_DELIVERED = "Task ended before delivery · copy to resubmit"
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnUpdate:
    id: str
    text: str

    def message(self, status: str) -> dict[str, Any]:
        return {
            "role": "user", "content": self.text,
            "aura_literal_composer_text": self.text,
            UPDATE_KEY: {"id": self.id, "status": status},
        }


class TurnUpdates:
    """An append-only task log, broadcast independently to all participating loops."""

    def __init__(
        self, on_status: Callable[[str, str], None] | None = None,
        *, preceding: list[dict[str, Any]] | None = None,
    ) -> None:
        self.task_id = uuid4().hex
        self._lock = threading.RLock()
        self._updates: list[TurnUpdate] = []
        self._statuses: dict[str, str] = {}
        self._open = True
        self._on_status = on_status
        self._discarded = False
        for message in preceding or ():
            update = TurnUpdate(message[UPDATE_KEY]["id"], message["content"])
            self._updates.append(update)
            self._statuses[update.id] = message[UPDATE_KEY].get("status", PENDING)
        self._preceding_count = len(self._updates)

    def submit(self, text: str) -> TurnUpdate | None:
        with self._lock:
            if not self._open:
                return None
            update = TurnUpdate(f"{self.task_id}:{len(self._updates) + 1}", text)
            self._updates.append(update)
            self._statuses[update.id] = PENDING
            return update

    def cursor(self, *, root: bool = False) -> UpdateCursor:
        return UpdateCursor(self, root=root)

    def close(self) -> None:
        """Refuse further submissions, retaining every accepted correction."""
        with self._lock:
            self._open = False
            for update in self._updates:
                if self._statuses[update.id] == PENDING:
                    self._set_status(update, NOT_DELIVERED)

    def invalidate(self) -> None:
        """An explicit reset/workspace switch detached this task's conversation."""
        with self._lock:
            self.close()
            self._discarded = True

    def _set_status(self, update: TurnUpdate, status: str) -> None:
        if self._statuses[update.id] == status:
            return
        self._statuses[update.id] = status
        if self._on_status is not None:
            try:
                self._on_status(update.id, status)
            except Exception:
                # Presentation cannot consume or interrupt runtime delivery.
                _log.exception("Could not present task update receipt")

    def persist(self, history: Any) -> None:
        """Root worker, after quiescence: retain even updates interrupted by Stop/error."""
        with self._lock:
            if self._discarded:
                return
            present = {
                m[UPDATE_KEY]["id"]: m for m in history.messages if UPDATE_KEY in m
            }
            for update in self._updates:
                status = self._statuses[update.id]
                if update.id in present:
                    present[update.id][UPDATE_KEY]["status"] = status
                else:
                    history.messages.append(update.message(status))


class UpdateCursor:
    """One loop's position. Reading here never consumes another loop's updates."""

    def __init__(self, updates: TurnUpdates, *, root: bool = False) -> None:
        self.updates = updates
        self._position = updates._preceding_count if root else 0
        self._root = root

    def pending(self) -> bool:
        with self.updates._lock:
            return self._position < len(self.updates._updates)

    def admit_tool(self) -> bool:
        """Linearize the start of a tool with respect to incoming corrections."""
        with self.updates._lock:
            return self.updates._open and not self.pending()

    def append_to(self, history: Any) -> None:
        """Only at a complete message boundary, immediately before a model request."""
        with self.updates._lock:
            if not self.updates._open:
                return
            for update in self.updates._updates[self._position:]:
                history.messages.append(copy.deepcopy(update.message(IN_CONTEXT)))
            self._position = len(self.updates._updates)
            # A retried root already holds preceding corrections in History;
            # acknowledge those too when it reaches its first model request.
            for update in self.updates._updates[:self._position]:
                self.updates._set_status(update, IN_CONTEXT)

    def finish(self) -> bool:
        """Completion wins only if this loop has incorporated all accepted updates."""
        with self.updates._lock:
            if self.pending():
                return False
            if self._root:
                self.updates._open = False
            return True
