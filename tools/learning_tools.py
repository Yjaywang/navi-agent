"""MCP tools for feedback learning and knowledge consolidation."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server

from memory.engine import MemoryEngine
from memory.models import FeedbackMemory

log = logging.getLogger(__name__)

# Lazy-initialized engine (set via init_learning_tools)
_engine: MemoryEngine | None = None


def init_learning_tools(engine: MemoryEngine) -> None:
    global _engine
    _engine = engine


def _get_engine() -> MemoryEngine:
    if _engine is None:
        raise RuntimeError("Learning tools not initialized — call init_learning_tools(engine) first")
    return _engine


@tool(
    "record_feedback",
    "Record user feedback (positive/negative/bookmark) on a specific bot response for future learning.",
    {
        "conversation_id": str,
        "turn_index": int,
        "feedback_type": str,
        "original_query": str,
        "original_response": str,
        "correction": str,
    },
)
async def record_feedback(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()
    feedback_id = uuid.uuid4().hex[:12]
    feedback_type = args.get("feedback_type", "positive")
    if feedback_type not in ("positive", "negative", "bookmark"):
        feedback_type = "positive"

    feedback = FeedbackMemory(
        id=feedback_id,
        feedback_type=feedback_type,
        original_query=args.get("original_query", ""),
        original_response=args.get("original_response", ""),
        correction=args.get("correction", ""),
        conversation_id=args.get("conversation_id", ""),
        turn_index=args.get("turn_index", -1),
        summary=f"{feedback_type} feedback: {args.get('original_query', '')[:80]}",
        tags=["feedback", feedback_type],
        source_user="discord_reaction",
    )

    path = engine.store_feedback(feedback)
    return {"content": [{"type": "text", "text": f"Feedback recorded: {feedback_id} at {path}"}]}


@tool(
    "consolidate_knowledge",
    "Trigger knowledge consolidation: load recent facts, group by topic, and return "
    "grouped facts for synthesis into knowledge articles. Topics with 3+ facts are included.",
    {"topic": str, "date_range_days": int},
)
async def consolidate_knowledge(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()
    topic = args.get("topic", "") or None
    date_range_days = args.get("date_range_days", 7) or 7

    result = engine.consolidate_knowledge(topic, date_range_days)
    groups = result["groups"]

    if not groups:
        return {"content": [{"type": "text", "text": "No topics with 3+ facts found in the given date range."}]}

    output = {"topics": {}}
    for topic_name, facts in groups.items():
        output["topics"][topic_name] = {
            "fact_count": len(facts),
            "facts": [
                {"id": f["id"], "summary": f["summary"], "content": f["content"][:500]}
                for f in facts
            ],
        }
    output["entry_ids"] = result["entry_ids"]
    output["instruction"] = (
        "For each topic above, synthesize the facts into a coherent knowledge article in Markdown. "
        "Resolve contradictions (prefer newer facts). Then call memory_store_fact with the consolidated "
        "article as content, tagged with the topic name and 'consolidated'. "
        "After successfully storing the consolidated article, call mark_facts_consolidated "
        "with the entry_ids listed above to mark the original facts as consolidated."
    )

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False, indent=2)}]}


@tool(
    "mark_facts_consolidated",
    "Mark original facts as consolidated after a knowledge article has been successfully stored. "
    "Call this only after the consolidated article is saved via memory_store_fact.",
    {"entry_ids": list},
)
async def mark_facts_consolidated(args: dict[str, Any]) -> dict[str, Any]:
    engine = _get_engine()
    entry_ids = args.get("entry_ids", [])
    if not entry_ids:
        return {"content": [{"type": "text", "text": "No entry_ids provided."}]}

    try:
        engine.mark_consolidated(entry_ids)
    except Exception:
        log.exception("Failed to mark entries as consolidated")
        return {"content": [{"type": "text", "text": "Error: failed to mark entries as consolidated."}]}

    return {"content": [{"type": "text", "text": f"Marked {len(entry_ids)} facts as consolidated."}]}


# Bundle into MCP server
learning_server = create_sdk_mcp_server(
    "learning-tools",
    tools=[record_feedback, consolidate_knowledge, mark_facts_consolidated],
)
