"""Tests for utils/permissions.py."""

from unittest.mock import MagicMock

import discord

from utils.permissions import Role, get_user_role


def _make_member(
    member_id: int = 100,
    role_ids: list[int] | None = None,
    owner_id: int = 999,
) -> MagicMock:
    """Create a mock discord.Member with the given roles."""
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.guild = MagicMock()
    member.guild.owner_id = owner_id

    roles = []
    for rid in (role_ids or []):
        role = MagicMock()
        role.id = rid
        roles.append(role)
    member.roles = roles
    return member


class TestGetUserRole:
    def test_guild_owner_is_admin(self):
        member = _make_member(member_id=42, owner_id=42)
        assert get_user_role(member, set(), set()) == Role.ADMIN

    def test_admin_role(self):
        member = _make_member(role_ids=[10, 20])
        assert get_user_role(member, admin_role_ids={20}, trusted_role_ids=set()) == Role.ADMIN

    def test_trusted_role(self):
        member = _make_member(role_ids=[30])
        assert get_user_role(member, admin_role_ids=set(), trusted_role_ids={30}) == Role.TRUSTED

    def test_no_matching_roles(self):
        member = _make_member(role_ids=[50])
        assert get_user_role(member, admin_role_ids={10}, trusted_role_ids={20}) == Role.EVERYONE

    def test_dm_user_is_everyone(self):
        user = MagicMock(spec=discord.User)
        user.id = 123
        assert get_user_role(user, admin_role_ids={10}, trusted_role_ids={20}) == Role.EVERYONE

    def test_admin_takes_precedence_over_trusted(self):
        member = _make_member(role_ids=[10, 20])
        assert get_user_role(member, admin_role_ids={10}, trusted_role_ids={20}) == Role.ADMIN
