import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord

from cogs.gym_challenge import GymChallengeCog
from core.database import DatabaseManager


class FakeRole:
    def __init__(self, role_id: int, name: str):
        self.id = role_id
        self.name = name

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"


class FakeGuild:
    def __init__(self, guild_id: int, name: str, roles: dict[int, FakeRole]):
        self.id = guild_id
        self.name = name
        self._roles = dict(roles)

    def get_role(self, role_id: int):
        return self._roles.get(role_id)


class FakeMember:
    def __init__(
        self,
        member_id: int,
        guild: FakeGuild,
        roles: list[FakeRole],
        *,
        fail_dm: bool = False,
        fail_add_role_ids: set[int] | None = None,
    ):
        self.id = member_id
        self.guild = guild
        self.roles = list(roles)
        self.fail_dm = fail_dm
        self.fail_add_role_ids = set(fail_add_role_ids or ())
        self.added_roles: list[FakeRole] = []
        self.removed_roles: list[FakeRole] = []
        self.dms: list[str] = []

    async def add_roles(self, role: FakeRole, *, reason: str | None = None):
        if role.id in self.fail_add_role_ids:
            raise RuntimeError("add_roles failed")
        self.added_roles.append(role)
        if role not in self.roles:
            self.roles.append(role)

    async def remove_roles(self, role: FakeRole, *, reason: str | None = None):
        self.removed_roles.append(role)
        self.roles = [r for r in self.roles if r.id != role.id]

    async def send(self, content: str):
        if self.fail_dm:
            response = SimpleNamespace(status=403, reason="Forbidden")
            raise discord.Forbidden(response, "Cannot send messages to this user")
        self.dms.append(content)


class FakeInteractionResponse:
    def __init__(self):
        self.done = False
        self.edits: list[dict] = []

    def is_done(self) -> bool:
        return self.done

    async def edit_message(self, **kwargs):
        self.done = True
        self.edits.append(kwargs)


class FakeSuccessInteraction:
    def __init__(self, member: FakeMember):
        self.user = member
        self.guild = member.guild
        self.response = FakeInteractionResponse()
        self.original_edits: list[dict] = []

    async def edit_original_response(self, **kwargs):
        self.original_edits.append(kwargs)


class CompletionRewardsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(Path(self._tmpdir.name) / "test.db")
        await self.db.initialize()

        self.bot = SimpleNamespace(get_cog=lambda _name: None)
        self.cog = GymChallengeCog(self.bot, eligibility_checker=SimpleNamespace())
        self.cog.db = self.db

        self.guild_id = 5001
        self.user_id = 9001
        self.panel_id = 7001
        self.gym_id = "gym-1"

        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO gyms (gym_id, guild_id, name, is_enabled)
                VALUES (?, ?, ?, TRUE)
                """,
                (self.gym_id, str(self.guild_id), "道馆一"),
            )
            await conn.commit()

    async def asyncTearDown(self):
        self._tmpdir.cleanup()

    async def _insert_panel(
        self,
        *,
        role_to_add_ids: list[str] | None,
        role_to_remove_ids: list[str] | None,
        blacklist_enabled: bool = True,
    ):
        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO challenge_panels (
                    message_id, guild_id, channel_id,
                    role_to_add_ids, role_to_remove_ids,
                    associated_gyms, blacklist_enabled, completion_threshold,
                    is_ultimate_gym
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, FALSE)
                """,
                (
                    str(self.panel_id),
                    str(self.guild_id),
                    "8001",
                    json.dumps(role_to_add_ids) if role_to_add_ids is not None else None,
                    json.dumps(role_to_remove_ids) if role_to_remove_ids is not None else None,
                    json.dumps([self.gym_id]),
                    blacklist_enabled,
                    None,
                ),
            )
            await conn.commit()

    async def _mark_completed(self):
        async with self.db.get_connection() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO user_progress (user_id, guild_id, gym_id) VALUES (?, ?, ?)",
                (str(self.user_id), str(self.guild_id), self.gym_id),
            )
            await conn.commit()

    async def _mark_claimed(self, role_id: int):
        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO claimed_role_rewards (guild_id, user_id, role_id, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (str(self.guild_id), str(self.user_id), str(role_id), "2026-01-01T00:00:00+00:00"),
            )
            await conn.commit()

    def _make_session(self, *, wrong_answers: list[dict] | None = None):
        return SimpleNamespace(
            user_id=str(self.user_id),
            guild_id=str(self.guild_id),
            gym_id=self.gym_id,
            gym_info={"name": "道馆一"},
            panel_message_id=self.panel_id,
            is_ultimate=False,
            questions_for_session=[{"question": "Q1"}],
            mistakes_made=len(wrong_answers or []),
            allowed_mistakes=3,
            wrong_answers=list(wrong_answers or []),
            get_completion_time=lambda: 12.5,
        )

    async def test_duplicate_claim_preserves_old_role_and_reports_in_embed_and_dm(self):
        new_role = FakeRole(101, "正式成员")
        old_role = FakeRole(202, "待考核")
        guild = FakeGuild(self.guild_id, "测试道馆服", {101: new_role, 202: old_role})
        member = FakeMember(self.user_id, guild, roles=[old_role])

        await self._insert_panel(role_to_add_ids=["101"], role_to_remove_ids=["202"])
        await self._mark_claimed(101)

        interaction = FakeSuccessInteraction(member)
        session = self._make_session()
        await self.cog._handle_challenge_success(interaction, session)

        # New role blocked by duplicate-claim record; old role MUST remain intact.
        self.assertEqual(member.added_roles, [])
        self.assertEqual(member.removed_roles, [])
        self.assertEqual([r.id for r in member.roles], [202])

        # Challenge success Embed includes the clear warning field (visible even if DM is disabled).
        self.assertEqual(len(interaction.response.edits), 1)
        embed = interaction.response.edits[0]["embed"]
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("提示", embed.fields[0].name)
        self.assertIn(new_role.mention, embed.fields[0].value)
        self.assertIn(old_role.mention, embed.fields[0].value)
        self.assertIn("已保留原有身份组", embed.fields[0].value)
        self.assertIn("联系服务器管理员", embed.fields[0].value)

        # DM resolves role IDs to current guild role names rather than raw <@&id> mentions.
        self.assertEqual(len(member.dms), 1)
        self.assertIn("@正式成员", member.dms[0])
        self.assertIn("@待考核", member.dms[0])
        self.assertNotIn("<@&101>", member.dms[0])
        self.assertNotIn("<@&202>", member.dms[0])

    async def test_duplicate_claim_still_shows_embed_notice_when_dm_is_closed(self):
        new_role = FakeRole(101, "正式成员")
        old_role = FakeRole(202, "待考核")
        guild = FakeGuild(self.guild_id, "测试道馆服", {101: new_role, 202: old_role})
        member = FakeMember(self.user_id, guild, roles=[old_role], fail_dm=True)

        await self._insert_panel(role_to_add_ids=["101"], role_to_remove_ids=["202"])
        await self._mark_claimed(101)

        interaction = FakeSuccessInteraction(member)
        await self.cog._handle_challenge_success(interaction, self._make_session())

        self.assertEqual(member.removed_roles, [])
        self.assertEqual(member.dms, [])
        embed = interaction.response.edits[0]["embed"]
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("未重复发放身份组", embed.fields[0].value)
        self.assertIn("已保留原有身份组", embed.fields[0].value)

    async def test_normal_completion_resolves_current_role_names_in_dm_even_after_rename(self):
        new_role = FakeRole(101, "初版正式_成员")
        old_role = FakeRole(202, "初版新人")
        guild = FakeGuild(self.guild_id, "测试道馆服", {101: new_role, 202: old_role})
        await self._insert_panel(role_to_add_ids=["101"], role_to_remove_ids=["202"])
        await self._mark_completed()

        # Simulate role rename before completion notification is sent.
        new_role.name = "改名后_正式成员"
        old_role.name = "改名后*新人"

        member = FakeMember(self.user_id, guild, roles=[old_role])
        field = await self.cog._check_completion_rewards(member, self._make_session())

        self.assertEqual([r.id for r in member.added_roles], [101])
        self.assertEqual([r.id for r in member.removed_roles], [202])
        self.assertEqual([r.id for r in member.roles], [101])

        self.assertIsNotNone(field)
        self.assertIn(new_role.mention, field["value"])
        self.assertIn(old_role.mention, field["value"])

        self.assertEqual(len(member.dms), 1)
        self.assertIn("🎉 恭喜你", member.dms[0])
        self.assertIn(r"@改名后\_正式成员", member.dms[0])
        self.assertIn(r"@改名后\*新人", member.dms[0])
        self.assertNotIn("<@&101>", member.dms[0])
        self.assertNotIn("<@&202>", member.dms[0])

    async def test_add_role_failure_or_missing_role_preserves_old_role(self):
        new_role = FakeRole(101, "正式成员")
        old_role = FakeRole(202, "待考核")
        guild = FakeGuild(self.guild_id, "测试道馆服", {101: new_role, 202: old_role})
        await self._insert_panel(role_to_add_ids=["101"], role_to_remove_ids=["202"])
        await self._mark_completed()

        # Case 1: add_roles raises an exception.
        member_fail = FakeMember(self.user_id, guild, roles=[old_role], fail_add_role_ids={101})
        field = await self.cog._check_completion_rewards(member_fail, self._make_session())
        self.assertEqual(member_fail.removed_roles, [])
        self.assertEqual([r.id for r in member_fail.roles], [202])
        self.assertFalse(await self.cog._has_claimed_reward(str(self.guild_id), str(self.user_id), "101"))
        self.assertIn("未能发放身份组", field["value"])
        self.assertIn("已保留原有身份组", field["value"])

        # Case 2: configured reward role no longer exists in guild.
        guild_missing = FakeGuild(self.guild_id, "测试道馆服", {202: old_role})
        member_missing = FakeMember(self.user_id, guild_missing, roles=[old_role])
        field_missing = await self.cog._check_completion_rewards(member_missing, self._make_session())
        self.assertEqual(member_missing.removed_roles, [])
        self.assertIn("身份组 ID `101`", field_missing["value"])
        self.assertIn("已保留原有身份组", field_missing["value"])

    async def test_replaying_while_already_holding_reward_role_is_silent(self):
        new_role = FakeRole(101, "正式成员")
        old_role = FakeRole(202, "待考核")
        guild = FakeGuild(self.guild_id, "测试道馆服", {101: new_role, 202: old_role})
        member = FakeMember(self.user_id, guild, roles=[new_role])

        await self._insert_panel(role_to_add_ids=["101"], role_to_remove_ids=["202"])
        await self._mark_completed()
        await self._mark_claimed(101)

        field = await self.cog._check_completion_rewards(member, self._make_session())
        self.assertIsNone(field)
        self.assertEqual(member.added_roles, [])
        self.assertEqual(member.removed_roles, [])
        self.assertEqual(member.dms, [])

    async def test_remove_only_panel_still_removes_role_and_formats_dm_name(self):
        old_role = FakeRole(202, "待考核")
        guild = FakeGuild(self.guild_id, "测试道馆服", {202: old_role})
        member = FakeMember(self.user_id, guild, roles=[old_role])

        await self._insert_panel(role_to_add_ids=[], role_to_remove_ids=["202"])
        await self._mark_completed()

        field = await self.cog._check_completion_rewards(member, self._make_session())
        self.assertEqual([r.id for r in member.removed_roles], [202])
        self.assertIsNotNone(field)
        self.assertEqual(len(member.dms), 1)
        self.assertIn("@待考核", member.dms[0])
        self.assertNotIn("<@&202>", member.dms[0])

    async def test_success_embed_never_exceeds_25_fields_with_reward_and_wrong_answers(self):
        new_role = FakeRole(101, "正式成员")
        old_role = FakeRole(202, "待考核")
        guild = FakeGuild(self.guild_id, "测试道馆服", {101: new_role, 202: old_role})
        member = FakeMember(self.user_id, guild, roles=[old_role])

        await self._insert_panel(role_to_add_ids=["101"], role_to_remove_ids=["202"])
        await self._mark_claimed(101)
        wrong_answers = [
            ({"text": f"题目 {i} " + ("x" * 600), "correct_answer": "B"}, "A")
            for i in range(25)
        ]
        interaction = FakeSuccessInteraction(member)
        await self.cog._handle_challenge_success(interaction, self._make_session(wrong_answers=wrong_answers))

        embed = interaction.response.edits[0]["embed"]
        self.assertEqual(len(embed.fields), 25)
        self.assertIn("提示", embed.fields[0].name)


if __name__ == "__main__":
    unittest.main()
