"""Bounded, SEC-compliant transport for one issuer's public JSON payloads.

The downloader owns HTTP behavior only.  It does not extract facts, classify
fiscal periods, publish to a database, or resolve ticker symbols.  Automated
tests pass a fake session; no test needs or is allowed to contact the SEC.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Tuple
from urllib.parse import urlparse

from ..types import normalize_cik

logger = logging.getLogger(__name__)

SEC_DATA_HOST = "data.sec.gov"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# SEC currently permits no more than 10 requests/second.  The slightly slower
# default leaves room for clock granularity and other callers in this process.
MIN_REQUEST_INTERVAL_SECONDS = 0.11
DEFAULT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_HISTORICAL_SUBMISSION_FILES = 100
_RETRYABLE_STATUS_CODES = frozenset((429, 500, 502, 503, 504))
_SUBMISSION_FILE_PATTERN = re.compile(r"^CIK(?P<cik>\d{10})-submissions-\d{3}\.json$")
_EMAIL_PATTERN = re.compile(r"(?<!\S)[^\s@]+@[^\s@]+\.[^\s@]+(?!\S)")
_PLACEHOLDER_USER_AGENT_PARTS = (
    "example.com",
    "your-email",
    "your_email",
    "admincontact@",
    "changeme",
    "placeholder",
)


class SecDownloadErrorCode(str, Enum):
    NETWORK_FAILURE = "network_failure"
    HTTP_STATUS = "http_status"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    RESPONSE_TOO_LARGE = "response_too_large"
    INVALID_JSON = "invalid_json"
    INVALID_SUBMISSIONS_INDEX = "invalid_submissions_index"
    ISSUER_MISMATCH = "issuer_mismatch"


class SecDownloadError(RuntimeError):
    """A typed, sanitized download failure safe for orchestration code."""

    def __init__(self, code: SecDownloadErrorCode, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SecDownloaderConfig:
    """Operational limits and the declared identity sent to the SEC."""

    user_agent: str
    request_interval_seconds: float = MIN_REQUEST_INTERVAL_SECONDS
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 20.0
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_attempts: int = 3
    retry_backoff_seconds: Tuple[float, ...] = (0.5, 1.0)
    max_historical_submission_files: int = DEFAULT_MAX_HISTORICAL_SUBMISSION_FILES

    def __post_init__(self) -> None:
        _validate_user_agent(self.user_agent)
        if not _is_finite_number(self.request_interval_seconds):
            raise ValueError("request_interval_seconds must be finite.")
        if self.request_interval_seconds < MIN_REQUEST_INTERVAL_SECONDS:
            raise ValueError(
                f"request_interval_seconds must be at least {MIN_REQUEST_INTERVAL_SECONDS}."
            )
        for field_name in ("connect_timeout_seconds", "read_timeout_seconds"):
            value = getattr(self, field_name)
            if not _is_finite_number(value) or value <= 0:
                raise ValueError(f"{field_name} must be a finite positive number.")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or self.max_response_bytes <= 0
        ):
            raise ValueError("max_response_bytes must be a positive integer.")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= 5
        ):
            raise ValueError("max_attempts must be an integer from 1 through 5.")
        try:
            backoff = tuple(self.retry_backoff_seconds)
        except TypeError:
            raise ValueError("retry_backoff_seconds must be a collection.") from None
        if not backoff or any(not _is_finite_number(value) or value < 0 for value in backoff):
            raise ValueError("retry_backoff_seconds must contain finite non-negative values.")
        object.__setattr__(self, "retry_backoff_seconds", backoff)
        if (
            isinstance(self.max_historical_submission_files, bool)
            or not isinstance(self.max_historical_submission_files, int)
            or not 1 <= self.max_historical_submission_files <= 1000
        ):
            raise ValueError(
                "max_historical_submission_files must be an integer from 1 through 1000."
            )

    @classmethod
    def from_environment(cls) -> "SecDownloaderConfig":
        """Load only the required process variable; never search parent dotenv files."""

        user_agent = os.getenv("SEC_USER_AGENT")
        if user_agent is None:
            raise ValueError(
                "SEC_USER_AGENT is required and must identify the application and a real contact email."
            )
        return cls(user_agent=user_agent)


@dataclass(frozen=True)
class SecIssuerPayload:
    """One complete, unclassified SEC payload bundle for a single issuer."""

    cik: str
    company_facts: Mapping[str, Any]
    submissions: Tuple[Mapping[str, Any], ...]
    company_facts_url: str
    submission_urls: Tuple[str, ...]
    downloaded_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        object.__setattr__(self, "submissions", tuple(self.submissions))
        object.__setattr__(self, "submission_urls", tuple(self.submission_urls))
        if not isinstance(self.company_facts, Mapping):
            raise ValueError("company_facts must be an object.")
        if not self.submissions or len(self.submissions) != len(self.submission_urls):
            raise ValueError("submissions and submission_urls must be non-empty and aligned.")
        if not isinstance(self.downloaded_at, datetime):
            raise ValueError("downloaded_at must be a datetime.")
        if self.downloaded_at.tzinfo is None or self.downloaded_at.utcoffset() is None:
            raise ValueError("downloaded_at must be timezone-aware.")


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return True
    return math.isfinite(value)


def _validate_user_agent(value: object) -> None:
    if not isinstance(value, str) or value != value.strip() or not 10 <= len(value) <= 256:
        raise ValueError(
            "SEC user_agent must be 10-256 characters with no surrounding whitespace."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("SEC user_agent must not contain control characters.")
    if _EMAIL_PATTERN.search(value) is None:
        raise ValueError("SEC user_agent must include a real contact email address.")
    lowered = value.lower()
    if any(part in lowered for part in _PLACEHOLDER_USER_AGENT_PARTS):
        raise ValueError("SEC user_agent must not contain placeholder contact information.")


def _header(headers: Mapping[str, Any], name: str) -> Optional[str]:
    lowered_name = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered_name:
            return str(value)
    return None


def _reject_json_constant(value: str):
    raise ValueError(f"non-finite JSON constant {value!r}")


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _decode_json_document(body: bytes) -> Mapping[str, Any]:
    try:
        payload = json.loads(
            body.decode("utf-8"),
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise SecDownloadError(
            SecDownloadErrorCode.INVALID_JSON,
            "SEC returned an invalid JSON document.",
        ) from None
    if not isinstance(payload, Mapping):
        raise SecDownloadError(
            SecDownloadErrorCode.INVALID_JSON,
            "SEC returned JSON whose top level is not an object.",
        )
    return payload


class SecDownloader:
    """Fetch a complete Company Facts and Submissions bundle for one CIK."""

    def __init__(
        self,
        config: SecDownloaderConfig,
        *,
        session=None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not isinstance(config, SecDownloaderConfig):
            raise ValueError("config must be a SecDownloaderConfig.")
        self._config = config
        self._provided_session = session
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_request_started_at: Optional[float] = None

    def fetch_issuer(self, cik: str) -> SecIssuerPayload:
        """Return one complete bundle or raise one sanitized ``SecDownloadError``."""

        normalized_cik = normalize_cik(cik)
        session = self._provided_session
        owns_session = session is None
        if session is None:
            import requests

            session = requests.Session()

        try:
            company_facts_url = SEC_COMPANY_FACTS_URL.format(cik=normalized_cik)
            submissions_url = SEC_SUBMISSIONS_URL.format(cik=normalized_cik)
            company_facts = self._request_json(session, company_facts_url)
            self._validate_payload_cik(company_facts, normalized_cik, "Company Facts")
            main_submissions = self._request_json(session, submissions_url)
            self._validate_payload_cik(main_submissions, normalized_cik, "Submissions")

            historical_names = self._historical_submission_names(
                main_submissions,
                normalized_cik,
            )
            submission_urls = [submissions_url]
            submissions = [main_submissions]
            for name in historical_names:
                historical_url = f"https://{SEC_DATA_HOST}/submissions/{name}"
                submissions.append(self._request_json(session, historical_url))
                submission_urls.append(historical_url)

            return SecIssuerPayload(
                cik=normalized_cik,
                company_facts=company_facts,
                submissions=tuple(submissions),
                company_facts_url=company_facts_url,
                submission_urls=tuple(submission_urls),
                downloaded_at=datetime.now(timezone.utc),
            )
        finally:
            if owns_session:
                try:
                    session.close()
                except Exception as error:
                    logger.warning("SEC session close failed (%s).", type(error).__name__)

    def _pace(self) -> None:
        now = self._monotonic()
        if self._last_request_started_at is not None:
            remaining = (
                self._config.request_interval_seconds
                - (now - self._last_request_started_at)
            )
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_request_started_at = now

    def _retry_delay(self, attempt_index: int, response=None) -> float:
        if response is not None:
            retry_after = _header(getattr(response, "headers", {}), "Retry-After")
            if retry_after is not None:
                try:
                    parsed = float(retry_after)
                except ValueError:
                    parsed = -1.0
                if 0 <= parsed <= 60:
                    return parsed
        backoff = self._config.retry_backoff_seconds
        return backoff[min(attempt_index, len(backoff) - 1)]

    def _request_json(self, session, url: str) -> Mapping[str, Any]:
        self._validate_url(url)
        for attempt_index in range(self._config.max_attempts):
            self._pace()
            response = None
            try:
                response = session.get(
                    url,
                    headers={
                        "User-Agent": self._config.user_agent,
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip, deflate",
                    },
                    timeout=(
                        self._config.connect_timeout_seconds,
                        self._config.read_timeout_seconds,
                    ),
                    stream=True,
                    allow_redirects=False,
                )
                status_code = response.status_code
                if isinstance(status_code, bool) or not isinstance(status_code, int):
                    raise SecDownloadError(
                        SecDownloadErrorCode.HTTP_STATUS,
                        "SEC returned an invalid HTTP status.",
                    )
                if status_code in _RETRYABLE_STATUS_CODES:
                    if attempt_index + 1 < self._config.max_attempts:
                        self._sleep(self._retry_delay(attempt_index, response))
                        continue
                    raise SecDownloadError(
                        SecDownloadErrorCode.HTTP_STATUS,
                        f"SEC request failed after bounded retries with HTTP {status_code}.",
                    )
                if status_code != 200:
                    raise SecDownloadError(
                        SecDownloadErrorCode.HTTP_STATUS,
                        f"SEC request failed with HTTP {status_code}.",
                    )
                return self._read_json_response(response)
            except SecDownloadError as error:
                if (
                    error.code is SecDownloadErrorCode.NETWORK_FAILURE
                    and attempt_index + 1 < self._config.max_attempts
                ):
                    self._sleep(self._retry_delay(attempt_index))
                    continue
                raise
            except Exception as error:
                logger.warning("SEC request failed (%s).", type(error).__name__)
                if attempt_index + 1 < self._config.max_attempts:
                    self._sleep(self._retry_delay(attempt_index))
                    continue
                raise SecDownloadError(
                    SecDownloadErrorCode.NETWORK_FAILURE,
                    "SEC request failed after bounded retries.",
                ) from None
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception as error:
                        logger.warning("SEC response close failed (%s).", type(error).__name__)
        raise AssertionError("bounded SEC request loop exited unexpectedly")

    def _read_json_response(self, response) -> Mapping[str, Any]:
        content_type = _header(getattr(response, "headers", {}), "Content-Type")
        if content_type is None or content_type.split(";", 1)[0].strip().lower() not in (
            "application/json",
            "application/problem+json",
        ):
            raise SecDownloadError(
                SecDownloadErrorCode.INVALID_CONTENT_TYPE,
                "SEC returned an unexpected response content type.",
            )

        content_length = _header(getattr(response, "headers", {}), "Content-Length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                raise SecDownloadError(
                    SecDownloadErrorCode.RESPONSE_TOO_LARGE,
                    "SEC returned an invalid response length.",
                ) from None
            if declared_size < 0 or declared_size > self._config.max_response_bytes:
                raise SecDownloadError(
                    SecDownloadErrorCode.RESPONSE_TOO_LARGE,
                    "SEC response exceeded the configured size limit.",
                )

        chunks = []
        size = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if not isinstance(chunk, bytes):
                    raise TypeError("response chunk was not bytes")
                size += len(chunk)
                if size > self._config.max_response_bytes:
                    raise SecDownloadError(
                        SecDownloadErrorCode.RESPONSE_TOO_LARGE,
                        "SEC response exceeded the configured size limit.",
                    )
                chunks.append(chunk)
        except SecDownloadError:
            raise
        except Exception as error:
            logger.warning("SEC response read failed (%s).", type(error).__name__)
            raise SecDownloadError(
                SecDownloadErrorCode.NETWORK_FAILURE,
                "SEC response could not be read.",
            ) from None
        return _decode_json_document(b"".join(chunks))

    def _historical_submission_names(
        self,
        main_submissions: Mapping[str, Any],
        cik: str,
    ) -> Tuple[str, ...]:
        filings = main_submissions.get("filings")
        if not isinstance(filings, Mapping):
            raise SecDownloadError(
                SecDownloadErrorCode.INVALID_SUBMISSIONS_INDEX,
                "SEC Submissions payload has no valid filings object.",
            )
        file_entries = filings.get("files", ())
        if not isinstance(file_entries, (list, tuple)):
            raise SecDownloadError(
                SecDownloadErrorCode.INVALID_SUBMISSIONS_INDEX,
                "SEC Submissions historical-file index is malformed.",
            )
        if len(file_entries) > self._config.max_historical_submission_files:
            raise SecDownloadError(
                SecDownloadErrorCode.INVALID_SUBMISSIONS_INDEX,
                "SEC Submissions historical-file index exceeds the configured limit.",
            )

        names = []
        for entry in file_entries:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str):
                raise SecDownloadError(
                    SecDownloadErrorCode.INVALID_SUBMISSIONS_INDEX,
                    "SEC Submissions historical-file index contains an invalid entry.",
                )
            name = entry["name"]
            match = _SUBMISSION_FILE_PATTERN.fullmatch(name)
            if match is None or match.group("cik") != cik:
                raise SecDownloadError(
                    SecDownloadErrorCode.INVALID_SUBMISSIONS_INDEX,
                    "SEC Submissions historical-file reference does not match the requested issuer.",
                )
            names.append(name)
        if len(names) != len(set(names)):
            raise SecDownloadError(
                SecDownloadErrorCode.INVALID_SUBMISSIONS_INDEX,
                "SEC Submissions historical-file index contains duplicate references.",
            )
        return tuple(names)

    @staticmethod
    def _validate_payload_cik(
        payload: Mapping[str, Any],
        expected_cik: str,
        document_name: str,
    ) -> None:
        raw_cik = payload.get("cik")
        if isinstance(raw_cik, int) and not isinstance(raw_cik, bool):
            raw_cik = str(raw_cik)
        try:
            actual_cik = normalize_cik(raw_cik)
        except ValueError:
            raise SecDownloadError(
                SecDownloadErrorCode.ISSUER_MISMATCH,
                f"SEC {document_name} payload has an invalid issuer identifier.",
            ) from None
        if actual_cik != expected_cik:
            raise SecDownloadError(
                SecDownloadErrorCode.ISSUER_MISMATCH,
                f"SEC {document_name} payload does not match the requested issuer.",
            )

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != SEC_DATA_HOST
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.query
            or parsed.fragment
        ):
            raise SecDownloadError(
                SecDownloadErrorCode.NETWORK_FAILURE,
                "Refusing a URL outside the approved SEC data host.",
            )
