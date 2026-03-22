"""SessionManager — per-user/thread conversation state with TTL cleanup."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal

log = logging.getLogger(__name__)

# Session key: (guild_id, channel_id, user_id)
SessionKey = tuple[str, str, str]


@dataclass
class Turn:
    """A single conversation turn."""

    role: Literal["user", "assistant"]
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class Session:
    """In-memory conversation session."""

    guild_id: str
    channel_id: str
    user_id: str
    turns: list[Turn] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    @property
    def key(self) -> SessionKey:
        return (self.guild_id, self.channel_id, self.user_id)

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append(Turn(role=role, content=content))
        self.last_active = time.time()

    def get_history(self, max_turns: int = 20) -> list[dict[str, str]]:
        """Return recent turns as list of {role, content} dicts."""
        recent = self.turns[-max_turns:]
        return [{"role": t.role, "content": t.content} for t in recent]

    def is_expired(self, ttl_seconds: float) -> bool:
        return (time.time() - self.last_active) > ttl_seconds


class SessionManager:
    """Manages per-user/channel conversation sessions with TTL cleanup."""

    def __init__(self, ttl_minutes: int = 60) -> None:
        self._sessions: dict[SessionKey, Session] = {}
        self._ttl_seconds: float = ttl_minutes * 60

    def get_or_create(
        self, guild_id: str, channel_id: str, user_id: str
    ) -> Session:
        key = (guild_id, channel_id, user_id)
        session = self._sessions.get(key)
        if session is not None and not session.is_expired(self._ttl_seconds):
            return session
        if session is not None:
            log.info("Session expired for %s, creating new", key)
            del self._sessions[key]
        session = Session(guild_id=guild_id, channel_id=channel_id, user_id=user_id)
        self._sessions[key] = session
        return session

    def cleanup_expired(self) -> int:
        """Remove all expired sessions. Returns count removed."""
        expired_keys = [
            k for k, s in self._sessions.items() if s.is_expired(self._ttl_seconds)
        ]
        for k in expired_keys:
            del self._sessions[k]
        if expired_keys:
            log.info("Cleaned up %d expired sessions", len(expired_keys))
        return len(expired_keys)

    def remove(self, guild_id: str, channel_id: str, user_id: str) -> None:
        key = (guild_id, channel_id, user_id)
        self._sessions.pop(key, None)
