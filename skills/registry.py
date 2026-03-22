"""SkillRegistry — holds loaded skills in memory and produces MCP server configs."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server

from memory.github_store import GitHubStore
from skills.base import (
    SAFE_BUILTINS,
    SKILL_EXEC_TIMEOUT,
    SkillMetadata,
    SkillRegistryManifest,
)
from skills.loader import (
    update_registry_on_github,
    validate_skill_code,
    read_registry,
)

log = logging.getLogger(__name__)

# Type mapping: string type names → Python types (for SdkMcpTool input_schema)
_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}


@dataclass
class LoadedSkill:
    """A skill that has been loaded into memory and is ready to execute."""

    metadata: SkillMetadata
    execute_fn: Callable[[dict[str, Any]], Any]
    tool: SdkMcpTool


class SkillRegistry:
    """Manages loaded skills and produces MCP server configs on demand.

    Designed as a singleton — use the module-level helpers to access.
    """

    def __init__(self, store: GitHubStore) -> None:
        self._skills: dict[str, LoadedSkill] = {}
        self._server_cache = None
        self._store = store

    @property
    def store(self) -> GitHubStore:
        return self._store

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_skill(self, metadata: SkillMetadata, code: str) -> None:
        """Validate, compile, and register a skill from source code."""
        errors = validate_skill_code(code)
        if errors:
            raise ValueError(f"Skill validation failed: {'; '.join(errors)}")

        # Execute code in a restricted namespace to extract the execute function
        namespace: dict[str, Any] = {"__builtins__": dict(SAFE_BUILTINS)}

        # Inject commonly-needed safe modules
        import json as _json
        import re as _re
        import math as _math
        from datetime import datetime as _datetime, timezone as _timezone

        namespace["json"] = _json
        namespace["re"] = _re
        namespace["math"] = _math
        namespace["datetime"] = _datetime
        namespace["timezone"] = _timezone

        exec(compile(code, f"<skill:{metadata.name}>", "exec"), namespace)  # noqa: S102

        raw_execute = namespace.get("execute")
        if raw_execute is None or not asyncio.iscoroutinefunction(raw_execute):
            raise ValueError("Skill must define `async def execute(args)`")

        # Build a timeout-wrapped handler
        async def _handler(args: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await asyncio.wait_for(
                    raw_execute(args),
                    timeout=SKILL_EXEC_TIMEOUT,
                )
                return result
            except asyncio.TimeoutError:
                return {
                    "content": [{"type": "text", "text": f"Skill '{metadata.name}' timed out after {SKILL_EXEC_TIMEOUT}s"}],
                    "is_error": True,
                }
            except Exception as exc:
                return {
                    "content": [{"type": "text", "text": f"Skill '{metadata.name}' error: {exc}"}],
                    "is_error": True,
                }

        # Build input_schema: convert string type names to Python types
        input_schema: dict[str, type] = {}
        for param_name, type_name in metadata.parameters.items():
            input_schema[param_name] = _TYPE_MAP.get(type_name, str)

        tool = SdkMcpTool(
            name=metadata.name,
            description=metadata.description,
            input_schema=input_schema,
            handler=_handler,
        )

        self._skills[metadata.name] = LoadedSkill(
            metadata=metadata,
            execute_fn=raw_execute,
            tool=tool,
        )
        self._server_cache = None  # invalidate
        log.info("Registered skill: %s (enabled=%s)", metadata.name, metadata.enabled)

    def unregister_skill(self, name: str) -> None:
        """Remove a skill from the in-memory registry and GitHub."""
        if name not in self._skills:
            return

        del self._skills[name]
        self._server_cache = None

        # Persist removal to GitHub
        try:
            manifest, _ = read_registry(self._store)
            manifest.skills = [s for s in manifest.skills if s.name != name]
            manifest.version += 1
            update_registry_on_github(self._store, manifest)
            log.info("Unregistered skill: %s (persisted to GitHub)", name)
        except Exception:
            log.exception("Skill '%s' removed from memory but failed to persist to GitHub", name)

    # ------------------------------------------------------------------
    # Server construction
    # ------------------------------------------------------------------

    def get_server(self, management_tools: list[SdkMcpTool] | None = None):
        """Build and cache an MCP server combining management tools + dynamic skills.

        Returns None if there are no tools at all.
        """
        if self._server_cache is not None:
            return self._server_cache

        all_tools: list[SdkMcpTool] = list(management_tools or [])

        # Add enabled skill tools
        for loaded in self._skills.values():
            if loaded.metadata.enabled:
                all_tools.append(loaded.tool)

        if not all_tools:
            return None

        self._server_cache = create_sdk_mcp_server("skill-tools", tools=all_tools)
        return self._server_cache

    def get_tool_names(self) -> list[str]:
        """Return MCP tool names for all enabled dynamic skills."""
        return [
            f"mcp__skill-tools__{name}"
            for name, loaded in self._skills.items()
            if loaded.metadata.enabled
        ]

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_skill_list(self) -> list[SkillMetadata]:
        """Return metadata for all registered skills."""
        return [loaded.metadata for loaded in self._skills.values()]

    def get_skill(self, name: str) -> LoadedSkill | None:
        return self._skills.get(name)

    # ------------------------------------------------------------------
    # Toggle
    # ------------------------------------------------------------------

    def toggle_skill(self, name: str, enabled: bool) -> None:
        """Enable or disable a skill and persist to GitHub."""
        loaded = self._skills.get(name)
        if loaded is None:
            raise KeyError(f"Skill '{name}' not found")

        loaded.metadata.enabled = enabled
        self._server_cache = None  # invalidate

        # Persist to GitHub
        try:
            manifest, _ = read_registry(self._store)
            for skill in manifest.skills:
                if skill.name == name:
                    skill.enabled = enabled
                    break
            manifest.version += 1
            update_registry_on_github(self._store, manifest)
            log.info("Toggled skill %s: enabled=%s", name, enabled)
        except Exception:
            log.exception("Failed to persist skill toggle to GitHub")
