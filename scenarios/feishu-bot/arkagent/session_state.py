"""Shared Managed Agents Session status semantics."""
from __future__ import annotations

SESSION_TERMINAL_STATUSES = frozenset({"failed", "error", "terminated"})
SESSION_QUIESCENT_STATUSES = frozenset({"idle", "stopped", "completed"})
SESSION_RESUMABLE_STATUSES = frozenset({"idle", "running"})


def normalize_session_status(value: object) -> str:
    """Return a stable lowercase status from an MA Session payload value."""
    return str(value or "").strip().lower()


def session_status_from_payload(session: object) -> str:
    """Extract and normalize ``status`` from an MA Session response."""
    if not isinstance(session, dict):
        return ""
    return normalize_session_status(session.get("status"))


def is_session_terminal(status: object) -> bool:
    return normalize_session_status(status) in SESSION_TERMINAL_STATUSES


def is_session_quiescent(status: object) -> bool:
    return normalize_session_status(status) in SESSION_QUIESCENT_STATUSES


def is_session_resumable(status: object) -> bool:
    return normalize_session_status(status) in SESSION_RESUMABLE_STATUSES


__all__ = [
    "SESSION_QUIESCENT_STATUSES",
    "SESSION_RESUMABLE_STATUSES",
    "SESSION_TERMINAL_STATUSES",
    "is_session_quiescent",
    "is_session_resumable",
    "is_session_terminal",
    "normalize_session_status",
    "session_status_from_payload",
]
