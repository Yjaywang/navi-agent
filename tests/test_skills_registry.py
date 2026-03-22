"""Tests for skills/registry.py — mocks GitHubStore and claude_agent_sdk."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from skills.base import SkillMetadata, SkillRegistryManifest

VALID_SKILL_CODE = '''\
SKILL_NAME = "greet"
SKILL_DESCRIPTION = "Greet a user"
SKILL_VERSION = "1.0.0"
SKILL_PARAMETERS = {"name": str}

async def execute(args):
    return {"content": [{"type": "text", "text": f"Hello, {args['name']}!"}]}
'''


def _make_metadata(name: str = "greet", enabled: bool = True) -> SkillMetadata:
    return SkillMetadata(
        name=name,
        description="Greet",
        version="1.0.0",
        parameters={"name": "str"},
        source="user",
        enabled=enabled,
        installed_at=datetime.now(timezone.utc),
        installed_by="u1",
        path=f"skills/installed/{name}.py",
    )


@pytest.fixture
def registry(mock_store):
    # Mock claude_agent_sdk imports
    with patch("skills.registry.create_sdk_mcp_server") as mock_create, \
         patch("skills.registry.SdkMcpTool") as MockTool:
        MockTool.side_effect = lambda **kw: MagicMock(**kw)

        from skills.registry import SkillRegistry
        reg = SkillRegistry(mock_store)
        reg._mock_create_server = mock_create
        return reg


class TestRegisterSkill:
    def test_registers_valid_skill(self, registry):
        meta = _make_metadata()
        registry.register_skill(meta, VALID_SKILL_CODE)
        assert "greet" in [s.name for s in registry.get_skill_list()]

    def test_rejects_invalid_code(self, registry):
        meta = _make_metadata()
        with pytest.raises(ValueError, match="validation failed"):
            registry.register_skill(meta, "import os\n")


class TestUnregisterSkill:
    def test_removes_skill(self, registry, mock_store):
        meta = _make_metadata()
        registry.register_skill(meta, VALID_SKILL_CODE)

        # Mock read_registry for unregister persistence
        manifest = SkillRegistryManifest(version=1, skills=[meta])
        mock_store.get_file.return_value = (manifest.model_dump_json(), "sha1")

        registry.unregister_skill("greet")
        assert registry.get_skill("greet") is None

    def test_noop_for_unknown(self, registry):
        registry.unregister_skill("nonexistent")  # should not raise


class TestToggleSkill:
    def test_toggle_enabled(self, registry, mock_store):
        meta = _make_metadata()
        registry.register_skill(meta, VALID_SKILL_CODE)

        manifest = SkillRegistryManifest(version=1, skills=[meta])
        mock_store.get_file.return_value = (manifest.model_dump_json(), "sha1")

        registry.toggle_skill("greet", enabled=False)
        loaded = registry.get_skill("greet")
        assert loaded.metadata.enabled is False

    def test_raises_on_unknown(self, registry):
        with pytest.raises(KeyError):
            registry.toggle_skill("nonexistent", enabled=True)


class TestGetToolNames:
    def test_only_enabled(self, registry):
        m1 = _make_metadata("s1", enabled=True)
        m2 = _make_metadata("s2", enabled=False)
        registry.register_skill(m1, VALID_SKILL_CODE.replace('"greet"', '"s1"'))
        registry.register_skill(m2, VALID_SKILL_CODE.replace('"greet"', '"s2"'))

        # Manually disable s2 after registration
        registry._skills["s2"].metadata.enabled = False

        names = registry.get_tool_names()
        assert "mcp__skill-tools__s1" in names
        assert "mcp__skill-tools__s2" not in names


class TestGetServer:
    def test_returns_none_when_empty(self, registry):
        assert registry.get_server() is None

    def test_returns_server_with_skills(self, registry):
        meta = _make_metadata()
        registry.register_skill(meta, VALID_SKILL_CODE)
        server = registry.get_server()
        assert server is not None
