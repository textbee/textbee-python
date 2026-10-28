from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import Message as HttpHeaders
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

import textbee._client as client_module
from textbee import Textbee, TextbeeError, __version__

API_KEY = "test-api-key"
DEVICE_ID = "65f0000000000000000000aa"


@dataclass
class Call:
    url: str
    method: str
    headers: dict[str, str]
    body: Any


class FakeResponse:
    def __init__(self, status: int, raw: bytes) -> None:
        self.status = status
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeUrlopen:
    """Stands in for the opener, raising HTTPError for non-2xx like the real one."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.responder: Callable[[int], tuple[int, Any]] = lambda _index: (200, {"data": []})

    def respond(self, status: int, body: Any = None) -> None:
        self.responder = lambda _index: (status, body)

    def respond_raw(self, status: int, text: str) -> None:
        self.responder = lambda _index: (status, RawText(text))

    def __call__(self, request: Request, timeout: float | None = None) -> FakeResponse:
        data = request.data
        assert data is None or isinstance(data, bytes)
        self.calls.append(
            Call(
                url=request.full_url,
                method=request.get_method(),
                # urllib stores header names capitalized, so compare lowercase.
                headers={key.lower(): value for key, value in request.header_items()},
                body=None if data is None else json.loads(data),
            )
        )

        status, body = self.responder(len(self.calls) - 1)
        if isinstance(body, RawText):
            raw = body.text.encode()
        else:
            raw = b"" if body is None else json.dumps(body).encode()

        if not 200 <= status < 300:
            raise HTTPError(request.full_url, status, "error", HttpHeaders(), io.BytesIO(raw))
        return FakeResponse(status, raw)

    @property
    def last(self) -> Call:
        assert self.calls, "urlopen was not called"
        return self.calls[-1]


@dataclass
class RawText:
    text: str


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeUrlopen:
    fake = FakeUrlopen()
    monkeypatch.setattr(client_module._OPENER, "open", fake)
    return fake


def client(base_url: str | None = None) -> Textbee:
    return Textbee(api_key=API_KEY, base_url=base_url)


class TestSendSms:
    def test_posts_the_modern_field_names_to_the_deviceless_send_endpoint(
        self, http: FakeUrlopen
    ) -> None:
        http.respond(200, {"data": {"successCount": 1}})

        client().send_sms(message="Hello from textbee", recipients=["+12025550123"])

        call = http.last
        assert call.url == "https://api.textbee.dev/api/v1/gateway/send-sms"
        assert call.method == "POST"
        assert call.headers["x-api-key"] == API_KEY
        assert call.headers["content-type"] == "application/json"
        assert call.body == {"message": "Hello from textbee", "recipients": ["+12025550123"]}
        assert "smsBody" not in call.body
        assert "receivers" not in call.body

    def test_unwraps_the_queued_response_and_keeps_it_narrowable(self, http: FakeUrlopen) -> None:
        http.respond(
            200,
            {
                "data": {
                    "success": True,
                    "message": "SMS added to queue for processing",
                    "smsBatchId": "batch-1",
                    "recipientCount": 2,
                }
            },
        )

        result = client().send_sms(message="queued", recipients=["+12025550123", "+12025550124"])

        assert "smsBatchId" in result
        if "smsBatchId" in result:
            assert result["smsBatchId"] == "batch-1"
            assert result["recipientCount"] == 2

    def test_passes_optional_fields_through_and_omits_them_entirely_when_unset(
        self, http: FakeUrlopen
    ) -> None:
        http.respond(200, {"data": {"successCount": 1}})

        client().send_sms(
            message="with options",
            recipients=["+12025550123"],
            device_id=DEVICE_ID,
            sim_subscription_id=2,
        )

        assert http.last.body == {
            "message": "with options",
            "recipients": ["+12025550123"],
            "deviceId": DEVICE_ID,
            "simSubscriptionId": 2,
        }

        client().send_sms(message="bare", recipients=["+12025550123"])

        assert set(http.last.body) == {"message", "recipients"}

    def test_accepts_any_sequence_of_recipients(self, http: FakeUrlopen) -> None:
        http.respond(200, {"data": {"successCount": 2}})

        client().send_sms(message="tuple", recipients=("+12025550123", "+12025550124"))

        assert http.last.body["recipients"] == ["+12025550123", "+12025550124"]

    def test_rejects_a_bare_string_of_recipients(self, http: FakeUrlopen) -> None:
        with pytest.raises(TypeError):
            client().send_sms(message="x", recipients="+12025550123")

        assert http.calls == []

    def test_serializes_a_datetime_and_does_not_enforce_the_72_hour_maximum(
        self, http: FakeUrlopen
    ) -> None:
        http.respond(200, {"data": {"successCount": 1}})

        # Well beyond 72 hours. The maximum is documentation, not client
        # validation, so the SDK must forward it and let the API decide.
        far_future = datetime(2099, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        client().send_sms(message="later", recipients=["+12025550123"], scheduled_at=far_future)

        assert http.last.body["scheduledAt"] == "2099-01-02T03:04:05.000Z"

    def test_converts_a_non_utc_datetime_to_utc(self, http: FakeUrlopen) -> None:
        http.respond(200, {"data": {"successCount": 1}})
        plus_three = timezone(timedelta(hours=3))

        client().send_sms(
            message="later",
            recipients=["+12025550123"],
            scheduled_at=datetime(2099, 1, 2, 6, 4, 5, 123456, tzinfo=plus_three),
        )

        assert http.last.body["scheduledAt"] == "2099-01-02T03:04:05.123Z"

    def test_passes_a_string_scheduled_at_through_untouched(self, http: FakeUrlopen) -> None:
        http.respond(200, {"data": {"successCount": 1}})

        client().send_sms(
            message="later", recipients=["+12025550123"], scheduled_at="2099-01-02T03:04:05+03:00"
        )

        assert http.last.body["scheduledAt"] == "2099-01-02T03:04:05+03:00"

    def test_rejects_a_naive_datetime(self, http: FakeUrlopen) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            client().send_sms(
                message="later",
                recipients=["+12025550123"],
                scheduled_at=datetime(2099, 1, 2, 3, 4, 5),
            )

        assert http.calls == []


class TestErrors:
    def test_maps_a_400_body_to_textbee_error(self, http: FakeUrlopen) -> None:
        http.respond(400, {"success": False, "error": "Failed to send SMS"})

        with pytest.raises(TextbeeError) as raised:
            client().send_sms(message="x", recipients=["+12025550123"])

        assert raised.value.status == 400
        assert raised.value.message == "Failed to send SMS"
        assert str(raised.value) == "Failed to send SMS"

    def test_maps_a_401_body_to_textbee_error(self, http: FakeUrlopen) -> None:
        http.respond(401, {"error": "Unauthorized"})

        with pytest.raises(TextbeeError) as raised:
            client().get_devices()

        assert raised.value.status == 401
        assert raised.value.message == "Unauthorized"
        assert raised.value.body == {"error": "Unauthorized"}

    def test_reads_a_message_field_when_there_is_no_error_field(self, http: FakeUrlopen) -> None:
        http.respond(404, {"message": "Device not found"})

        with pytest.raises(TextbeeError, match="Device not found"):
            client().get_device(DEVICE_ID)

    def test_falls_back_to_the_status_when_the_body_carries_no_error_text(
        self, http: FakeUrlopen
    ) -> None:
        http.respond_raw(502, "<html>gateway</html>")

        with pytest.raises(TextbeeError) as raised:
            client().get_devices()

        assert raised.value.status == 502
        assert raised.value.message == "HTTP 502"
        assert raised.value.body == "<html>gateway</html>"

    def test_never_puts_the_api_key_in_the_error_or_repr(self, http: FakeUrlopen) -> None:
        http.respond(401, {"error": "Unauthorized"})

        with pytest.raises(TextbeeError) as raised:
            client().get_devices()

        assert API_KEY not in repr(raised.value)
        assert API_KEY not in repr(client())


class TestTelemetryHeaders:
    def test_identifies_the_sdk_and_its_version_on_every_request(self, http: FakeUrlopen) -> None:
        client().get_devices()

        expected = f"textbee-python/{__version__}"
        assert http.last.headers["user-agent"] == expected
        assert http.last.headers["x-sdk-client"] == expected
        assert http.last.headers["accept"] == "application/json"


class TestResponseEnvelopes:
    def test_returns_get_messages_as_data_and_meta_because_it_has_no_outer_wrapper(
        self, http: FakeUrlopen
    ) -> None:
        page = {
            "data": [
                {
                    "_id": "sms-1",
                    "message": "hi",
                    "type": "RECEIVED",
                    "direction": "received",
                    "status": "received",
                }
            ],
            "meta": {
                "page": 1,
                "limit": 50,
                "total": 1,
                "totalPages": 1,
                "nextCursor": None,
                "hasMore": False,
            },
        }
        http.respond(200, page)

        result = client().get_messages(
            device_ids=["dev-a", "dev-b"],
            sms_batch_id="batch-1",
            direction="received",
            status="received",
            from_=datetime(2026, 8, 1, tzinfo=timezone.utc),
            to="2026-09-01T00:00:00Z",
            order="asc",
            limit=50,
        )

        assert result == page

        url = urlsplit(http.last.url)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        assert url.path == "/api/v1/gateway/messages"
        assert query["deviceIds"] == "dev-a,dev-b"
        assert query["smsBatchId"] == "batch-1"
        assert query["direction"] == "received"
        assert query["status"] == "received"
        # Datetimes serialize to the explicit-timezone form the API requires
        assert query["from"] == "2026-08-01T00:00:00.000Z"
        assert query["to"] == "2026-09-01T00:00:00Z"
        assert query["order"] == "asc"
        assert query["limit"] == "50"
        assert "type" not in query

    def test_returns_error_history_on_a_message_that_failed_and_later_succeeded(
        self, http: FakeUrlopen
    ) -> None:
        entry = {
            "code": "FCM_SEND_FAILED",
            "message": "push failed",
            "at": "2026-10-01T10:00:00.000Z",
            "source": "push",
            "backfilled": True,
        }
        page = {
            "data": [
                {"_id": "sms-2", "message": "hi", "type": "SENT", "status": "sent", "errorHistory": [entry]}
            ],
            "meta": {},
        }
        http.respond(200, page)

        result = client().get_messages()

        assert result["data"][0]["errorHistory"] == [entry]

    def test_sends_no_query_at_all_for_a_bare_get_messages(self, http: FakeUrlopen) -> None:
        http.respond(200, {"data": [], "meta": {}})

        client().get_messages()

        url = urlsplit(http.last.url)
        assert url.path == "/api/v1/gateway/messages"
        assert url.query == ""

    def test_iterate_messages_follows_next_cursor_to_the_end_and_then_stops(
        self, http: FakeUrlopen
    ) -> None:
        pages: list[dict[str, Any]] = [
            {
                "data": [{"_id": "a"}, {"_id": "b"}],
                "meta": {
                    "page": 1,
                    "limit": 2,
                    "total": 5,
                    "totalPages": 3,
                    "nextCursor": "cur-1",
                    "hasMore": True,
                },
            },
            {
                "data": [{"_id": "c"}, {"_id": "d"}],
                "meta": {"limit": 2, "nextCursor": "cur-2", "hasMore": True},
            },
            {
                "data": [{"_id": "e"}],
                "meta": {"limit": 2, "nextCursor": None, "hasMore": False},
            },
        ]
        http.responder = lambda index: (200, pages[index])

        seen = [message["_id"] for message in client().iterate_messages(order="asc")]

        assert seen == ["a", "b", "c", "d", "e"]
        assert len(http.calls) == 3
        # First call opens without a cursor; later calls carry the returned one
        cursors = [parse_qs(urlsplit(call.url).query).get("cursor") for call in http.calls]
        assert cursors == [None, ["cur-1"], ["cur-2"]]
        # Filters carry over to every page
        assert all(parse_qs(urlsplit(c.url).query)["order"] == ["asc"] for c in http.calls)

    def test_iterate_messages_stops_early_when_the_consumer_breaks_out(
        self, http: FakeUrlopen
    ) -> None:
        http.respond(
            200,
            {
                "data": [{"_id": "a"}, {"_id": "b"}],
                "meta": {"limit": 2, "nextCursor": "cur-1", "hasMore": True},
            },
        )

        for _message in client().iterate_messages():
            break

        assert len(http.calls) == 1

    def test_iterate_messages_is_lazy(self, http: FakeUrlopen) -> None:
        client().iterate_messages()

        assert http.calls == []

    def test_unwraps_the_nested_get_sms_batch_envelope(self, http: FakeUrlopen) -> None:
        http.respond(
            200,
            {
                "data": {
                    "batch": {"_id": "batch-1", "successCount": 1, "failureCount": 0},
                    "messages": [{"_id": "sms-1"}],
                }
            },
        )

        result = client().get_sms_batch(DEVICE_ID, "batch-1")

        assert http.last.url.endswith(f"/gateway/devices/{DEVICE_ID}/sms-batch/batch-1")
        assert result["batch"]["_id"] == "batch-1"
        assert len(result["messages"]) == 1

    def test_unwraps_the_plain_data_envelope_for_get_sms_and_get_device(
        self, http: FakeUrlopen
    ) -> None:
        http.respond(200, {"data": {"_id": "sms-1", "status": "delivered"}})
        assert client().get_sms(DEVICE_ID, "sms-1")["status"] == "delivered"
        assert http.last.url.endswith(f"/gateway/devices/{DEVICE_ID}/sms/sms-1")

        http.respond(200, {"data": {"_id": DEVICE_ID, "enabled": True}})
        assert client().get_device(DEVICE_ID)["_id"] == DEVICE_ID

    def test_posts_to_set_default_without_a_body(self, http: FakeUrlopen) -> None:
        http.respond(200, {"data": {"_id": DEVICE_ID, "isDefault": True}})

        result = client().set_default_device(DEVICE_ID)

        assert http.last.method == "POST"
        assert http.last.url == (
            f"https://api.textbee.dev/api/v1/gateway/devices/{DEVICE_ID}/set-default"
        )
        assert http.last.body is None
        assert "content-type" not in http.last.headers
        assert result["isDefault"] is True

    def test_escapes_ids_in_the_path(self, http: FakeUrlopen) -> None:
        http.respond(200, {"data": {"_id": "x"}})

        client().get_device("a/b?c")

        assert http.last.url.endswith("/gateway/devices/a%2Fb%3Fc")


class TestOptions:
    def test_strips_trailing_slashes_from_a_custom_base_url(self, http: FakeUrlopen) -> None:
        client(base_url="http://localhost:3001/api/v1/").get_devices()

        assert http.last.url == "http://localhost:3001/api/v1/gateway/devices"

    # Same cases as textbee-mcp's normalizeBaseUrl, so all clients agree.
    @pytest.mark.parametrize(
        ("base_url", "expected"),
        [
            (None, "https://api.textbee.dev/api/v1"),
            ("", "https://api.textbee.dev/api/v1"),
            ("  ", "https://api.textbee.dev/api/v1"),
            ("https://sms.example.com", "https://sms.example.com/api/v1"),
            ("https://sms.example.com/", "https://sms.example.com/api/v1"),
            ("https://sms.example.com/api/v1", "https://sms.example.com/api/v1"),
            ("https://sms.example.com/api/v1/", "https://sms.example.com/api/v1"),
            ("https://sms.example.com/textbee/api/v1", "https://sms.example.com/textbee/api/v1"),
            # A subpath deployment gets /api/v1 added after its prefix.
            ("https://sms.example.com/textbee", "https://sms.example.com/textbee/api/v1"),
            ("https://example.com/sms/", "https://example.com/sms/api/v1"),
            ("https://api.textbee.dev/api/v1/gateway/send-sms", "https://api.textbee.dev/api/v1"),
            ("api.textbee.dev", "https://api.textbee.dev/api/v1"),
            ("localhost:3001", "https://localhost:3001/api/v1"),
            ("http://localhost:3001", "http://localhost:3001/api/v1"),
            ("http://127.0.0.1:3001/api/v1", "http://127.0.0.1:3001/api/v1"),
            ("http://[::1]:3001", "http://[::1]:3001/api/v1"),
            ("http://sms.example.com", "http://sms.example.com/api/v1"),
            ("https://sms.example.com/API/V1", "https://sms.example.com/api/v1"),
            ("https://sms.example.com/v1", "https://sms.example.com/v1/api/v1"),
            ("https://example.com/my app/api/v1", "https://example.com/my%20app/api/v1"),
            ("https://b" + chr(0xFC) + "cher.example", "https://xn--bcher-kva.example/api/v1"),
            ("HTTPS://SMS.Example.com:443/?q=1#top", "https://sms.example.com/api/v1"),
        ],
    )
    def test_normalizes_a_self_hosted_base_url(
        self, http: FakeUrlopen, base_url: str | None, expected: str
    ) -> None:
        client(base_url=base_url).get_devices()

        assert http.last.url == f"{expected}/gateway/devices"

    @pytest.mark.parametrize(
        "base_url",
        [
            "not a url",
            "ftp://sms.example.com",
            "https://",
            "https://sms.example.com:99999",
            "https://user:pass@sms.example.com",
            "https://api.textbee.dev@evil.example",
        ],
    )
    def test_rejects_an_invalid_base_url_instead_of_falling_back(self, base_url: str) -> None:
        with pytest.raises(ValueError) as raised:
            Textbee(api_key=API_KEY, base_url=base_url)

        assert "api.textbee.dev" not in str(raised.value)

    def test_rejects_a_missing_api_key(self) -> None:
        with pytest.raises(ValueError):
            Textbee(api_key="")

    def test_forwards_the_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[float | None] = []

        def fake(request: Request, timeout: float | None = None) -> FakeResponse:
            seen.append(timeout)
            return FakeResponse(200, b'{"data": []}')

        monkeypatch.setattr(client_module._OPENER, "open", fake)

        Textbee(api_key=API_KEY).get_devices()
        Textbee(api_key=API_KEY, timeout=5).get_devices()

        assert seen == [30.0, 5]


class TestReviewHardening:
    def test_strips_surrounding_whitespace_from_the_api_key(self, http: FakeUrlopen) -> None:
        Textbee(api_key=f"  {API_KEY}\n").get_devices()

        assert http.last.headers["x-api-key"] == API_KEY

    @pytest.mark.parametrize("api_key", ["secret\nkey", "secret" + chr(0xE9), "a\x00b"])
    def test_rejects_an_api_key_with_invalid_characters_without_echoing_it(
        self, api_key: str
    ) -> None:
        with pytest.raises(ValueError) as raised:
            Textbee(api_key=api_key)

        assert "secret" not in str(raised.value)

    def test_keeps_credentials_in_an_invalid_base_url_out_of_the_error(self) -> None:
        with pytest.raises(ValueError) as raised:
            Textbee(api_key=API_KEY, base_url="https://user:secret@sms.example.com:99999")

        assert "secret" not in str(raised.value)

    def test_rejects_a_bare_string_of_device_ids(self, http: FakeUrlopen) -> None:
        with pytest.raises(TypeError):
            client().get_messages(device_ids="65f0000000000000000000aa")
        with pytest.raises(TypeError):
            next(client().iterate_messages(device_ids="65f0000000000000000000aa"))

        assert http.calls == []

    @pytest.mark.parametrize("body", [None, [], "<html>dashboard</html>"])
    def test_a_2xx_that_is_not_a_json_object_raises_textbee_error(
        self, http: FakeUrlopen, body: Any
    ) -> None:
        if isinstance(body, str):
            http.respond_raw(200, body)
        else:
            http.respond(200, body)

        with pytest.raises(TextbeeError, match="base_url"):
            client().get_devices()

    def test_a_2xx_object_without_data_raises_textbee_error(self, http: FakeUrlopen) -> None:
        http.respond(200, {"ok": True})

        with pytest.raises(TextbeeError, match="base_url"):
            client().get_device(DEVICE_ID)
        with pytest.raises(TextbeeError, match="base_url"):
            client().get_messages()

    def test_prefers_message_over_the_reason_phrase_in_framework_errors(
        self, http: FakeUrlopen
    ) -> None:
        http.respond(
            404,
            {"statusCode": 404, "message": "Cannot GET /sms/gateway/devices", "error": "Not Found"},
        )
        with pytest.raises(TextbeeError, match="Cannot GET /sms/gateway/devices"):
            client().get_devices()

        http.respond(
            400,
            {
                "statusCode": 400,
                "message": ["recipients must be an array", "message should not be empty"],
                "error": "Bad Request",
            },
        )
        with pytest.raises(TextbeeError) as raised:
            client().get_devices()
        assert raised.value.message == ("recipients must be an array; message should not be empty")

    def test_textbee_error_survives_pickling_and_copying(self) -> None:
        import copy
        import pickle

        error = TextbeeError(404, "Device not found", {"error": "Device not found"})

        for clone in (pickle.loads(pickle.dumps(error)), copy.copy(error)):
            assert isinstance(clone, TextbeeError)
            assert (clone.status, clone.message, clone.body) == (
                404,
                "Device not found",
                {"error": "Device not found"},
            )

    def test_rejects_dates_and_other_non_datetime_values(self, http: FakeUrlopen) -> None:
        from datetime import date

        with pytest.raises(TypeError, match="scheduled_at"):
            client().send_sms(
                message="x",
                recipients=["+12025550123"],
                scheduled_at=date(2099, 1, 1),  # type: ignore[arg-type]
            )
        with pytest.raises(TypeError, match="from_"):
            client().get_messages(from_=date(2026, 8, 1))  # type: ignore[arg-type]

        assert http.calls == []

    def test_iterate_messages_page_size_is_sent_as_limit(self, http: FakeUrlopen) -> None:
        http.respond(200, {"data": [], "meta": {"limit": 100, "nextCursor": None}})

        list(client().iterate_messages(page_size=100))

        assert parse_qs(urlsplit(http.last.url).query)["limit"] == ["100"]
