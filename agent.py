"""Claude Agent wrapper — sends a user message and returns the full response."""

import logging
import os

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from config import load_config
from memory.engine import MemoryEngine
from tools.memory_tools import (
    init_memory_tools,
    memory_server,
    set_pending_attachments,
    clear_pending_attachments,
)

log = logging.getLogger(__name__)

# Lazy singleton
_engine: MemoryEngine | None = None


def _get_engine() -> MemoryEngine:
    global _engine
    if _engine is None:
        config = load_config()
        _engine = MemoryEngine(config)
        init_memory_tools(config)
    return _engine


def _load_system_prompt(memory_context: str = "") -> str:
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "system.md")
    with open(prompt_path) as f:
        template = f.read()
    return template.replace("{memory_context}", memory_context)


async def run_query(
    user_message: str,
    user_id: str = "",
    guild_id: str = "",
    conversation_history: list[dict[str, str]] | None = None,
    attachments: dict[str, dict] | None = None,
) -> str:
    """Send a message to Claude and return the full text response."""
    config = load_config()
    engine = _get_engine()

    # Set up pending attachments for image tools
    if attachments:
        set_pending_attachments(attachments)
    else:
        clear_pending_attachments()

    # Retrieve relevant memory context
    try:
        memory_context = engine.retrieve_context(user_message, user_id)
        log.info("Memory context for user %s: %s", user_id, memory_context[:200] if memory_context else "(empty)")
    except Exception:
        log.exception("Failed to retrieve memory context")
        memory_context = ""

    system_prompt = _load_system_prompt(memory_context)
    log.debug("System prompt length: %d chars", len(system_prompt))

    options = ClaudeAgentOptions(
        cwd=os.getcwd(),
        allowed_tools=[
            "Read", "Glob", "Grep", "Bash",
            "mcp__memory-tools__memory_search",
            "mcp__memory-tools__memory_store_fact",
            "mcp__memory-tools__memory_store_conversation",
            "mcp__memory-tools__memory_get_user_profile",
            "mcp__memory-tools__memory_update_user_profile",
            "mcp__memory-tools__view_attached_image",
            "mcp__memory-tools__memory_store_image",
        ],
        permission_mode="bypassPermissions",
        model=config.model,
        system_prompt=system_prompt,
        mcp_servers={"memory-tools": memory_server},
        max_turns=15,
    )

    # Build the full prompt with conversation history + attachment info
    prompt_parts: list[str] = []

    if conversation_history:
        history_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'You'}: {m['content']}"
            for m in conversation_history
        )
        prompt_parts.append(f"<conversation_history>\n{history_text}\n</conversation_history>")

    if attachments:
        att_lines = []
        for att_id, att_info in attachments.items():
            att_lines.append(f"- attachment_id={att_id}, filename={att_info['filename']}, type={att_info['content_type']}")
        prompt_parts.append(
            "<attached_images>\n"
            "The user attached the following image(s). "
            "Use `view_attached_image` to see each image, then `memory_store_image` to save it.\n"
            + "\n".join(att_lines)
            + "\n</attached_images>"
        )

    prompt_parts.append(f"User's latest message: {user_message}")
    full_prompt = "\n\n".join(prompt_parts)

    parts: list[str] = []

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(full_prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
    finally:
        clear_pending_attachments()

    return "\n".join(parts) if parts else "（No response generated）"
