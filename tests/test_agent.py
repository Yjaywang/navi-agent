"""Tests for agent.py — _load_system_prompt, get_engine, get_skill_registry, run_query."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent as agent_mod
from agent import _load_system_prompt, get_engine, get_skill_registry, run_query


# ── _load_system_prompt ──────────────────────────────────────────────────


class TestLoadSystemPrompt:
    def test_empty_context_clears_placeholder(self):
        result = _load_system_prompt("")
        assert "{memory_context}" not in result
        assert "{raw_context}" not in result
        # Should NOT contain the memory_context.md header when empty
        assert "What You Know About This User" not in result

    def test_context_injected_with_template(self):
        result = _load_system_prompt("User likes Python.")
        assert "User likes Python." in result
        # Should contain the memory_context.md header
        assert "What You Know About This User" in result
        assert "{memory_context}" not in result
        assert "{raw_context}" not in result

    def test_result_contains_system_prompt_content(self):
        result = _load_system_prompt("")
        # Verify the base system.md content is present
        assert "Memory System" in result


# ── get_engine ───────────────────────────────────────────────────────────


class TestGetEngine:
    @pytest.fixture(autouse=True)
    def _reset(self, reset_agent_singletons):
        pass

    @patch("agent.init_learning_tools")
    @patch("agent.init_memory_tools")
    @patch("agent.MemoryEngine")
    @patch("agent.load_config")
    def test_lazy_singleton(self, mock_config, MockEngine, mock_init_mem, mock_init_learn):
        mock_config.return_value = MagicMock()
        engine1 = get_engine()
        engine2 = get_engine()
        assert engine1 is engine2
        # MemoryEngine should only be constructed once
        MockEngine.assert_called_once()

    @patch("agent.init_learning_tools")
    @patch("agent.init_memory_tools")
    @patch("agent.MemoryEngine")
    @patch("agent.load_config")
    def test_inits_tools(self, mock_config, MockEngine, mock_init_mem, mock_init_learn):
        mock_config.return_value = MagicMock()
        get_engine()
        mock_init_mem.assert_called_once()
        mock_init_learn.assert_called_once()


# ── get_skill_registry ───────────────────────────────────────────────────


class TestGetSkillRegistry:
    @pytest.fixture(autouse=True)
    def _reset(self, reset_agent_singletons):
        pass

    @patch("agent.init_skill_tools")
    @patch("agent.load_skills_from_github", return_value=[])
    @patch("agent.install_builtins", return_value=[])
    @patch("agent.SkillRegistry")
    @patch("agent.get_engine")
    def test_lazy_singleton(self, mock_engine, MockRegistry, mock_builtins, mock_load, mock_init):
        mock_engine.return_value = MagicMock()
        reg1 = get_skill_registry()
        reg2 = get_skill_registry()
        assert reg1 is reg2
        MockRegistry.assert_called_once()

    @patch("agent.init_skill_tools")
    @patch("agent.load_skills_from_github", return_value=[])
    @patch("agent.install_builtins", return_value=[])
    @patch("agent.SkillRegistry")
    @patch("agent.get_engine")
    def test_calls_install_and_load(self, mock_engine, MockRegistry, mock_builtins, mock_load, mock_init):
        mock_engine.return_value = MagicMock()
        get_skill_registry()
        mock_builtins.assert_called_once()
        mock_load.assert_called_once()
        mock_init.assert_called_once()


# ── run_query ────────────────────────────────────────────────────────────


class TestRunQuery:
    @pytest.fixture(autouse=True)
    def _reset(self, reset_agent_singletons):
        pass

    def _setup_mocks(self, response_text="Hello from Claude"):
        """Create patchers for run_query dependencies."""
        patches = {}
        patches["config"] = patch("agent.load_config", return_value=MagicMock(
            model="claude-sonnet-4-6", max_turns=20
        ))

        mock_engine = MagicMock()
        mock_engine.retrieve_context.return_value = ""
        patches["get_engine"] = patch("agent.get_engine", return_value=mock_engine)

        mock_registry = MagicMock()
        mock_registry.get_server.return_value = MagicMock()
        mock_registry.get_tool_names.return_value = []
        patches["get_skill_registry"] = patch("agent.get_skill_registry", return_value=mock_registry)

        patches["set_pending"] = patch("agent.set_pending_attachments")
        patches["clear_pending"] = patch("agent.clear_pending_attachments")
        patches["get_files"] = patch("agent.get_response_files", return_value=[])
        patches["clear_files"] = patch("agent.clear_response_files")

        # Mock ClaudeSDKClient as async context manager
        mock_client_instance = AsyncMock()

        # Build mock response using custom classes that isinstance() will match
        class _FakeTextBlock:
            def __init__(self, text):
                self.text = text

        class _FakeAssistantMsg:
            def __init__(self, content):
                self.content = content

        mock_text_block = _FakeTextBlock(response_text)
        mock_assistant_msg = _FakeAssistantMsg([mock_text_block])

        async def mock_receive():
            yield mock_assistant_msg

        mock_client_instance.receive_response = mock_receive
        mock_client_instance.query = AsyncMock()

        mock_sdk = MagicMock()
        mock_sdk.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_sdk.return_value.__aexit__ = AsyncMock(return_value=False)
        patches["sdk"] = patch("agent.ClaudeSDKClient", mock_sdk)

        # Patch isinstance checks so our fake classes match
        patches["assistant_msg"] = patch("agent.AssistantMessage", _FakeAssistantMsg)
        patches["text_block"] = patch("agent.TextBlock", _FakeTextBlock)

        return patches, mock_engine

    async def test_basic_query_returns_text(self):
        patches, _ = self._setup_mocks("Hello!")
        with patches["config"], patches["get_engine"], patches["get_skill_registry"], \
             patches["set_pending"], patches["clear_pending"], patches["get_files"], \
             patches["clear_files"], patches["sdk"], patches["assistant_msg"], \
             patches["text_block"]:
            text, files = await run_query("hi")
        assert "Hello!" in text

    async def test_memory_retrieval_called(self):
        patches, mock_engine = self._setup_mocks()
        with patches["config"], patches["get_engine"], patches["get_skill_registry"], \
             patches["set_pending"], patches["clear_pending"], patches["get_files"], \
             patches["clear_files"], patches["sdk"], patches["assistant_msg"], \
             patches["text_block"]:
            await run_query("hi", user_id="user123")
        mock_engine.retrieve_context.assert_called_once_with("hi", "user123")

    async def test_error_marker_suppression(self):
        patches, _ = self._setup_mocks("Error: invalid api key provided")
        with patches["config"], patches["get_engine"], patches["get_skill_registry"], \
             patches["set_pending"], patches["clear_pending"], patches["get_files"], \
             patches["clear_files"], patches["sdk"], patches["assistant_msg"], \
             patches["text_block"]:
            text, _ = await run_query("hi")
        assert "抱歉" in text
        assert "invalid api key" not in text

    async def test_attachments_set_and_cleared(self):
        patches, _ = self._setup_mocks()
        attachments = {"att1": {"filename": "img.png", "content_type": "image/png", "data": b""}}
        with patches["config"], patches["get_engine"], patches["get_skill_registry"], \
             patches["set_pending"] as mock_set, patches["clear_pending"], \
             patches["get_files"], patches["clear_files"], patches["sdk"], \
             patches["assistant_msg"], patches["text_block"]:
            await run_query("look at this", attachments=attachments)
        mock_set.assert_called_once_with(attachments)
