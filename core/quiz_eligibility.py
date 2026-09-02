import asyncio
import base64
import binascii
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, NoReturn, Protocol

import aiohttp


logger = logging.getLogger(__name__)


DOJO_SITE_ORIGIN = "https://dojo.windkingmomfly.site"
ELIGIBILITY_ENDPOINT = f"{DOJO_SITE_ORIGIN}/api/internal/quiz-eligibility"
TARGET_GUILD_ID = "1134557553011998840"
TUTORIAL_DIRECTORY_URL = f"{DOJO_SITE_ORIGIN}/docs/tutorials/"
ELIGIBILITY_TIMEOUT_SECONDS = 5
API_KEY_CONFIG_NAME = "QUIZ_ELIGIBILITY_API_KEY"


class EligibilityConfigurationError(ValueError):
    pass


def eligibility_api_key_from_config(config: Mapping[str, Any]) -> str:
    value = config.get(API_KEY_CONFIG_NAME)
    valid_alphabet = isinstance(value, str) and re.fullmatch(
        r"[A-Za-z0-9_-]+", value
    )
    if not valid_alphabet or len(value) % 4 == 1:
        raise EligibilityConfigurationError(
            "quiz eligibility configuration invalid"
        )

    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        )
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise EligibilityConfigurationError(
            "quiz eligibility configuration invalid"
        ) from None
    if len(decoded) < 32:
        raise EligibilityConfigurationError(
            "quiz eligibility configuration invalid"
        )
    return value


class EligibilityOutcome(Enum):
    ELIGIBLE = "eligible"
    FINGERPRINT_MISSING = "fingerprint_missing"


class EligibilityFailureCategory(Enum):
    HTTP_STATUS = "http_status"
    MALFORMED_RESPONSE = "malformed_response"
    TIMEOUT = "timeout"
    NETWORK = "network"
    REQUEST_FAILED = "request_failed"


class EligibilityUnavailable(Exception):
    def __init__(self, category: EligibilityFailureCategory):
        self.category = category
        super().__init__(category.value)


class EligibilityTransportFailure(Exception):
    def __init__(self, category: EligibilityFailureCategory):
        self.category = category
        super().__init__(category.value)


@dataclass(frozen=True)
class EligibilityHttpResponse:
    status: int
    body: Any


class EligibilityTransport(Protocol):
    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, str],
        total_timeout: float,
    ) -> EligibilityHttpResponse: ...


class EligibilityChecker(Protocol):
    async def check(self, discord_user_id: str) -> EligibilityOutcome: ...


class AiohttpEligibilityTransport:
    def __init__(self, session_factory=aiohttp.ClientSession):
        self._session_factory = session_factory

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, str],
        total_timeout: float,
    ) -> EligibilityHttpResponse:
        try:
            timeout = aiohttp.ClientTimeout(total=total_timeout)
            async with self._session_factory(timeout=timeout) as session:
                async with session.post(
                    url,
                    headers=headers,
                    json=json_body,
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        return EligibilityHttpResponse(
                            status=response.status, body=None
                        )
                    try:
                        body = await response.json()
                    except aiohttp.ContentTypeError:
                        raise EligibilityTransportFailure(
                            EligibilityFailureCategory.MALFORMED_RESPONSE
                        ) from None
                    except (ValueError, TypeError, UnicodeError):
                        raise EligibilityTransportFailure(
                            EligibilityFailureCategory.MALFORMED_RESPONSE
                        ) from None
                    return EligibilityHttpResponse(
                        status=response.status, body=body
                    )
        except EligibilityTransportFailure:
            raise
        except asyncio.TimeoutError:
            raise EligibilityTransportFailure(
                EligibilityFailureCategory.TIMEOUT
            ) from None
        except aiohttp.ClientError:
            raise EligibilityTransportFailure(
                EligibilityFailureCategory.NETWORK
            ) from None
        except Exception:
            raise EligibilityTransportFailure(
                EligibilityFailureCategory.REQUEST_FAILED
            ) from None


class QuizEligibilityClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: EligibilityTransport | None = None,
    ):
        self._api_key = api_key
        self._transport = transport or AiohttpEligibilityTransport()

    @staticmethod
    def _reject(category: EligibilityFailureCategory) -> NoReturn:
        logger.warning("quiz_eligibility_failure category=%s", category.value)
        raise EligibilityUnavailable(category)

    async def check(self, discord_user_id: str) -> EligibilityOutcome:
        try:
            response = await self._transport.post_json(
                ELIGIBILITY_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json_body={"discordUserId": discord_user_id},
                total_timeout=ELIGIBILITY_TIMEOUT_SECONDS,
            )
        except EligibilityTransportFailure as failure:
            self._reject(failure.category)
        except Exception:
            self._reject(EligibilityFailureCategory.REQUEST_FAILED)
        if response.status != 200:
            self._reject(EligibilityFailureCategory.HTTP_STATUS)
        if (
            isinstance(response.body, dict)
            and set(response.body) == {"eligible"}
            and response.body["eligible"] is True
        ):
            return EligibilityOutcome.ELIGIBLE
        if (
            isinstance(response.body, dict)
            and set(response.body) == {"eligible", "reason"}
            and response.body["eligible"] is False
            and response.body["reason"] == "fingerprint_missing"
        ):
            return EligibilityOutcome.FINGERPRINT_MISSING
        self._reject(EligibilityFailureCategory.MALFORMED_RESPONSE)
