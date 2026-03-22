"""Three-tier permission model: Everyone < Trusted < Admin."""

import enum

import discord
from discord import app_commands


class Role(enum.IntEnum):
    EVERYONE = 0
    TRUSTED = 1
    ADMIN = 2


def get_user_role(
    member: discord.Member | discord.User,
    admin_role_ids: set[int],
    trusted_role_ids: set[int],
) -> Role:
    """Determine a user's permission tier from their Discord roles."""
    if isinstance(member, discord.Member):
        # Guild owner is always admin
        if member.guild.owner_id == member.id:
            return Role.ADMIN
        member_role_ids = {r.id for r in member.roles}
        if admin_role_ids & member_role_ids:
            return Role.ADMIN
        if trusted_role_ids & member_role_ids:
            return Role.TRUSTED
        return Role.EVERYONE
    # DM users (plain discord.User) get TRUSTED — not ADMIN to prevent escalation
    return Role.TRUSTED


def require_role(
    min_role: Role,
    admin_role_ids: set[int],
    trusted_role_ids: set[int],
):
    """Return a ``discord.app_commands.check`` that enforces *min_role*."""

    async def predicate(interaction: discord.Interaction) -> bool:
        user_role = get_user_role(interaction.user, admin_role_ids, trusted_role_ids)
        if user_role >= min_role:
            return True
        raise app_commands.CheckFailure("你沒有權限使用這個指令。")

    return app_commands.check(predicate)
