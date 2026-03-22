"""Skill code validation and GitHub I/O for skill persistence."""

from __future__ import annotations

import ast
import json
import logging
from datetime import datetime, timezone
from typing import Any

from memory.github_store import GitHubStore
from skills.base import (
    BLOCKED_ATTRIBUTES,
    BLOCKED_CALLS,
    BLOCKED_IMPORTS,
    MAX_SKILL_CODE_SIZE,
    REQUIRED_ATTRIBUTES,
    SkillMetadata,
    SkillRegistryManifest,
)

log = logging.getLogger(__name__)

REGISTRY_PATH = "skills/registry.json"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_skill_code(code: str) -> list[str]:
    """Validate skill code via AST analysis. Returns list of errors (empty = valid)."""
    errors: list[str] = []

    # Size check
    if len(code.encode()) > MAX_SKILL_CODE_SIZE:
        errors.append(f"Code exceeds {MAX_SKILL_CODE_SIZE // 1024}KB limit")
        return errors

    # Syntax check
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        errors.append(f"Syntax error: {exc}")
        return errors

    # Check required top-level attributes
    top_level_assigns: set[str] = set()
    has_execute = False

    for node in ast.iter_child_nodes(tree):
        # Simple assignment: SKILL_NAME = "..."
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    top_level_assigns.add(target.id)
        # async def execute(...)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute":
            has_execute = True

    missing = REQUIRED_ATTRIBUTES - top_level_assigns
    if missing:
        errors.append(f"Missing required attributes: {', '.join(sorted(missing))}")
    if not has_execute:
        errors.append("Missing required `async def execute(args)` function")

    # Walk entire AST for forbidden patterns
    for node in ast.walk(tree):
        # Blocked imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_root = alias.name.split(".")[0]
                if module_root in BLOCKED_IMPORTS:
                    errors.append(f"Blocked import: {alias.name}")

        if isinstance(node, ast.ImportFrom) and node.module:
            module_root = node.module.split(".")[0]
            if module_root in BLOCKED_IMPORTS:
                errors.append(f"Blocked import: from {node.module}")

        # Blocked function calls
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BLOCKED_CALLS:
                errors.append(f"Blocked call: {func.id}()")
            if isinstance(func, ast.Attribute) and func.attr in BLOCKED_CALLS:
                errors.append(f"Blocked call: .{func.attr}()")

        # Blocked attribute access
        if isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRIBUTES:
            errors.append(f"Blocked attribute access: .{node.attr}")

    return errors


# ---------------------------------------------------------------------------
# GitHub I/O
# ---------------------------------------------------------------------------


def _read_registry(store: GitHubStore) -> tuple[SkillRegistryManifest, str | None]:
    """Read skills/registry.json. Returns (manifest, sha) or (empty manifest, None)."""
    try:
        content, sha = store.get_file(REGISTRY_PATH)
        manifest = SkillRegistryManifest.model_validate_json(content)
        return manifest, sha
    except FileNotFoundError:
        return SkillRegistryManifest(), None


def load_skills_from_github(
    store: GitHubStore,
) -> list[tuple[SkillMetadata, str]]:
    """Load all enabled skills from the GitHub memory repo.

    Returns list of (metadata, code) tuples.
    """
    manifest, _ = _read_registry(store)
    results: list[tuple[SkillMetadata, str]] = []

    for skill in manifest.skills:
        if not skill.enabled:
            log.debug("Skipping disabled skill: %s", skill.name)
            continue
        try:
            code, _ = store.get_file(skill.path)
            results.append((skill, code))
            log.info("Loaded skill from GitHub: %s", skill.name)
        except FileNotFoundError:
            log.warning("Skill file not found: %s (path: %s)", skill.name, skill.path)
        except Exception:
            log.exception("Failed to load skill: %s", skill.name)

    return results


def save_skill_to_github(
    store: GitHubStore,
    metadata: SkillMetadata,
    code: str,
) -> None:
    """Save a skill file and update registry.json atomically."""
    manifest, _ = _read_registry(store)

    # Update or add entry
    existing_idx = next(
        (i for i, s in enumerate(manifest.skills) if s.name == metadata.name),
        None,
    )
    if existing_idx is not None:
        manifest.skills[existing_idx] = metadata
    else:
        manifest.skills.append(metadata)

    manifest.version += 1

    # Atomic commit: skill file + registry.json
    files = {
        metadata.path: code,
        REGISTRY_PATH: manifest.model_dump_json(indent=2),
    }
    store.atomic_commit(files, f"Install skill: {metadata.name}")
    log.info("Saved skill to GitHub: %s", metadata.name)


def remove_skill_from_github(store: GitHubStore, name: str) -> None:
    """Remove a skill entry from registry.json (leaves the file for history)."""
    manifest, sha = _read_registry(store)

    manifest.skills = [s for s in manifest.skills if s.name != name]
    manifest.version += 1

    store.create_or_update_file(
        REGISTRY_PATH,
        manifest.model_dump_json(indent=2),
        f"Remove skill: {name}",
        sha=sha,
    )
    log.info("Removed skill from registry: %s", name)


def update_registry_on_github(
    store: GitHubStore,
    manifest: SkillRegistryManifest,
) -> None:
    """Write an updated manifest to skills/registry.json."""
    _, sha = _read_registry(store)
    store.create_or_update_file(
        REGISTRY_PATH,
        manifest.model_dump_json(indent=2),
        "Update skill registry",
        sha=sha,
    )


# ---------------------------------------------------------------------------
# Builtin skill installation
# ---------------------------------------------------------------------------


def install_builtins(store: GitHubStore) -> list[tuple[SkillMetadata, str]]:
    """Install builtin skills to GitHub if not already present.

    Returns list of (metadata, code) for newly installed skills.
    """
    import importlib.resources as pkg_resources

    manifest, _ = _read_registry(store)
    existing_names = {s.name for s in manifest.skills}

    installed: list[tuple[SkillMetadata, str]] = []
    builtin_dir = pkg_resources.files("skills.builtin")

    for resource in builtin_dir.iterdir():
        if not resource.name.endswith(".py") or resource.name == "__init__.py":
            continue

        code = resource.read_text(encoding="utf-8")

        # Extract metadata from code
        try:
            namespace: dict[str, Any] = {}
            exec(compile(code, resource.name, "exec"), namespace)  # noqa: S102
        except Exception:
            log.exception("Failed to parse builtin skill: %s", resource.name)
            continue

        name = namespace.get("SKILL_NAME")
        if not name or name in existing_names:
            continue

        metadata = SkillMetadata(
            name=name,
            description=namespace.get("SKILL_DESCRIPTION", ""),
            version=namespace.get("SKILL_VERSION", "1.0.0"),
            parameters={k: v.__name__ if isinstance(v, type) else str(v)
                        for k, v in namespace.get("SKILL_PARAMETERS", {}).items()},
            source="builtin",
            enabled=True,
            installed_at=datetime.now(timezone.utc),
            installed_by="system",
            path=f"skills/installed/{name}.py",
        )

        try:
            save_skill_to_github(store, metadata, code)
            installed.append((metadata, code))
            log.info("Installed builtin skill: %s", name)
        except Exception:
            log.exception("Failed to install builtin skill: %s", name)

    return installed
