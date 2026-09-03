import asyncio
import unittest

import aiohttp

from core.quiz_eligibility import (
    AiohttpEligibilityTransport,
    ELIGIBILITY_ENDPOINT,
    ELIGIBILITY_TIMEOUT_SECONDS,
    EligibilityConfigurationError,
    EligibilityFailureCategory,
    EligibilityHttpResponse,
    EligibilityOutcome,
    EligibilityUnavailable,
    QuizEligibilityClient,
    eligibility_api_key_from_config,
)


TEST_API_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
TEST_DISCORD_USER_ID = "100000000000000001"


class RecordingTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post_json(self, url, *, headers, json_body, total_timeout):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "total_timeout": total_timeout,
            }
        )
        return self.response


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeSession:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeSessionFactory:
    def __init__(self, session):
        self.session = session
        self.timeouts = []

    def __call__(self, *, timeout):
        self.timeouts.append(timeout.total)
        return self.session


class QuizEligibilityClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_eligible_response_uses_one_bounded_minimal_request(self):
        transport = RecordingTransport(
            EligibilityHttpResponse(status=200, body={"eligible": True})
        )
        client = QuizEligibilityClient(TEST_API_KEY, transport=transport)

        outcome = await client.check(TEST_DISCORD_USER_ID)

        self.assertIs(outcome, EligibilityOutcome.ELIGIBLE)
        self.assertEqual(
            transport.calls,
            [
                {
                    "url": ELIGIBILITY_ENDPOINT,
                    "headers": {
                        "Authorization": f"Bearer {TEST_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    "json_body": {"discordUserId": TEST_DISCORD_USER_ID},
                    "total_timeout": ELIGIBILITY_TIMEOUT_SECONDS,
                }
            ],
        )

    async def test_fingerprint_missing_response_is_the_only_rejection_outcome(self):
        transport = RecordingTransport(
            EligibilityHttpResponse(
                status=200,
                body={"eligible": False, "reason": "fingerprint_missing"},
            )
        )
        client = QuizEligibilityClient(TEST_API_KEY, transport=transport)

        outcome = await client.check(TEST_DISCORD_USER_ID)

        self.assertIs(outcome, EligibilityOutcome.FINGERPRINT_MISSING)

    async def test_status_and_response_shape_fail_closed_with_fixed_categories(self):
        cases = [
            (301, None, EligibilityFailureCategory.HTTP_STATUS),
            (400, {"error": "request_rejected"}, EligibilityFailureCategory.HTTP_STATUS),
            (401, {"error": "request_rejected"}, EligibilityFailureCategory.HTTP_STATUS),
            (403, None, EligibilityFailureCategory.HTTP_STATUS),
            (413, None, EligibilityFailureCategory.HTTP_STATUS),
            (500, None, EligibilityFailureCategory.HTTP_STATUS),
            (503, None, EligibilityFailureCategory.HTTP_STATUS),
            (200, {"eligible": True, "extra": True}, EligibilityFailureCategory.MALFORMED_RESPONSE),
            (200, {"eligible": False}, EligibilityFailureCategory.MALFORMED_RESPONSE),
            (200, {"eligible": False, "reason": "expired"}, EligibilityFailureCategory.MALFORMED_RESPONSE),
            (200, {"eligible": 1}, EligibilityFailureCategory.MALFORMED_RESPONSE),
            (200, [True], EligibilityFailureCategory.MALFORMED_RESPONSE),
        ]

        for status, body, category in cases:
            with self.subTest(status=status, body=body):
                client = QuizEligibilityClient(
                    TEST_API_KEY,
                    transport=RecordingTransport(
                        EligibilityHttpResponse(status=status, body=body)
                    ),
                )

                with self.assertLogs("core.quiz_eligibility", level="WARNING"):
                    with self.assertRaises(EligibilityUnavailable) as raised:
                        await client.check(TEST_DISCORD_USER_ID)

                self.assertIs(raised.exception.category, category)

    async def test_invalid_json_is_malformed_without_logging_response_details(self):
        sensitive_response = "private response contents"
        session = FakeSession(
            FakeResponse(200, ValueError(sensitive_response))
        )
        client = QuizEligibilityClient(
            TEST_API_KEY,
            transport=AiohttpEligibilityTransport(
                session_factory=FakeSessionFactory(session)
            ),
        )

        with self.assertLogs("core.quiz_eligibility", level="WARNING") as logs:
            with self.assertRaises(EligibilityUnavailable) as raised:
                await client.check(TEST_DISCORD_USER_ID)

        self.assertIs(
            raised.exception.category,
            EligibilityFailureCategory.MALFORMED_RESPONSE,
        )
        self.assertNotIn(sensitive_response, "\n".join(logs.output))

    async def test_transport_failures_are_fixed_and_redacted(self):
        sensitive_fragments = [TEST_API_KEY, ELIGIBILITY_ENDPOINT, TEST_DISCORD_USER_ID]
        cases = [
            (
                asyncio.TimeoutError(" ".join(sensitive_fragments)),
                EligibilityFailureCategory.TIMEOUT,
            ),
            (
                aiohttp.ClientConnectionError(" ".join(sensitive_fragments)),
                EligibilityFailureCategory.NETWORK,
            ),
        ]

        for transport_error, category in cases:
            with self.subTest(category=category.value):
                session = FakeSession(transport_error)
                factory = FakeSessionFactory(session)
                client = QuizEligibilityClient(
                    TEST_API_KEY,
                    transport=AiohttpEligibilityTransport(session_factory=factory),
                )

                with self.assertLogs("core.quiz_eligibility", level="WARNING") as logs:
                    with self.assertRaises(EligibilityUnavailable) as raised:
                        await client.check(TEST_DISCORD_USER_ID)

                self.assertIs(raised.exception.category, category)
                self.assertEqual(factory.timeouts, [ELIGIBILITY_TIMEOUT_SECONDS])
                self.assertEqual(len(session.calls), 1)
                self.assertIs(session.calls[0][1]["allow_redirects"], False)
                output = "\n".join(logs.output)
                self.assertIn(category.value, output)
                for fragment in sensitive_fragments:
                    self.assertNotIn(fragment, output)


class EligibilityConfigurationTests(unittest.TestCase):
    def test_api_key_requires_unpadded_base64url_with_at_least_32_bytes(self):
        self.assertEqual(
            eligibility_api_key_from_config(
                {"QUIZ_ELIGIBILITY_API_KEY": TEST_API_KEY}
            ),
            TEST_API_KEY,
        )

        invalid_values = [
            None,
            123,
            "",
            "short",
            "REPLACE_WITH_BASE64URL_API_KEY",
            "A" * 33 + "=",
            "A" * 42 + "+",
            "A" * 41,
        ]
        for value in invalid_values:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(
                    EligibilityConfigurationError,
                    "^quiz eligibility configuration invalid$",
                ):
                    eligibility_api_key_from_config(
                        {"QUIZ_ELIGIBILITY_API_KEY": value}
                    )


if __name__ == "__main__":
    unittest.main()
