"""MCP tools for skill management (list, create, toggle)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from claude_agent_sdk import tool

from skills.base import SkillMetadata
from skills.loader import save_skill_to_github, remove_skill_from_github, validate_skill_code
from skills.registry import SkillRegistry

log = logging.getLogger(__name__)

# Lazy-initialized registry (set via init_skill_tools)
_registry: SkillRegistry | None = None


def init_skill_tools(registry: SkillRegistry) -> None:
    global _registry
    _registry = registry


def _get_registry() -> SkillRegistry:
    if _registry is None:
        raise RuntimeError("Skill tools not initialized — call init_skill_tools(registry) first")
    return _registry


# ---------------------------------------------------------------------------
# Tool 1: skill_list
# ---------------------------------------------------------------------------


@tool(
    "skill_list",
    "List all installed skills and their status (name, description, version, source, enabled).",
    {},
)
async def skill_list(args: dict[str, Any]) -> dict[str, Any]:
    registry = _get_registry()
    skills = registry.get_skill_list()

    if not skills:
        return {"content": [{"type": "text", "text": "No skills installed."}]}

    items = [
        {
            "name": s.name,
            "description": s.description,
            "version": s.version,
            "source": s.source,
            "enabled": s.enabled,
            "installed_by": s.installed_by,
        }
        for s in skills
    ]
    return {"content": [{"type": "text", "text": json.dumps(items, ensure_ascii=False, indent=2)}]}


# ---------------------------------------------------------------------------
# Tool 2: skill_create
# ---------------------------------------------------------------------------


@tool(
    "skill_create",
    (
        "Create and install a new skill. The code must define SKILL_NAME, SKILL_DESCRIPTION, "
        "SKILL_VERSION, SKILL_PARAMETERS, and an `async def execute(args)` function. "
        "Set source to 'user' for user-requested skills or 'agent' for self-created skills "
        "(agent skills start disabled and need user confirmation via skill_toggle)."
    ),
    {
        "name": str,
        "description": str,
        "version": str,
        "parameters_json": str,
        "code": str,
        "source": str,
        "installed_by": str,
    },
)
async def skill_create(args: dict[str, Any]) -> dict[str, Any]:
    registry = _get_registry()

    name = args["name"]
    code = args["code"]
    source = args.get("source", "user")
    installed_by = args.get("installed_by", "unknown")

    # Validate
    errors = validate_skill_code(code)
    if errors:
        return {
            "content": [{"type": "text", "text": f"Skill validation failed:\n" + "\n".join(f"- {e}" for e in errors)}],
            "is_error": True,
        }

    # Parse parameters_json
    try:
        parameters = json.loads(args.get("parameters_json", "{}"))
    except json.JSONDecodeError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid parameters_json: {exc}"}],
            "is_error": True,
        }

    # Agent-created skills start disabled
    enabled = source != "agent"

    metadata = SkillMetadata(
        name=name,
        description=args.get("description", ""),
        version=args.get("version", "1.0.0"),
        parameters={k: v if isinstance(v, str) else type(v).__name__ for k, v in parameters.items()},
        source=source,
        enabled=enabled,
        installed_at=datetime.now(timezone.utc),
        installed_by=installed_by,
        path=f"skills/installed/{name}.py",
    )

    # Save to GitHub
    try:
        save_skill_to_github(registry.store, metadata, code)
    except Exception as exc:
        return {
            "content": [{"type": "text", "text": f"Failed to save skill to GitHub: {exc}"}],
            "is_error": True,
        }

    # Register in memory
    try:
        registry.register_skill(metadata, code)
    except ValueError as exc:
        return {
            "content": [{"type": "text", "text": f"Failed to register skill: {exc}"}],
            "is_error": True,
        }

    if source == "agent":
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Skill '{name}' has been created but is **disabled** (source: agent). "
                    f"Please ask the user to confirm, then call `skill_toggle` with "
                    f'name="{name}" and enabled=true to activate it.'
                ),
            }],
        }

    return {
        "content": [{
            "type": "text",
            "text": f"Skill '{name}' installed and enabled successfully. It will be available as a tool in the next query.",
        }],
    }


# ---------------------------------------------------------------------------
# Tool 3: skill_toggle
# ---------------------------------------------------------------------------


@tool(
    "skill_toggle",
    "Enable or disable an installed skill. Use this to activate agent-created skills after user confirmation.",
    {"name": str, "enabled": bool},
)
async def skill_toggle(args: dict[str, Any]) -> dict[str, Any]:
    registry = _get_registry()
    name = args["name"]
    enabled = args["enabled"]

    try:
        registry.toggle_skill(name, enabled)
    except KeyError:
        return {
            "content": [{"type": "text", "text": f"Skill '{name}' not found."}],
            "is_error": True,
        }
    except Exception as exc:
        return {
            "content": [{"type": "text", "text": f"Failed to toggle skill: {exc}"}],
            "is_error": True,
        }

    status = "enabled" if enabled else "disabled"
    return {
        "content": [{"type": "text", "text": f"Skill '{name}' is now {status}."}],
    }


# ---------------------------------------------------------------------------
# Exported management tools list (used by SkillRegistry.get_server())
# ---------------------------------------------------------------------------

MANAGEMENT_TOOLS = [skill_list, skill_create, skill_toggle]
