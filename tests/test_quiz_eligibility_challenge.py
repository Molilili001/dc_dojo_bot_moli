import asyncio
import contextlib
import io
import unittest
from types import SimpleNamespace

from bot import DiscordBot
from cogs.gym_challenge import GymChallengeCog, setup
from core.quiz_eligibility import (
    TARGET_GUILD_ID,
    TUTORIAL_DIRECTORY_URL,
    EligibilityFailureCategory,
    EligibilityConfigurationError,
    EligibilityOutcome,
    EligibilityUnavailable,
)
from views.challenge_views import StartChallengeView


TEST_DISCORD_USER_ID = "100000000000000001"


class FakeEligibilityChecker:
    def __init__(self, outcome=EligibilityOutcome.ELIGIBLE):
        self.outcome = outcome
        self.calls = []

    async def check(self, discord_user_id):
        self.calls.append(discord_user_id)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class BlockingEligibilityChecker(FakeEligibilityChecker):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def check(self, discord_user_id):
        self.calls.append(discord_user_id)
        self.entered.set()
        await self.release.wait()
        return self.outcome


class FakeInteraction:
    def __init__(self, guild_id=TARGET_GUILD_ID, cog=None):
        self.user = SimpleNamespace(id=int(TEST_DISCORD_USER_ID), roles=[])
        self.guild = SimpleNamespace(id=int(guild_id))
        self.edits = []
        self.followups = []
        self.followup = SimpleNamespace(send=self._send_followup)
        self.response = FakeInteractionResponse()
        self.client = SimpleNamespace(
            get_cog=lambda name: cog if name == "GymChallengeCog" else None
        )

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)

    async def _send_followup(self, *args, **kwargs):
        self.followups.append((args, kwargs))


class FakeInteractionResponse:
    def __init__(self):
        self.deferred = False
        self.defer_kwargs = []

    def is_done(self):
        return self.deferred

    async def defer(self, **kwargs):
        self.deferred = True
        self.defer_kwargs.append(kwargs)


class ChallengeHarness(GymChallengeCog):
    def __init__(self, checker, *, ban_entry=None):
        super().__init__(SimpleNamespace(), eligibility_checker=checker)
        self.ban_entry = ban_entry
        self.displayed_sessions = []
        self.failure_mutations = 0

    async def _get_challenge_ban_entry(self, guild_id, member):
        return self.ban_entry

    async def _display_next_question(self, interaction, session, from_modal=False):
        self.displayed_sessions.append(session)

    def _format_challenge_ban_message(self, entry, member):
        return "challenge-ban-message"

    async def _increment_failure(self, user_id, guild_id, gym_id):
        self.failure_mutations += 1
        raise AssertionError("eligibility rejection must not mutate failures")


def make_session(*, guild_id=TARGET_GUILD_ID, is_ultimate=False):
    return SimpleNamespace(
        user_id=TEST_DISCORD_USER_ID,
        guild_id=str(guild_id),
        gym_id="ultimate" if is_ultimate else "synthetic-gym",
        is_ultimate=is_ultimate,
        started=False,
    )


class QuizEligibilityChallengeTests(unittest.IsolatedAsyncioTestCase):
    async def test_challenge_configuration_failure_does_not_stop_other_cog_loads(self):
        attempted = []

        async def load_extension(name):
            attempted.append(name)
            if name == "cogs.gym_challenge":
                raise EligibilityConfigurationError(
                    "quiz eligibility configuration invalid"
                )

        loader = SimpleNamespace(
            initial_cogs=["cogs.gym_challenge", "cogs.user_progress"],
            optional_cogs=[],
            load_extension=load_extension,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertLogs("bot", level="ERROR") as logs:
                await DiscordBot._load_cogs(loader)

        self.assertEqual(
            attempted,
            ["cogs.gym_challenge", "cogs.user_progress"],
        )
        self.assertIn(
            "quiz eligibility configuration invalid",
            "\n".join(logs.output),
        )

    async def test_invalid_configuration_prevents_challenge_cog_loading(self):
        added_cogs = []

        async def add_cog(cog):
            added_cogs.append(cog)

        bot = SimpleNamespace(config={}, add_cog=add_cog)

        with self.assertRaisesRegex(
            EligibilityConfigurationError,
            "^quiz eligibility configuration invalid$",
        ):
            await setup(bot)

        self.assertEqual(added_cogs, [])

    async def test_valid_configuration_loads_challenge_cog(self):
        added_cogs = []

        async def add_cog(cog):
            added_cogs.append(cog)

        bot = SimpleNamespace(
            config={
                "QUIZ_ELIGIBILITY_API_KEY": (
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                )
            },
            add_cog=add_cog,
        )

        await setup(bot)

        self.assertEqual(len(added_cogs), 1)
        self.assertIsInstance(added_cogs[0], GymChallengeCog)

    async def test_start_button_enters_shared_coordination_seam_before_started(self):
        checker = FakeEligibilityChecker()
        cog = ChallengeHarness(checker)
        session = make_session()
        interaction = FakeInteraction(cog=cog)
        cog.active_challenges[TEST_DISCORD_USER_ID] = session
        cog.user_challenge_locks[TEST_DISCORD_USER_ID] = asyncio.Lock()
        view = StartChallengeView(session.gym_id)
        start_button = view.children[0]

        await start_button.callback(interaction)

        self.assertEqual(checker.calls, [TEST_DISCORD_USER_ID])
        self.assertTrue(session.started)
        self.assertEqual(cog.displayed_sessions, [session])
        self.assertEqual(interaction.response.defer_kwargs, [{"ephemeral": True}])

    async def test_start_button_stops_tutorial_for_private_missing_response(self):
        checker = FakeEligibilityChecker(EligibilityOutcome.FINGERPRINT_MISSING)
        cog = ChallengeHarness(checker)
        session = make_session()
        interaction = FakeInteraction(cog=cog)
        cog.active_challenges[TEST_DISCORD_USER_ID] = session
        view = StartChallengeView(session.gym_id)
        start_button = view.children[0]

        await start_button.callback(interaction)

        self.assertTrue(view.is_finished())
        self.assertEqual(interaction.response.defer_kwargs, [{"ephemeral": True}])
        self.assertEqual(len(interaction.edits), 1)
        self.assertNotIn(TEST_DISCORD_USER_ID, cog.active_challenges)

    async def test_target_guild_eligible_session_enters_first_question(self):
        checker = FakeEligibilityChecker()
        cog = ChallengeHarness(checker)
        session = make_session()
        interaction = FakeInteraction()
        cog.active_challenges[TEST_DISCORD_USER_ID] = session
        cog.user_challenge_locks[TEST_DISCORD_USER_ID] = asyncio.Lock()

        await cog.display_question(interaction, session)

        self.assertEqual(checker.calls, [TEST_DISCORD_USER_ID])
        self.assertTrue(session.started)
        self.assertEqual(cog.displayed_sessions, [session])
        self.assertIs(cog.active_challenges[TEST_DISCORD_USER_ID], session)
        self.assertEqual(interaction.edits, [])
        self.assertEqual(interaction.followups, [])

    async def test_missing_observation_privately_cleans_unstarted_session_without_penalty(self):
        checker = FakeEligibilityChecker(EligibilityOutcome.FINGERPRINT_MISSING)
        cog = ChallengeHarness(checker)
        session = make_session()
        interaction = FakeInteraction()
        cog.active_challenges[TEST_DISCORD_USER_ID] = session
        cog.user_challenge_locks[TEST_DISCORD_USER_ID] = asyncio.Lock()

        await cog.display_question(interaction, session)

        self.assertFalse(session.started)
        self.assertNotIn(TEST_DISCORD_USER_ID, cog.active_challenges)
        self.assertNotIn(TEST_DISCORD_USER_ID, cog.user_challenge_locks)
        self.assertEqual(cog.displayed_sessions, [])
        self.assertEqual(len(interaction.edits), 1)
        message = interaction.edits[0]["content"]
        self.assertIn(TUTORIAL_DIRECTORY_URL, message)
        self.assertIn("等待网站显示观测成功", message)
        self.assertIn("重新选择道馆", message)
        self.assertIn("未记录挑战失败", message)
        self.assertEqual(interaction.edits[0]["embed"], None)
        self.assertEqual(interaction.edits[0]["view"], None)
        self.assertEqual(interaction.followups, [])
        self.assertEqual(cog.failure_mutations, 0)

    async def test_technical_failure_is_generic_private_and_has_no_penalty(self):
        checker = FakeEligibilityChecker(
            EligibilityUnavailable(EligibilityFailureCategory.NETWORK)
        )
        cog = ChallengeHarness(checker)
        session = make_session()
        interaction = FakeInteraction()
        cog.active_challenges[TEST_DISCORD_USER_ID] = session
        cog.user_challenge_locks[TEST_DISCORD_USER_ID] = asyncio.Lock()

        await cog.display_question(interaction, session)

        self.assertFalse(session.started)
        self.assertNotIn(TEST_DISCORD_USER_ID, cog.active_challenges)
        self.assertNotIn(TEST_DISCORD_USER_ID, cog.user_challenge_locks)
        self.assertEqual(cog.displayed_sessions, [])
        self.assertEqual(len(interaction.edits), 1)
        message = interaction.edits[0]["content"]
        self.assertIn("答题资格服务暂时不可用", message)
        self.assertIn("尚未开始", message)
        self.assertIn("未记录挑战失败", message)
        self.assertNotIn("network", message)
        self.assertNotIn(TEST_DISCORD_USER_ID, message)
        self.assertNotIn("http", message)
        self.assertEqual(interaction.followups, [])
        self.assertEqual(cog.failure_mutations, 0)

    async def test_challenge_ban_precedes_eligibility_request(self):
        checker = FakeEligibilityChecker()
        cog = ChallengeHarness(checker, ban_entry={"reason": "synthetic"})
        session = make_session()
        interaction = FakeInteraction()
        cog.active_challenges[TEST_DISCORD_USER_ID] = session
        cog.user_challenge_locks[TEST_DISCORD_USER_ID] = asyncio.Lock()

        await cog.display_question(interaction, session)

        self.assertEqual(checker.calls, [])
        self.assertFalse(session.started)
        self.assertNotIn(TEST_DISCORD_USER_ID, cog.active_challenges)
        self.assertNotIn(TEST_DISCORD_USER_ID, cog.user_challenge_locks)
        self.assertEqual(cog.displayed_sessions, [])
        self.assertEqual(
            interaction.followups,
            [(('challenge-ban-message',), {"ephemeral": True})],
        )

    async def test_session_expiring_during_request_cannot_start_a_stale_question(self):
        checker = BlockingEligibilityChecker()
        cog = ChallengeHarness(checker)
        session = make_session()
        interaction = FakeInteraction()
        cog.active_challenges[TEST_DISCORD_USER_ID] = session
        cog.user_challenge_locks[TEST_DISCORD_USER_ID] = asyncio.Lock()

        start_task = asyncio.create_task(
            cog.display_question(interaction, session)
        )
        await checker.entered.wait()
        cog._cleanup_user_session(TEST_DISCORD_USER_ID)
        checker.release.set()
        await start_task

        self.assertEqual(checker.calls, [TEST_DISCORD_USER_ID])
        self.assertFalse(session.started)
        self.assertEqual(cog.displayed_sessions, [])

    async def test_non_target_guild_keeps_existing_flow_without_request(self):
        checker = FakeEligibilityChecker()
        cog = ChallengeHarness(checker)
        session = make_session(guild_id="200000000000000002")
        interaction = FakeInteraction(guild_id=session.guild_id)
        cog.active_challenges[TEST_DISCORD_USER_ID] = session

        await cog.display_question(interaction, session)

        self.assertEqual(checker.calls, [])
        self.assertTrue(session.started)
        self.assertEqual(cog.displayed_sessions, [session])

    async def test_ultimate_uses_the_same_target_guild_gate(self):
        checker = FakeEligibilityChecker()
        cog = ChallengeHarness(checker)
        session = make_session(is_ultimate=True)
        interaction = FakeInteraction()
        cog.active_challenges[TEST_DISCORD_USER_ID] = session

        await cog.display_question(interaction, session)

        self.assertEqual(checker.calls, [TEST_DISCORD_USER_ID])
        self.assertTrue(session.started)
        self.assertEqual(cog.displayed_sessions, [session])

    async def test_double_start_serializes_one_decision_and_one_question(self):
        checker = BlockingEligibilityChecker()
        cog = ChallengeHarness(checker)
        session = make_session()
        first_interaction = FakeInteraction()
        second_interaction = FakeInteraction()
        cog.active_challenges[TEST_DISCORD_USER_ID] = session
        cog.user_challenge_locks[TEST_DISCORD_USER_ID] = asyncio.Lock()

        first = asyncio.create_task(
            cog.display_question(first_interaction, session)
        )
        await checker.entered.wait()
        second = asyncio.create_task(
            cog.display_question(second_interaction, session)
        )
        await asyncio.sleep(0)
        self.assertEqual(checker.calls, [TEST_DISCORD_USER_ID])

        checker.release.set()
        await asyncio.gather(first, second)

        self.assertEqual(checker.calls, [TEST_DISCORD_USER_ID])
        self.assertTrue(session.started)
        self.assertEqual(cog.displayed_sessions, [session])

    async def test_new_session_after_missing_observation_makes_a_new_request(self):
        checker = FakeEligibilityChecker(EligibilityOutcome.FINGERPRINT_MISSING)
        cog = ChallengeHarness(checker)
        first_session = make_session()
        cog.active_challenges[TEST_DISCORD_USER_ID] = first_session

        await cog.display_question(FakeInteraction(), first_session)

        second_session = make_session()
        checker.outcome = EligibilityOutcome.ELIGIBLE
        cog.active_challenges[TEST_DISCORD_USER_ID] = second_session
        await cog.display_question(FakeInteraction(), second_session)

        self.assertEqual(
            checker.calls,
            [TEST_DISCORD_USER_ID, TEST_DISCORD_USER_ID],
        )
        self.assertTrue(second_session.started)
        self.assertEqual(cog.displayed_sessions, [second_session])


if __name__ == "__main__":
    unittest.main()
