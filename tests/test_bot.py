"""Tests for bot.py — _should_respond, _clean_mention, _build_history."""

from __future__ import annotations

from unittest.mock import MagicMock

import discord
import pytest

from bot import _should_respond, _clean_mention, _build_history
from tests.conftest import make_mock_message, AsyncIteratorMock


# ── _should_respond ──────────────────────────────────────────────────────


class TestShouldRespond:
    def test_ignores_self(self, mock_bot_user):
        msg = make_mock_message(author=mock_bot_user)
        assert _should_respond(msg, mock_bot_user) is False

    def test_ignores_other_bots(self, mock_bot_user):
        other_bot = MagicMock(spec=discord.User)
        other_bot.bot = True
        msg = make_mock_message(author=other_bot)
        assert _should_respond(msg, mock_bot_user) is False

    def test_responds_to_dm(self, mock_bot_user):
        dm_channel = MagicMock(spec=discord.DMChannel)
        author = MagicMock(spec=discord.User)
        author.bot = False
        msg = make_mock_message(author=author, channel=dm_channel)
        assert _should_respond(msg, mock_bot_user) is True

    def test_responds_to_mention(self, mock_bot_user):
        author = MagicMock(spec=discord.User)
        author.bot = False
        msg = make_mock_message(author=author, mentions=[mock_bot_user])
        assert _should_respond(msg, mock_bot_user) is True

    def test_responds_in_thread(self, mock_bot_user):
        thread = MagicMock(spec=discord.Thread)
        thread.owner_id = mock_bot_user.id
        author = MagicMock(spec=discord.User)
        author.bot = False
        msg = make_mock_message(author=author, channel=thread)
        assert _should_respond(msg, mock_bot_user) is True

    def test_ignores_regular_channel_message(self, mock_bot_user):
        author = MagicMock(spec=discord.User)
        author.bot = False
        channel = MagicMock(spec=discord.TextChannel)
        msg = make_mock_message(author=author, channel=channel, mentions=[])
        assert _should_respond(msg, mock_bot_user) is False


# ── _clean_mention ───────────────────────────────────────────────────────


class TestCleanMention:
    def test_removes_mention(self, mock_bot_user):
        text = f"<@{mock_bot_user.id}> hello there"
        assert _clean_mention(text, mock_bot_user) == "hello there"

    def test_no_mention_returns_original(self, mock_bot_user):
        text = "just a normal message"
        assert _clean_mention(text, mock_bot_user) == "just a normal message"

    def test_only_mention_returns_empty(self, mock_bot_user):
        text = f"<@{mock_bot_user.id}>"
        assert _clean_mention(text, mock_bot_user) == ""


# ── _build_history ───────────────────────────────────────────────────────


class TestBuildHistory:
    @pytest.fixture
    def bot_user(self, mock_bot_user):
        return mock_bot_user

    def _make_discord_msg(self, author, content):
        msg = MagicMock(spec=discord.Message)
        msg.author = author
        msg.content = content
        return msg

    async def test_labels_roles_correctly(self, bot_user):
        human = MagicMock(spec=discord.User)
        human.bot = False
        msgs = [
            self._make_discord_msg(human, "Hi bot"),
            self._make_discord_msg(bot_user, "Hello!"),
            self._make_discord_msg(human, "Thanks"),
        ]
        channel = MagicMock()
        channel.history = MagicMock(return_value=AsyncIteratorMock(msgs))

        result = await _build_history(channel, bot_user)
        # Last user message ("Thanks") should be popped
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "Hi bot"}
        assert result[1] == {"role": "assistant", "content": "Hello!"}

    async def test_empty_on_http_exception(self, bot_user):
        class _RaisingIterator:
            def __aiter__(self):
                return self
            async def __anext__(self):
                resp = MagicMock()
                resp.status = 500
                raise discord.HTTPException(resp, "error")

        channel = MagicMock()
        channel.history = MagicMock(return_value=_RaisingIterator())

        result = await _build_history(channel, bot_user)
        assert result == []

    async def test_strips_mention_from_history(self, bot_user):
        human = MagicMock(spec=discord.User)
        msgs = [
            self._make_discord_msg(human, f"<@{bot_user.id}> what's up"),
            self._make_discord_msg(human, "follow up"),
        ]
        channel = MagicMock()
        channel.history = MagicMock(return_value=AsyncIteratorMock(msgs))

        result = await _build_history(channel, bot_user)
        # Last user message popped, first should have mention stripped
        assert len(result) == 1
        assert result[0]["content"] == "what's up"

    async def test_skips_empty_messages(self, bot_user):
        human = MagicMock(spec=discord.User)
        msgs = [
            self._make_discord_msg(human, f"<@{bot_user.id}>"),  # becomes empty after strip
            self._make_discord_msg(human, "real message"),
        ]
        channel = MagicMock()
        channel.history = MagicMock(return_value=AsyncIteratorMock(msgs))

        result = await _build_history(channel, bot_user)
        # "real message" is the last user msg and gets popped; empty msg is skipped
        assert result == []
