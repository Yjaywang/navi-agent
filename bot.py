#!/usr/bin/env python3
"""Discord bot entry point — listens for messages and routes them to the Claude agent."""

import asyncio
import logging
import os
import signal
import time

import io

import discord
from discord import app_commands

import uuid

from datetime import datetime, timezone

import agent
from config import load_config
from memory.models import FeedbackMemory, UserProfile
from sessions.manager import SessionManager
from skills.base import SkillMetadata
from skills.loader import validate_skill_code, save_skill_to_github
from utils.chunking import chunk_text
from utils.rate_limiter import RateLimiter
from utils.permissions import Role, get_user_role, require_role

# Max messages to fetch from thread history for conversation context
_MAX_HISTORY = 10

log = logging.getLogger(__name__)


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

    # Configure logging from config (before any log calls)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    session_manager = SessionManager(ttl_minutes=config.session_ttl_minutes)
    rate_limiter = RateLimiter()
    _start_time = time.monotonic()

    # Per-guild concurrency limiters
    _guild_semaphores: dict[str, asyncio.Semaphore] = {}

    def _get_semaphore(guild_id: str) -> asyncio.Semaphore:
        if guild_id not in _guild_semaphores:
            _guild_semaphores[guild_id] = asyncio.Semaphore(5)
        return _guild_semaphores[guild_id]

    intents = discord.Intents.default()
    intents.message_content = True
    intents.reactions = True
    intents.members = True  # Needed for role-based permission checks
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    # ── Slash commands ──────────────────────────────────────────────

    @tree.command(name="status", description="顯示 Navi 的狀態")
    async def cmd_status(interaction: discord.Interaction):
        uptime_s = int(time.monotonic() - _start_time)
        h, remainder = divmod(uptime_s, 3600)
        m, s = divmod(remainder, 60)

        session_count = len(session_manager._sessions)

        embed = discord.Embed(title="Navi Status", color=0x5865F2)
        embed.add_field(name="Uptime", value=f"{h}h {m}m {s}s")
        embed.add_field(name="Active Sessions", value=str(session_count))
        embed.add_field(name="Model", value=config.model)
        await interaction.response.send_message(embed=embed)

    @tree.command(name="learn", description="讓 Navi 記住或學習你提供的內容")
    @app_commands.describe(content="要讓 Navi 記住的內容")
    async def cmd_learn(interaction: discord.Interaction, content: str):
        await interaction.response.defer()
        user_id = str(interaction.user.id)
        prompt = (
            f"The user explicitly asked you to memorize the following. "
            f"Store it as a fact using `memory_store_fact` with appropriate tags. "
            f"Confirm what you stored.\n\n{content}"
        )
        try:
            async with asyncio.timeout(config.agent_query_timeout):
                response_text, _ = await agent.run_query(
                    prompt, user_id=user_id,
                )
            for part in chunk_text(response_text):
                await interaction.followup.send(part)
        except TimeoutError:
            await interaction.followup.send("記憶儲存超時，請稍後再試。")
        except Exception:
            log.exception("Learn command failed")
            await interaction.followup.send("記憶儲存失敗。")

    @tree.command(name="consolidate", description="觸發知識整理 (Admin)")
    @require_role(Role.ADMIN, config.admin_role_ids, config.trusted_role_ids)
    async def cmd_consolidate(interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            async with asyncio.timeout(config.agent_query_timeout):
                response_text, _ = await agent.run_query(
                    "請進行知識整理 (consolidate knowledge)",
                    user_id=str(interaction.user.id),
                )
            for part in chunk_text(response_text):
                await interaction.followup.send(part)
        except TimeoutError:
            await interaction.followup.send("知識整理超時，請稍後再試。")
        except Exception:
            log.exception("Consolidation failed")
            await interaction.followup.send("知識整理失敗。")

    @tree.command(name="skill_remove", description="移除已安裝的技能 (Admin)")
    @app_commands.describe(name="要移除的技能名稱")
    @require_role(Role.ADMIN, config.admin_role_ids, config.trusted_role_ids)
    async def cmd_skill_remove(interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        try:
            registry = agent.get_skill_registry()
            if not registry.get_skill(name):
                await interaction.followup.send(f"找不到技能 `{name}`。")
                return
            registry.unregister_skill(name)
            await interaction.followup.send(f"技能 `{name}` 已移除。")
        except Exception:
            log.exception("Failed to remove skill %s", name)
            await interaction.followup.send("移除技能失敗。")

    @tree.command(name="memory_forget", description="刪除特定主題的記憶 (Admin)")
    @app_commands.describe(topic="要遺忘的主題關鍵字")
    @require_role(Role.ADMIN, config.admin_role_ids, config.trusted_role_ids)
    async def cmd_memory_forget(interaction: discord.Interaction, topic: str):
        await interaction.response.defer(ephemeral=True)
        try:
            engine = agent.get_engine()
            count = engine.forget_topic(topic)
            if count == 0:
                await interaction.followup.send(f"找不到與 `{topic}` 相關的記憶。")
            else:
                await interaction.followup.send(f"已遺忘 {count} 條與 `{topic}` 相關的記憶。")
        except Exception:
            log.exception("Failed to forget memories about %s", topic)
            await interaction.followup.send("刪除記憶失敗。")

    @tree.command(name="ask", description="單次問答")
    @app_commands.describe(question="你想問的問題")
    async def cmd_ask(interaction: discord.Interaction, question: str):
        await interaction.response.defer()
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        try:
            async with asyncio.timeout(config.agent_query_timeout):
                response_text, response_files = await agent.run_query(
                    question, user_id=user_id, guild_id=guild_id,
                )
            chunks = chunk_text(response_text)
            discord_files = [
                discord.File(io.BytesIO(f["data"]), filename=f["filename"])
                for f in response_files
            ]
            # Send first chunk as followup, then create thread for the rest
            first_msg = await interaction.followup.send(chunks[0], wait=True)
            if len(chunks) > 1 and not isinstance(interaction.channel, discord.DMChannel):
                try:
                    thread = await first_msg.create_thread(
                        name=f"Ask: {question[:50]}",
                        auto_archive_duration=60,
                    )
                    for i, part in enumerate(chunks[1:]):
                        is_last = i == len(chunks) - 2
                        if is_last and discord_files:
                            await thread.send(part, files=discord_files)
                        else:
                            await thread.send(part)
                except discord.HTTPException:
                    for part in chunks[1:]:
                        await interaction.followup.send(part)
            elif discord_files:
                await interaction.followup.send(files=discord_files)
        except TimeoutError:
            await interaction.followup.send("回答超時，請稍後再試。")
        except Exception:
            log.exception("Ask command failed")
            await interaction.followup.send("回答失敗。")

    @tree.command(name="chat", description="開始持續對話 thread")
    async def cmd_chat(interaction: discord.Interaction):
        if isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("DM 中不需要建立對話串，直接發訊息即可！")
            return
        await interaction.response.defer()
        display_name = interaction.user.display_name
        try:
            msg = await interaction.followup.send(
                f"已為 {display_name} 建立新的對話串！請在這裡繼續對話。", wait=True,
            )
            await msg.create_thread(
                name=f"Chat with {display_name}",
                auto_archive_duration=60,
            )
        except Exception:
            log.exception("Chat command failed")
            await interaction.followup.send("建立對話串失敗。")

    @tree.command(name="skill_add", description="新增自訂技能 (Trusted)")
    @app_commands.describe(name="技能名稱", code="技能的 Python 程式碼")
    @require_role(Role.TRUSTED, config.admin_role_ids, config.trusted_role_ids)
    async def cmd_skill_add(interaction: discord.Interaction, name: str, code: str):
        await interaction.response.defer(ephemeral=True)
        try:
            errors = validate_skill_code(code)
            if errors:
                await interaction.followup.send(
                    "技能驗證失敗:\n" + "\n".join(f"- {e}" for e in errors)
                )
                return
            # Extract metadata from code
            namespace: dict = {}
            exec(compile(code, f"<skill:{name}>", "exec"), namespace)  # noqa: S102
            metadata = SkillMetadata(
                name=name,
                description=namespace.get("SKILL_DESCRIPTION", ""),
                version=namespace.get("SKILL_VERSION", "1.0.0"),
                parameters={
                    k: v.__name__ if isinstance(v, type) else str(v)
                    for k, v in namespace.get("SKILL_PARAMETERS", {}).items()
                },
                source="user",
                enabled=True,
                installed_at=datetime.now(timezone.utc),
                installed_by=str(interaction.user.id),
                path=f"skills/installed/{name}.py",
            )
            registry = agent.get_skill_registry()
            save_skill_to_github(registry.store, metadata, code)
            registry.register_skill(metadata, code)
            await interaction.followup.send(f"技能 `{name}` 已安裝並啟用。")
        except Exception:
            log.exception("Failed to add skill %s", name)
            await interaction.followup.send("新增技能失敗。")

    @tree.command(name="skill_list", description="列出已安裝的技能")
    async def cmd_skill_list(interaction: discord.Interaction):
        registry = agent.get_skill_registry()
        skills = registry.get_skill_list()
        if not skills:
            await interaction.response.send_message("目前沒有已安裝的技能。")
            return
        embed = discord.Embed(title="已安裝技能", color=0x5865F2)
        for s in skills:
            status = "✅ 啟用" if s.enabled else "⬜ 停用"
            embed.add_field(
                name=f"{s.name} v{s.version} [{status}]",
                value=s.description[:100] or "(無描述)",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @tree.command(name="memory_search", description="搜尋 agent 記憶 (Trusted)")
    @app_commands.describe(query="搜尋關鍵字")
    @require_role(Role.TRUSTED, config.admin_role_ids, config.trusted_role_ids)
    async def cmd_memory_search(interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            engine = agent.get_engine()
            results = engine.indexer.search(query, top_k=10)
            if not results:
                await interaction.followup.send(f"找不到與 `{query}` 相關的記憶。")
                return
            lines = []
            for i, entry in enumerate(results, 1):
                date_str = entry.updated_at.strftime("%Y-%m-%d")
                lines.append(f"{i}. [{entry.type}] {entry.summary} ({date_str})")
            text = "\n".join(lines)
            for part in chunk_text(text):
                await interaction.followup.send(part)
        except Exception:
            log.exception("Memory search failed")
            await interaction.followup.send("搜尋記憶失敗。")

    @tree.command(name="profile_set", description="設定你的使用者偏好")
    @app_commands.describe(key="設定項目", value="設定值")
    @app_commands.choices(key=[
        app_commands.Choice(name="display_name", value="display_name"),
        app_commands.Choice(name="preferred_language", value="preferred_language"),
        app_commands.Choice(name="notes", value="notes"),
    ])
    async def cmd_profile_set(
        interaction: discord.Interaction,
        key: app_commands.Choice[str],
        value: str,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            user_id = str(interaction.user.id)
            engine = agent.get_engine()
            profile = engine.get_user_profile(user_id)
            if profile is None:
                profile = UserProfile(
                    user_id=user_id,
                    display_name=interaction.user.display_name,
                )
            k = key.value
            if k == "display_name":
                profile.display_name = value
            elif k == "preferred_language":
                profile.preferred_language = value
            elif k == "notes":
                profile.notes.append(value)
            profile.last_seen = datetime.now(timezone.utc)
            engine.update_user_profile(profile)
            await interaction.followup.send(f"已更新 `{k}` 為 `{value}`。")
        except Exception:
            log.exception("Profile set failed")
            await interaction.followup.send("設定失敗。")

    @tree.command(name="profile_show", description="顯示你的個人資料")
    async def cmd_profile_show(interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        engine = agent.get_engine()
        profile = engine.get_user_profile(user_id)
        if profile is None:
            await interaction.response.send_message(
                "你還沒有設定個人資料。使用 `/profile_set` 來設定。", ephemeral=True,
            )
            return
        embed = discord.Embed(
            title=f"{profile.display_name or interaction.user.display_name} 的個人資料",
            color=0x5865F2,
        )
        embed.add_field(name="Display Name", value=profile.display_name or "(未設定)")
        embed.add_field(
            name="Preferred Language", value=profile.preferred_language or "(未設定)",
        )
        embed.add_field(
            name="Notes",
            value="\n".join(profile.notes) if profile.notes else "(無)",
            inline=False,
        )
        embed.add_field(
            name="First Seen", value=profile.first_seen.strftime("%Y-%m-%d %H:%M UTC"),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.error
    async def on_app_command_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(str(error), ephemeral=True)
        else:
            log.exception("Slash command error: %s", error)
            if not interaction.response.is_done():
                await interaction.response.send_message("指令執行失敗。", ephemeral=True)

    # ── Events ──────────────────────────────────────────────────────

    @client.event
    async def setup_hook():
        # Graceful shutdown on SIGTERM / SIGINT
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(_graceful_shutdown()),
            )

    async def _graceful_shutdown():
        log.info("Shutting down gracefully...")
        session_manager.cleanup_expired()
        rate_limiter.cleanup(max_age=0)
        await client.close()

    @client.event
    async def on_ready():
        await tree.sync()
        log.info(f"Bot connected as {client.user} (id={client.user.id}), slash commands synced")

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

        # ── Rate limiting ──
        guild_id = str(message.guild.id) if message.guild else ""
        user_id = str(message.author.id)
        user_role = get_user_role(
            message.author, config.admin_role_ids, config.trusted_role_ids
        )
        if user_role < Role.ADMIN:
            limit = (
                config.rate_limit_trusted
                if user_role >= Role.TRUSTED
                else config.rate_limit_everyone
            )
            if not rate_limiter.check(guild_id, user_id, limit):
                await message.reply("你的訊息太頻繁了，請稍後再試。")
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
        channel_id = str(response_channel.id)
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

        async with _get_semaphore(guild_id):
            # Show typing indicator while processing
            async with response_channel.typing():
                try:
                    async with asyncio.timeout(config.agent_query_timeout):
                        response_text, response_files = await agent.run_query(
                            user_text_or_fallback,
                            user_id=user_id,
                            guild_id=guild_id,
                            conversation_history=history,
                            attachments=attachments,
                        )
                except TimeoutError:
                    log.warning("Agent query timed out for user %s", user_id)
                    response_text = "我在這個問題上花太久了，請試著簡化問題或稍後再試。"
                    response_files = []
                except Exception:
                    log.exception("Agent query failed")
                    response_text = "抱歉，處理時發生了錯誤。請稍後再試。"
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

        # Opportunistic cleanup
        session_manager.cleanup_expired()
        rate_limiter.cleanup()

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
            engine = agent.get_engine()
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
