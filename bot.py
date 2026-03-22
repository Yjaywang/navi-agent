#!/usr/bin/env python3
"""Discord bot entry point — listens for messages and routes them to the Claude agent."""

import asyncio
import logging
import os

import io

import discord

import uuid

import agent
from config import load_config
from memory.models import FeedbackMemory
from sessions.manager import SessionManager
from utils.chunking import chunk_text

# Max messages to fetch from thread history for conversation context
_MAX_HISTORY = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Limit concurrent agent queries to avoid overloading
_semaphore = asyncio.Semaphore(5)


def _should_respond(message: discord.Message, bot_user: discord.User) -> bool:
    """Determine if the bot should respond to this message."""
    # Never respond to self
    if message.author == bot_user:
        return False

    # Never respond to other bots
    if message.author.bot:
        return False

    # DM — always respond
    if isinstance(message.channel, discord.DMChannel):
        return True

    # @mention
    if bot_user in message.mentions:
        return True

    # Reply inside a thread the bot owns or participates in
    if isinstance(message.channel, discord.Thread):
        if message.channel.owner_id == bot_user.id:
            return True
        # Also respond if the bot has sent messages in this thread
        # (checked by thread owner for simplicity; Phase 3 will refine)
        return True

    return False


def _clean_mention(text: str, bot_user: discord.User) -> str:
    """Remove the bot's @mention from the message text."""
    return text.replace(f"<@{bot_user.id}>", "").strip()


async def _build_history(
    channel: discord.abc.Messageable,
    bot_user: discord.User,
) -> list[dict[str, str]]:
    """Fetch recent messages from the channel and format as conversation history."""
    messages: list[dict[str, str]] = []
    try:
        async for msg in channel.history(limit=_MAX_HISTORY, oldest_first=True):
            if msg.author == bot_user:
                role = "assistant"
            else:
                role = "user"
            text = msg.content.replace(f"<@{bot_user.id}>", "").strip()
            if text:
                messages.append({"role": role, "content": text})
    except discord.HTTPException:
        pass
    # Remove the last message (it's the current one, already passed as user_message)
    if messages and messages[-1]["role"] == "user":
        messages.pop()
    return messages


def main():
    config = load_config()
    session_manager = SessionManager(
        ttl_minutes=int(os.environ.get("SESSION_TTL_MINUTES", "60"))
    )

    intents = discord.Intents.default()
    intents.message_content = True
    intents.reactions = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        log.info(f"Bot connected as {client.user} (id={client.user.id})")

    @client.event
    async def on_message(message: discord.Message):
        if not _should_respond(message, client.user):
            return

        user_text = _clean_mention(message.content, client.user)

        # Download attachments
        attachments: dict[str, dict] = {}
        for att in message.attachments:
            if att.content_type:
                try:
                    data = await att.read()
                    att_id = str(att.id)
                    attachments[att_id] = {
                        "filename": att.filename,
                        "content_type": att.content_type,
                        "data": data,
                    }
                    log.info("Downloaded attachment %s (%s, %d bytes)", att.filename, att.content_type, len(data))
                except discord.HTTPException:
                    log.warning("Failed to download attachment %s", att.filename)

        if not user_text and not attachments:
            return

        # Determine where to send the response
        response_channel = message.channel

        # If mentioned in a regular channel (not thread, not DM), create a thread
        if (
            not isinstance(message.channel, (discord.DMChannel, discord.Thread))
            and client.user in message.mentions
        ):
            try:
                thread = await message.create_thread(
                    name=f"Chat with {message.author.display_name}",
                    auto_archive_duration=60,
                )
                response_channel = thread
            except discord.HTTPException:
                # Fallback to replying in the same channel
                pass

        # Session management
        guild_id = str(message.guild.id) if message.guild else ""
        channel_id = str(response_channel.id)
        user_id = str(message.author.id)
        user_text_or_fallback = user_text or "(user sent an attachment without text)"

        session = session_manager.get_or_create(guild_id, channel_id, user_id)

        # Bootstrap from Discord history if session is fresh (e.g. after bot restart)
        if not session.turns:
            discord_history = await _build_history(response_channel, client.user)
            for msg in discord_history:
                session.add_turn(msg["role"], msg["content"])

        # Build history for agent (before adding current message)
        history = session.get_history()

        # Record current user message
        session.add_turn("user", user_text_or_fallback)

        async with _semaphore:
            # Show typing indicator while processing
            async with response_channel.typing():
                try:
                    response_text, response_files = await agent.run_query(
                        user_text_or_fallback,
                        user_id=user_id,
                        guild_id=guild_id,
                        conversation_history=history,
                        attachments=attachments,
                    )
                except Exception as e:
                    log.exception("Agent query failed")
                    response_text = f"Sorry, something went wrong: {e}"
                    response_files = []

            # Record assistant response in session
            session.add_turn("assistant", response_text)

            # Send response in chunks
            chunks = chunk_text(response_text)
            # Attach files to the last chunk
            discord_files = [
                discord.File(io.BytesIO(f["data"]), filename=f["filename"])
                for f in response_files
            ]
            for i, part in enumerate(chunks):
                is_last = i == len(chunks) - 1
                if is_last and discord_files:
                    await response_channel.send(part, files=discord_files)
                else:
                    await response_channel.send(part)

        # Opportunistic cleanup of expired sessions
        session_manager.cleanup_expired()

    _FEEDBACK_EMOJIS = {"👍": "positive", "👎": "negative", "🔖": "bookmark"}

    @client.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        # Ignore bot's own reactions
        if payload.user_id == client.user.id:
            return

        emoji = str(payload.emoji)
        if emoji not in _FEEDBACK_EMOJIS:
            return

        feedback_type = _FEEDBACK_EMOJIS[emoji]

        # Fetch the channel and message
        channel = client.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(payload.channel_id)
            except discord.HTTPException:
                return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        # Only process reactions on the bot's own messages
        if message.author.id != client.user.id:
            return

        # Find the preceding user message (original query)
        original_query = ""
        try:
            async for msg in channel.history(limit=20, before=message, oldest_first=False):
                if msg.author.id != client.user.id:
                    original_query = msg.content
                    break
        except discord.HTTPException:
            log.warning("Failed to fetch message history for feedback in channel %s", payload.channel_id)

        try:
            engine = agent._get_engine()
            feedback = FeedbackMemory(
                id=uuid.uuid4().hex[:12],
                feedback_type=feedback_type,
                original_query=original_query[:500],
                original_response=message.content[:500],
                correction="",
                conversation_id=str(payload.channel_id),
                turn_index=-1,
                message_id=str(payload.message_id),
                summary=f"{feedback_type} feedback: {original_query[:80]}",
                tags=["feedback", feedback_type],
                source_user=str(payload.user_id),
            )
            engine.store_feedback(feedback)
            log.info(
                "Recorded %s feedback from user %s on message %s",
                feedback_type, payload.user_id, payload.message_id,
            )
        except Exception:
            log.exception("Failed to record feedback")

    client.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
