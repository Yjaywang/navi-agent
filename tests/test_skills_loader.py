"""Tests for skills/loader.py — validate_skill_code and GitHub I/O."""

import json
from unittest.mock import MagicMock

import pytest

from skills.loader import validate_skill_code, read_registry, save_skill_to_github
from skills.base import SkillMetadata, SkillRegistryManifest

VALID_SKILL = '''\
SKILL_NAME = "greet"
SKILL_DESCRIPTION = "Greet a user"
SKILL_VERSION = "1.0.0"
SKILL_PARAMETERS = {"name": str}

async def execute(args):
    return {"content": [{"type": "text", "text": f"Hello, {args['name']}!"}]}
'''


class TestValidateSkillCode:
    def test_valid_code(self):
        assert validate_skill_code(VALID_SKILL) == []

    def test_missing_attribute(self):
        code = 'SKILL_NAME = "x"\nasync def execute(args): pass'
        errors = validate_skill_code(code)
        assert any("Missing required attributes" in e for e in errors)

    def test_missing_execute(self):
        code = (
            'SKILL_NAME = "x"\n'
            'SKILL_DESCRIPTION = "x"\n'
            'SKILL_VERSION = "1.0"\n'
            'SKILL_PARAMETERS = {}\n'
            'def execute(args): pass\n'  # not async
        )
        errors = validate_skill_code(code)
        assert any("execute" in e for e in errors)

    def test_blocked_import_os(self):
        code = VALID_SKILL + "\nimport os\n"
        errors = validate_skill_code(code)
        assert any("Blocked import" in e for e in errors)

    def test_blocked_import_subprocess(self):
        code = VALID_SKILL + "\nfrom subprocess import run\n"
        errors = validate_skill_code(code)
        assert any("Blocked import" in e for e in errors)

    def test_blocked_call_exec(self):
        code = VALID_SKILL + "\nexec('print(1)')\n"
        errors = validate_skill_code(code)
        assert any("Blocked call" in e for e in errors)

    def test_blocked_attribute(self):
        code = VALID_SKILL + "\nx = obj.__globals__\n"
        errors = validate_skill_code(code)
        assert any("Blocked attribute" in e for e in errors)

    def test_syntax_error(self):
        errors = validate_skill_code("def (invalid syntax")
        assert any("Syntax error" in e for e in errors)

    def test_oversized_code(self):
        code = "x = 1\n" * 100_000
        errors = validate_skill_code(code)
        assert any("limit" in e.lower() for e in errors)


class TestReadRegistry:
    def test_returns_manifest(self, mock_store):
        manifest = SkillRegistryManifest(version=2, skills=[])
        mock_store.get_file.side_effect = None
        mock_store.get_file.return_value = (manifest.model_dump_json(), "sha1")
        result, sha = read_registry(mock_store)
        assert result.version == 2
        assert sha == "sha1"

    def test_returns_empty_on_not_found(self, mock_store):
        mock_store.get_file.side_effect = FileNotFoundError
        result, sha = read_registry(mock_store)
        assert result.version == 0
        assert sha is None


class TestSaveSkillToGithub:
    def test_atomic_commit(self, mock_store):
        mock_store.get_file.side_effect = FileNotFoundError  # no existing registry
        from datetime import datetime, timezone

        metadata = SkillMetadata(
            name="greet",
            description="Greet",
            version="1.0.0",
            parameters={"name": "str"},
            source="user",
            installed_at=datetime.now(timezone.utc),
            installed_by="u1",
            path="skills/installed/greet.py",
        )
        save_skill_to_github(mock_store, metadata, VALID_SKILL)
        mock_store.atomic_commit.assert_called_once()
        files = mock_store.atomic_commit.call_args[0][0]
        assert "skills/installed/greet.py" in files
        assert "skills/registry.json" in files
