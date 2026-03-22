"""Skill data models and constants."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SkillMetadata(BaseModel):
    """Metadata for an installed skill."""

    name: str
    description: str
    version: str
    parameters: dict[str, str]  # e.g. {"text": "str", "count": "int"}
    source: Literal["builtin", "user", "agent"]
    enabled: bool = True
    installed_at: datetime
    installed_by: str
    path: str  # path in GitHub memory repo, e.g. "skills/installed/translate.py"


class SkillRegistryManifest(BaseModel):
    """Top-level structure of skills/registry.json in the memory repo."""

    version: int = 0
    skills: list[SkillMetadata] = []


# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

REQUIRED_ATTRIBUTES = {"SKILL_NAME", "SKILL_DESCRIPTION", "SKILL_VERSION", "SKILL_PARAMETERS"}

BLOCKED_IMPORTS = {
    "os", "sys", "subprocess", "shutil", "pathlib", "socket",
    "http", "urllib", "requests", "ctypes", "signal",
    "multiprocessing", "importlib", "builtins", "code", "codeop",
    "compileall", "py_compile", "webbrowser", "tempfile", "glob",
    "io", "pickle", "shelve", "sqlite3", "xmlrpc",
}

BLOCKED_CALLS = {
    "exec", "eval", "__import__", "compile", "open",
    "getattr", "setattr", "delattr", "breakpoint", "input",
    "globals", "locals", "vars", "dir", "type",
}

BLOCKED_ATTRIBUTES = {
    "__dict__", "__class__", "__bases__", "__subclasses__",
    "__globals__", "__code__", "__builtins__", "__import__",
}

# Safe builtins exposed to skill code at exec() time
SAFE_BUILTINS = {
    "True": True,
    "False": False,
    "None": None,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "hasattr": hasattr,
    "repr": repr,
    "format": format,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "RuntimeError": RuntimeError,
    "Exception": Exception,
}

MAX_SKILL_CODE_SIZE = 50 * 1024  # 50 KB
SKILL_EXEC_TIMEOUT = 30  # seconds
