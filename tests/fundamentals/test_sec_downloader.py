import datetime as dt
import json
from decimal import Decimal

import pytest

from src.fundamentals.adapters.sec_companyfacts import extract_sec_company_facts
from src.fundamentals.adapters.sec_downloader import (
    MIN_REQUEST_INTERVAL_SECONDS,
    SEC_COMPANY_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    SecDownloadError,
    SecDownloadErrorCode,
    SecDownloader,
    SecDownloaderConfig,
)
from src.fundamentals.concept_map import SEC_CONCEPT_MAP_V1

CIK = "0000320193"
USER_AGENT = "Valuation Engine sec-contact@real-domain.test"


class FakeResponse:
    def __init__(
        self,
        body=b"{}",
        *,
        status_code=200,
        content_type="application/json",
        headers=None,
        chunks=None,
        stream_error=None,
    ):
        self.status_code = status_code
        self.headers = dict(headers or {})
        if content_type is not None:
            self.headers.setdefault("Content-Type", content_type)
        self._body = body
        self._chunks = chunks
        self._stream_error = stream_error
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size == 64 * 1024
        if self._stream_error is not None:
            raise self._stream_error
        if self._chunks is not None:
            yield from self._chunks
        else:
            yield self._body

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _json_response(value, **kwargs):
    return FakeResponse(json.dumps(value).encode("utf-8"), **kwargs)


def _company_facts():
    return {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {"us-gaap": {}},
    }


def _main_submissions(*historical_names):
    return {
        "cik": CIK,
        "filings": {
            "recent": {},
            "files": [{"name": name} for name in historical_names],
        },
    }


def _downloader(session, *, config=None, clock=None):
    clock = clock or FakeClock()
    return SecDownloader(
        config or SecDownloaderConfig(user_agent=USER_AGENT),
        session=session,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


class TestConfiguration:
    def test_requires_a_declared_application_and_real_contact_email(self):
        for user_agent in (
            "",
            "anonymous-bot",
            "Valuation Engine your-email@example.com",
            " Valuation Engine contact@real-domain.test",
            "Valuation Engine contact@real-domain.test\nInjected: yes",
        ):
            with pytest.raises(ValueError, match="user_agent"):
                SecDownloaderConfig(user_agent=user_agent)

    def test_loads_only_the_explicit_environment_value(self, monkeypatch):
        monkeypatch.setenv("SEC_USER_AGENT", USER_AGENT)

        assert SecDownloaderConfig.from_environment().user_agent == USER_AGENT

    def test_missing_environment_identity_fails_before_any_request(self, monkeypatch):
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)

        with pytest.raises(ValueError, match="SEC_USER_AGENT"):
            SecDownloaderConfig.from_environment()

    def test_cannot_configure_a_rate_above_the_sec_limit(self):
        with pytest.raises(ValueError, match="request_interval_seconds"):
            SecDownloaderConfig(
                user_agent=USER_AGENT,
                request_interval_seconds=MIN_REQUEST_INTERVAL_SECONDS - 0.01,
            )


class TestCompleteIssuerBundle:
    def test_fetches_company_facts_current_and_historical_submissions(self):
        historical_name = f"CIK{CIK}-submissions-001.json"
        fractional_document = (
            b'{"cik":320193,"entityName":"Apple Inc.",'
            b'"facts":{"us-gaap":{"EarningsPerShare":{"units":{"USD/shares":'
            b'[{"val":1.25}]}}}}}'
        )
        responses = (
            FakeResponse(fractional_document),
            _json_response(_main_submissions(historical_name)),
            _json_response({"accessionNumber": ["0000320193-20-000001"]}),
        )
        session = FakeSession(responses)
        clock = FakeClock()

        payload = _downloader(session, clock=clock).fetch_issuer("320193")

        assert payload.cik == CIK
        assert payload.company_facts["facts"]["us-gaap"]["EarningsPerShare"]["units"][
            "USD/shares"
        ][0]["val"] == Decimal("1.25")
        assert len(payload.submissions) == 2
        assert payload.submissions[1]["accessionNumber"] == ["0000320193-20-000001"]
        assert payload.company_facts_url == SEC_COMPANY_FACTS_URL.format(cik=CIK)
        assert payload.submission_urls == (
            SEC_SUBMISSIONS_URL.format(cik=CIK),
            f"https://data.sec.gov/submissions/{historical_name}",
        )
        assert payload.downloaded_at.tzinfo is not None
        assert len(clock.sleeps) == 2
        assert all(delay == pytest.approx(MIN_REQUEST_INTERVAL_SECONDS) for delay in clock.sleeps)
        assert all(response.closed for response in responses)

    def test_every_request_has_the_sec_identity_limits_and_no_redirects(self):
        session = FakeSession(
            (_json_response(_company_facts()), _json_response(_main_submissions()))
        )

        _downloader(session).fetch_issuer(CIK)

        assert [url for url, _kwargs in session.calls] == [
            SEC_COMPANY_FACTS_URL.format(cik=CIK),
            SEC_SUBMISSIONS_URL.format(cik=CIK),
        ]
        for _url, kwargs in session.calls:
            assert kwargs == {
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate",
                },
                "timeout": (5.0, 20.0),
                "stream": True,
                "allow_redirects": False,
            }

    def test_bundle_is_directly_accepted_by_the_existing_extractor(self):
        accession = "0000320193-24-000123"
        company_facts = {
            "cik": 320193,
            "entityName": "Apple Inc.",
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2023-10-01",
                                    "end": "2024-09-28",
                                    "val": 391_035_000_000,
                                    "accn": accession,
                                    "fy": 2024,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2024-11-01",
                                }
                            ]
                        }
                    }
                }
            },
        }
        submissions = {
            "cik": CIK,
            "filings": {
                "recent": {
                    "accessionNumber": [accession],
                    "filingDate": ["2024-11-01"],
                    "acceptanceDateTime": ["2024-11-01T06:01:36.000Z"],
                    "reportDate": ["2024-09-28"],
                    "form": ["10-K"],
                    "primaryDocument": ["aapl-20240928.htm"],
                },
                "files": [],
            },
        }
        session = FakeSession((_json_response(company_facts), _json_response(submissions)))

        payload = _downloader(session).fetch_issuer(CIK)
        result = extract_sec_company_facts(
            payload.company_facts,
            payload.submissions,
            expected_cik=payload.cik,
            concept_map=SEC_CONCEPT_MAP_V1,
            ingestion_batch_id="batch-downloader-contract",
            ingested_at=payload.downloaded_at,
        )

        assert result.is_complete
        assert len(result.facts) == 1
        fact = result.facts[0]
        assert fact.value == Decimal("391035000000")
        assert fact.accepted_at == dt.datetime(
            2024, 11, 1, 6, 1, 36, tzinfo=dt.timezone.utc
        )
        assert fact.source_document_url == (
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019324000123/aapl-20240928.htm"
        )

    def test_invalid_cik_is_rejected_before_the_transport_is_called(self):
        session = FakeSession(())

        with pytest.raises(ValueError, match="CIK"):
            _downloader(session).fetch_issuer("AAPL")

        assert session.calls == []

    def test_company_facts_issuer_mismatch_refuses_the_bundle(self):
        company_facts = _company_facts()
        company_facts["cik"] = 789019
        session = FakeSession((_json_response(company_facts),))

        with pytest.raises(SecDownloadError) as caught:
            _downloader(session).fetch_issuer(CIK)

        assert caught.value.code is SecDownloadErrorCode.ISSUER_MISMATCH


class TestBoundedTransportFailures:
    def test_retries_transient_status_and_honors_bounded_retry_after(self):
        unavailable = FakeResponse(status_code=503, headers={"Retry-After": "2"})
        session = FakeSession(
            (
                unavailable,
                _json_response(_company_facts()),
                _json_response(_main_submissions()),
            )
        )
        clock = FakeClock()

        _downloader(session, clock=clock).fetch_issuer(CIK)

        assert len(session.calls) == 3
        assert clock.sleeps[0] == 2.0
        assert unavailable.closed

    def test_does_not_retry_a_permanent_http_error_or_read_its_body(self):
        not_found = FakeResponse(b"secret response body", status_code=404)
        session = FakeSession((not_found,))

        with pytest.raises(SecDownloadError) as caught:
            _downloader(session).fetch_issuer(CIK)

        assert caught.value.code is SecDownloadErrorCode.HTTP_STATUS
        assert "secret response body" not in str(caught.value)
        assert len(session.calls) == 1
        assert not_found.closed

    def test_network_errors_are_retried_and_sanitized(self):
        secret = "postgresql://user:password@example.invalid/private"
        session = FakeSession((RuntimeError(secret), RuntimeError(secret), RuntimeError(secret)))

        with pytest.raises(SecDownloadError) as caught:
            _downloader(session).fetch_issuer(CIK)

        assert caught.value.code is SecDownloadErrorCode.NETWORK_FAILURE
        assert secret not in str(caught.value)
        assert len(session.calls) == 3

    def test_stream_failure_is_retried_without_returning_partial_json(self):
        failed_response = FakeResponse(stream_error=TimeoutError("partial body"))
        session = FakeSession(
            (
                failed_response,
                _json_response(_company_facts()),
                _json_response(_main_submissions()),
            )
        )

        payload = _downloader(session).fetch_issuer(CIK)

        assert payload.cik == CIK
        assert len(session.calls) == 3
        assert failed_response.closed

    def test_partial_historical_download_never_returns_a_partial_bundle(self):
        historical_name = f"CIK{CIK}-submissions-001.json"
        session = FakeSession(
            (
                _json_response(_company_facts()),
                _json_response(_main_submissions(historical_name)),
                FakeResponse(status_code=503),
            )
        )
        config = SecDownloaderConfig(user_agent=USER_AGENT, max_attempts=1)

        with pytest.raises(SecDownloadError) as caught:
            _downloader(session, config=config).fetch_issuer(CIK)

        assert caught.value.code is SecDownloadErrorCode.HTTP_STATUS


class TestResponseValidation:
    def test_rejects_non_json_content_before_reading_it(self):
        response = FakeResponse(b"<html>blocked</html>", content_type="text/html")
        session = FakeSession((response,))

        with pytest.raises(SecDownloadError) as caught:
            _downloader(session).fetch_issuer(CIK)

        assert caught.value.code is SecDownloadErrorCode.INVALID_CONTENT_TYPE
        assert response.closed

    def test_rejects_declared_response_over_the_size_limit(self):
        response = FakeResponse(headers={"Content-Length": "101"})
        session = FakeSession((response,))
        config = SecDownloaderConfig(user_agent=USER_AGENT, max_response_bytes=100)

        with pytest.raises(SecDownloadError) as caught:
            _downloader(session, config=config).fetch_issuer(CIK)

        assert caught.value.code is SecDownloadErrorCode.RESPONSE_TOO_LARGE

    def test_rejects_stream_that_exceeds_the_size_limit_after_decompression(self):
        response = FakeResponse(chunks=(b"a" * 60, b"b" * 41))
        session = FakeSession((response,))
        config = SecDownloaderConfig(user_agent=USER_AGENT, max_response_bytes=100)

        with pytest.raises(SecDownloadError) as caught:
            _downloader(session, config=config).fetch_issuer(CIK)

        assert caught.value.code is SecDownloadErrorCode.RESPONSE_TOO_LARGE

    @pytest.mark.parametrize(
        "body",
        (
            b"not-json",
            b"[]",
            b'{"cik": 1, "cik": 2}',
            b'{"value": NaN}',
            b"\xff",
        ),
    )
    def test_rejects_ambiguous_or_malformed_json(self, body):
        session = FakeSession((FakeResponse(body),))

        with pytest.raises(SecDownloadError) as caught:
            _downloader(session).fetch_issuer(CIK)

        assert caught.value.code is SecDownloadErrorCode.INVALID_JSON


class TestHistoricalSubmissionIndex:
    @pytest.mark.parametrize(
        "files",
        (
            "not-an-array",
            ({"name": "../escape.json"},),
            ({"name": "CIK0000789019-submissions-001.json"},),
            (
                {"name": f"CIK{CIK}-submissions-001.json"},
                {"name": f"CIK{CIK}-submissions-001.json"},
            ),
            ({"not_name": "missing"},),
        ),
    )
    def test_refuses_malformed_cross_issuer_or_duplicate_references(self, files):
        main = _main_submissions()
        main["filings"]["files"] = files
        session = FakeSession((_json_response(_company_facts()), _json_response(main)))

        with pytest.raises(SecDownloadError) as caught:
            _downloader(session).fetch_issuer(CIK)

        assert caught.value.code is SecDownloadErrorCode.INVALID_SUBMISSIONS_INDEX
        assert len(session.calls) == 2

    def test_refuses_more_historical_pages_than_the_configured_limit(self):
        names = (
            f"CIK{CIK}-submissions-001.json",
            f"CIK{CIK}-submissions-002.json",
        )
        session = FakeSession(
            (_json_response(_company_facts()), _json_response(_main_submissions(*names)))
        )
        config = SecDownloaderConfig(
            user_agent=USER_AGENT,
            max_historical_submission_files=1,
        )

        with pytest.raises(SecDownloadError) as caught:
            _downloader(session, config=config).fetch_issuer(CIK)

        assert caught.value.code is SecDownloadErrorCode.INVALID_SUBMISSIONS_INDEX
