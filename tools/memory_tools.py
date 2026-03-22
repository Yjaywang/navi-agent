"""MCP tools for memory CRUD and search."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server

from config import Config
from memory.engine import MemoryEngine
from memory.models import ConversationMemory, FactMemory, UserProfile

# Lazy-initialized engine (set via init_memory_tools)
_engine: MemoryEngine | None = None

# Temporary storage for attachments (set per query by bot.py → agent.py)
_pending_attachments: dict[str, dict] = {}

# Files to send back to the user as Discord attachments (populated by memory_retrieve_file)
_response_files: list[dict] = []

# Extensions considered text-readable by the agent
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".xml", ".yaml", ".yml",
    ".csv", ".html", ".css", ".sql", ".sh", ".toml", ".ini", ".cfg",
    ".log", ".rst", ".go", ".rs", ".java", ".c", ".cpp", ".h",
    ".rb", ".php", ".swift", ".kt", ".r", ".tex",
}

_TEXT_MIME_TYPES = {
    "application/json", "application/xml", "application/javascript",
    "application/x-yaml", "application/x-sh", "application/sql",
    "application/toml",
}


def _is_text_file(filename: str, content_type: str) -> bool:
    """Determine if a file is text-based (readable by the agent)."""
    if content_type.startswith("text/"):
        return True
    if content_type in _TEXT_MIME_TYPES:
        return True
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    return ext in _TEXT_EXTENSIONS


def init_memory_tools(config: Config) -> None:
    global _engine
    if _engine is None:
        _engine = MemoryEngine(config)


def set_pending_attachments(attachments: dict[str, dict]) -> None:
    """Store image attachments for the current query. Called by agent.py."""
    global _pending_attachments
    _pending_attachments = attachments


def clear_pending_attachments() -> None:
    global _pending_attachments
    _pending_attachments = {}


def get_response_files() -> list[dict]:
    """Return files queued for sending back to the user."""
    return list(_response_files)


def clear_response_files() -> None:
    global _response_files
    _response_files = []


def _get_engine() -> MemoryEngine:
    if _engine is None:
        raise RuntimeError("Memory tools not initialized — call init_memory_tools(config) first")
    return _engine


# --- Tools ---


@tool(
    "memory_search",
    "Search the agent's memory for relevant past conversations, facts, and knowledge.",
    {"query": str, "type_filter": str, "max_results": int},
)
async def memory_search(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()
    query = args["query"]
    type_filter = args.get("type_filter", "") or None
    max_results = args.get("max_results", 5) or 5

    results = engine.indexer.search(query, top_k=max_results, type_filter=type_filter)

    if not results:
        return {"content": [{"type": "text", "text": "No matching memories found."}]}

    items: list[dict] = []
    for entry in results:
        try:
            content, _ = engine.store.get_file(entry.path)
        except FileNotFoundError:
            content = "(content unavailable)"
        items.append({
            "id": entry.id,
            "type": entry.type,
            "summary": entry.summary,
            "tags": entry.tags,
            "updated_at": entry.updated_at.isoformat(),
            "content": content[:500],
        })

    return {"content": [{"type": "text", "text": json.dumps(items, ensure_ascii=False, indent=2)}]}


@tool(
    "memory_store_fact",
    "Store a new fact learned from conversation. Include tags for future retrieval.",
    {"summary": str, "content": str, "tags": str, "confidence": float, "source_conversation": str},
)
async def memory_store_fact(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()

    fact_id = uuid.uuid4().hex[:12]
    tags = [t.strip() for t in args.get("tags", "").split(",") if t.strip()]

    fact = FactMemory(
        id=fact_id,
        source_user="agent",
        content=args["content"],
        summary=args["summary"],
        tags=tags,
        confidence=args.get("confidence", 1.0),
        source_conversation=args.get("source_conversation", ""),
    )

    path = engine.store_fact(fact)
    return {"content": [{"type": "text", "text": f"Fact stored: {fact_id} at {path}"}]}


@tool(
    "memory_store_conversation",
    "Store a conversation summary after a chat session ends.",
    {"summary": str, "outcome": str, "topics": str, "turns_json": str},
)
async def memory_store_conversation(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()

    conv_id = uuid.uuid4().hex[:12]
    topics = [t.strip() for t in args.get("topics", "").split(",") if t.strip()]

    try:
        turns = json.loads(args.get("turns_json", "[]"))
    except json.JSONDecodeError:
        turns = []

    memory = ConversationMemory(
        id=conv_id,
        source_user="agent",
        content=args.get("summary", ""),
        summary=args["summary"],
        outcome=args.get("outcome", ""),
        topics=topics,
        tags=topics,
        turns=turns,
    )

    path = engine.store_conversation(memory)
    return {"content": [{"type": "text", "text": f"Conversation stored: {conv_id} at {path}"}]}


@tool(
    "memory_get_user_profile",
    "Retrieve a user's profile and preferences.",
    {"user_id": str},
)
async def memory_get_user_profile(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()
    profile = engine.get_user_profile(args["user_id"])
    if profile is None:
        return {"content": [{"type": "text", "text": "No profile found for this user."}]}
    return {"content": [{"type": "text", "text": profile.model_dump_json(indent=2)}]}


@tool(
    "memory_update_user_profile",
    "Update a user's profile with new preferences or information.",
    {"user_id": str, "display_name": str, "preferred_language": str, "notes": str},
)
async def memory_update_user_profile(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()
    user_id = args["user_id"]

    existing = engine.get_user_profile(user_id)
    now = datetime.now(timezone.utc)

    if existing:
        if args.get("display_name"):
            existing.display_name = args["display_name"]
        if args.get("preferred_language"):
            existing.preferred_language = args["preferred_language"]
        if args.get("notes"):
            new_notes = [n.strip() for n in args["notes"].split(",") if n.strip()]
            existing.notes.extend(new_notes)
        existing.last_seen = now
        profile = existing
    else:
        notes = [n.strip() for n in args.get("notes", "").split(",") if n.strip()]
        profile = UserProfile(
            user_id=user_id,
            display_name=args.get("display_name", ""),
            preferred_language=args.get("preferred_language"),
            notes=notes,
            first_seen=now,
            last_seen=now,
        )

    engine.update_user_profile(profile)
    return {"content": [{"type": "text", "text": f"Profile updated for user {user_id}."}]}


@tool(
    "view_attached_image",
    "View an image that the user attached to their message. Call this to see what the image contains. "
    "The attachment_id is provided in the user's message.",
    {"attachment_id": str},
)
async def view_attached_image(args: dict[str, Any]) -> dict[str, Any]:
    import base64

    attachment_id = args["attachment_id"]
    attachment = _pending_attachments.get(attachment_id)
    if not attachment:
        return {"content": [{"type": "text", "text": f"No attachment found with id '{attachment_id}'."}]}

    b64_data = base64.b64encode(attachment["data"]).decode()
    return {
        "content": [
            {"type": "image", "data": b64_data, "mimeType": attachment["content_type"]},
            {"type": "text", "text": f"Image: {attachment['filename']} ({len(attachment['data'])} bytes)"},
        ]
    }


@tool(
    "memory_store_image",
    "Store an image in the memory repo with a description. Call this AFTER viewing the image with view_attached_image.",
    {"attachment_id": str, "description": str, "tags": str},
)
async def memory_store_image(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()
    attachment_id = args["attachment_id"]
    attachment = _pending_attachments.get(attachment_id)
    if not attachment:
        return {"content": [{"type": "text", "text": f"No attachment found with id '{attachment_id}'."}]}

    description = args["description"]
    tags = [t.strip() for t in args.get("tags", "").split(",") if t.strip()]
    filename = attachment["filename"]
    now = datetime.now(timezone.utc)

    # Store the image file
    date_prefix = now.strftime("%Y/%m/%d")
    image_id = uuid.uuid4().hex[:12]
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "png"
    image_path = f"images/{date_prefix}/{image_id}.{ext}"

    engine.store.store_binary_file(
        image_path, attachment["data"], f"Store image {image_id}"
    )

    # Store a fact about the image
    fact = FactMemory(
        id=image_id,
        source_user="agent",
        content=f"Image: {filename}\nPath: {image_path}\nDescription: {description}",
        summary=f"Image: {filename} - {description[:80]}",
        tags=["image", filename] + tags,
        confidence=1.0,
    )
    fact_path = engine.store_fact(fact)

    return {"content": [{"type": "text", "text": f"Image stored at {image_path}, fact at {fact_path}"}]}


@tool(
    "view_attached_file",
    "View a non-image file that the user attached. For text files, returns the file content. "
    "For binary files (PDF, zip, etc.), returns metadata only. "
    "The attachment_id is provided in the user's message.",
    {"attachment_id": str},
)
async def view_attached_file(args: dict[str, Any]) -> dict[str, Any]:
    attachment_id = args["attachment_id"]
    attachment = _pending_attachments.get(attachment_id)
    if not attachment:
        return {"content": [{"type": "text", "text": f"No attachment found with id '{attachment_id}'."}]}

    filename = attachment["filename"]
    content_type = attachment["content_type"]
    data: bytes = attachment["data"]
    size = len(data)

    if _is_text_file(filename, content_type):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        max_chars = 100_000
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        header = f"File: {filename} ({content_type}, {size} bytes)"
        if truncated:
            header += f"\n[TRUNCATED: showing first {max_chars} of {size} characters]"
        return {"content": [{"type": "text", "text": f"{header}\n\n--- File Content ---\n{text}"}]}
    else:
        return {"content": [{"type": "text", "text": (
            f"File: {filename} ({content_type}, {size} bytes)\n"
            "This is a binary file. Content cannot be displayed, but it can be stored "
            "to memory with `memory_store_file`."
        )}]}


@tool(
    "memory_store_file",
    "Store a file attachment in the memory repo with a description. "
    "Works for any file type (text, PDF, code, documents, etc.). "
    "Call this AFTER viewing the file with view_attached_file.",
    {"attachment_id": str, "description": str, "tags": str},
)
async def memory_store_file(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()
    attachment_id = args["attachment_id"]
    attachment = _pending_attachments.get(attachment_id)
    if not attachment:
        return {"content": [{"type": "text", "text": f"No attachment found with id '{attachment_id}'."}]}

    description = args["description"]
    tags = [t.strip() for t in args.get("tags", "").split(",") if t.strip()]
    filename = attachment["filename"]
    content_type = attachment["content_type"]
    now = datetime.now(timezone.utc)

    # Store the file
    date_prefix = now.strftime("%Y/%m/%d")
    file_id = uuid.uuid4().hex[:12]
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    file_path = f"files/{date_prefix}/{file_id}.{ext}"

    engine.store.store_binary_file(
        file_path, attachment["data"], f"Store file {file_id}"
    )

    # Store a fact about the file
    fact = FactMemory(
        id=file_id,
        source_user="agent",
        content=f"File: {filename}\nType: {content_type}\nPath: {file_path}\nDescription: {description}",
        summary=f"File: {filename} - {description[:80]}",
        tags=["file", filename, ext, content_type.split("/")[0]] + tags,
        confidence=1.0,
    )
    fact_path = engine.store_fact(fact)

    return {"content": [{"type": "text", "text": f"File stored at {file_path}, fact at {fact_path}"}]}


@tool(
    "memory_retrieve_file",
    "Retrieve a previously stored file or image from the memory repo and send it back to the user. "
    "Provide the repo path (e.g. 'files/2026/03/22/abc123.csv' or 'images/2026/03/22/abc123.png'). "
    "You can find the path by searching memory first with memory_search. "
    "Use original_filename to restore the original file name (found in the fact content 'File:' field).",
    {"file_path": str, "original_filename": str},
)
async def memory_retrieve_file(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()
    file_path = args["file_path"]

    try:
        data, _ = engine.store.get_binary_file(file_path)
    except FileNotFoundError:
        return {"content": [{"type": "text", "text": f"File not found: {file_path}"}]}

    # Use original filename if provided, otherwise fall back to repo path
    filename = args.get("original_filename") or (file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path)

    _response_files.append({
        "filename": filename,
        "data": data,
    })

    return {"content": [{"type": "text", "text": (
        f"File retrieved: {filename} ({len(data)} bytes). "
        "It will be sent as an attachment in the Discord message."
    )}]}


# Bundle into MCP server
memory_server = create_sdk_mcp_server(
    "memory-tools",
    tools=[
        memory_search,
        memory_store_fact,
        memory_store_conversation,
        memory_get_user_profile,
        memory_update_user_profile,
        view_attached_image,
        memory_store_image,
        view_attached_file,
        memory_store_file,
        memory_retrieve_file,
    ],
)
