"""Tests for sessions/manager.py."""

import time
from unittest.mock import patch

from sessions.manager import Session, SessionManager, Turn


class TestTurn:
    def test_creation(self):
        t = Turn(role="user", content="hi")
        assert t.role == "user"
        assert t.content == "hi"
        assert isinstance(t.timestamp, float)


class TestSession:
    def test_add_turn(self):
        s = Session(guild_id="g", channel_id="c", user_id="u")
        s.add_turn("user", "hello")
        s.add_turn("assistant", "hi")
        assert len(s.turns) == 2

    def test_get_history(self):
        s = Session(guild_id="g", channel_id="c", user_id="u")
        for i in range(25):
            s.add_turn("user", f"msg-{i}")
        history = s.get_history(max_turns=5)
        assert len(history) == 5
        assert history[-1]["content"] == "msg-24"

    def test_key(self):
        s = Session(guild_id="g", channel_id="c", user_id="u")
        assert s.key == ("g", "c", "u")

    def test_is_expired(self):
        s = Session(guild_id="g", channel_id="c", user_id="u")
        assert not s.is_expired(ttl_seconds=60)
        # Force last_active into the past
        s.last_active = time.time() - 120
        assert s.is_expired(ttl_seconds=60)


class TestSessionManager:
    def test_create_new(self):
        mgr = SessionManager(ttl_minutes=1)
        s = mgr.get_or_create("g", "c", "u")
        assert s.guild_id == "g"

    def test_return_existing(self):
        mgr = SessionManager(ttl_minutes=60)
        s1 = mgr.get_or_create("g", "c", "u")
        s2 = mgr.get_or_create("g", "c", "u")
        assert s1 is s2

    def test_recreate_on_expire(self):
        mgr = SessionManager(ttl_minutes=1)
        s1 = mgr.get_or_create("g", "c", "u")
        s1.last_active = time.time() - 120
        s2 = mgr.get_or_create("g", "c", "u")
        assert s2 is not s1

    def test_cleanup_expired(self):
        mgr = SessionManager(ttl_minutes=1)
        s = mgr.get_or_create("g", "c", "u")
        s.last_active = time.time() - 120
        removed = mgr.cleanup_expired()
        assert removed == 1

    def test_remove(self):
        mgr = SessionManager(ttl_minutes=60)
        mgr.get_or_create("g", "c", "u")
        mgr.remove("g", "c", "u")
        # Next call should create a new session
        s = mgr.get_or_create("g", "c", "u")
        assert len(s.turns) == 0
