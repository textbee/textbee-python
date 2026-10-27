from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union, cast
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ._errors import TextbeeError
from ._types import (
    Device,
    Direction,
    Message,
    MessageList,
    MessageStatus,
    Order,
    SendSmsResponse,
    Sms,
    SmsBatchResult,
)
from ._version import __version__

DEFAULT_BASE_URL = "https://api.textbee.dev/api/v1"
DEFAULT_TIMEOUT = 30.0
CLIENT_ID = f"textbee-python/{__version__}"

DateInput = Union[str, datetime]
QueryValue = Union[str, int, None]


class Textbee:
    """Client for the textbee REST API.

    ```python
    textbee = Textbee(api_key=os.environ["TEXTBEE_API_KEY"])
    textbee.send_sms(recipients=["+12025550123"], message="Hello from textbee!")
    ```
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Create a client.

        ``api_key`` comes from the textbee dashboard and is sent as the
        ``x-api-key`` header.

        ``base_url`` points the client at a self-hosted instance instead of
        ``https://api.textbee.dev/api/v1``. A bare origin such as
        ``https://sms.example.com`` gets ``/api/v1`` added. A URL that contains
        ``/api/v1`` is cut there, so a pasted endpoint URL still works. Any
        other path is used as given, for proxies that map a prefix onto the
        API. A value with no scheme gets ``https://``. An invalid URL raises
        ``ValueError`` rather than falling back, so a typo never sends your key
        to the public API.

        ``timeout`` is in seconds and applies to each connect and read, not to
        the request as a whole.
        """
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError("Textbee requires an api_key")
        # Never echo the key: this error ends up in logs and error trackers.
        if not api_key.isascii() or not api_key.isprintable():
            raise ValueError("api_key contains invalid characters")

        self._api_key = api_key
        self._base_url = _normalize_base_url(base_url)
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"Textbee(base_url={self._base_url!r})"

    def send_sms(
        self,
        *,
        message: str,
        recipients: Sequence[str],
        device_id: str | None = None,
        sim_subscription_id: int | None = None,
        scheduled_at: DateInput | None = None,
    ) -> SendSmsResponse:
        """Send an SMS to one or more recipients.

        ``recipients`` are phone numbers in international format.

        ``device_id`` picks the sending phone. Get ids from ``get_devices()``.
        Omit it and textbee picks the sender: your default device first,
        otherwise the enabled device with the most recent heartbeat. A malformed
        id fails with 400 rather than falling back, so a typo cannot send from
        the wrong phone.

        ``sim_subscription_id`` picks the SIM on a multi-SIM phone. It is not
        validated: an id that does not match a SIM in the phone is ignored and
        the message goes out from the SIM configured in the app, or the system
        default. Find the id in the Android app under Dashboard, "SIM Cards".

        ``scheduled_at`` sends later instead of now, as an ISO 8601 string or a
        timezone-aware ``datetime`` in the future.
        """
        if isinstance(recipients, str):
            raise TypeError("recipients must be a list of phone numbers, not a string")

        body: dict[str, Any] = {
            "message": message,
            "recipients": list(recipients),
        }
        if device_id is not None:
            body["deviceId"] = device_id
        if sim_subscription_id is not None:
            body["simSubscriptionId"] = sim_subscription_id
        if scheduled_at is not None:
            body["scheduledAt"] = _to_iso_string(scheduled_at, "scheduled_at")

        payload = self._request("POST", "/gateway/send-sms", body=body)
        return cast(SendSmsResponse, _data(payload))

    def get_devices(self) -> list[Device]:
        """List the devices registered to this account."""
        payload = self._request("GET", "/gateway/devices")
        return cast(list[Device], _data(payload))

    def get_device(self, device_id: str) -> Device:
        """Fetch a single device by id."""
        payload = self._request("GET", f"/gateway/devices/{_segment(device_id)}")
        return cast(Device, _data(payload))

    def set_default_device(self, device_id: str) -> Device:
        """Make a device the default sender for requests that omit ``device_id``."""
        payload = self._request("POST", f"/gateway/devices/{_segment(device_id)}/set-default")
        return cast(Device, _data(payload))

    def get_messages(
        self,
        *,
        device_ids: Sequence[str] | None = None,
        sms_batch_id: str | None = None,
        direction: Direction | None = None,
        status: MessageStatus | None = None,
        search: str | None = None,
        from_: DateInput | None = None,
        to: DateInput | None = None,
        order: Order | None = None,
        page: int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> MessageList:
        """Page through the account's sent and received messages.

        ``device_ids`` narrows to some devices; omit it for every device.
        ``sms_batch_id`` narrows to one send; combine it with
        ``status="failed"`` to list the recipients that failed. ``search`` is a
        free text match across body, recipient and sender.

        ``from_`` is an inclusive lower bound on when the platform stored the
        message, ``to`` an exclusive upper bound. Pass a timezone-aware
        ``datetime`` or a string with an explicit timezone
        (``2026-08-01T00:00:00Z``); a bare date like ``2026-08-01`` is read as
        UTC midnight.

        ``page`` is 1-based and ``limit`` defaults to 50, capped at 100 by the
        API. ``cursor`` is ``meta["nextCursor"]`` from a previous response and
        is mutually exclusive with ``page``; in cursor mode ``meta`` omits the
        total count.
        """
        if isinstance(device_ids, str):
            raise TypeError("device_ids must be a list of device ids, not a string")

        # The endpoint returns {data, meta} with no outer wrapper, so the whole
        # payload is the result.
        payload = self._request(
            "GET",
            "/gateway/messages",
            query={
                "deviceIds": ",".join(device_ids) if device_ids else None,
                "smsBatchId": sms_batch_id,
                "direction": direction,
                "status": status,
                "search": search,
                "from": _to_iso_string(from_, "from_"),
                "to": _to_iso_string(to, "to"),
                "order": order,
                "page": page,
                "limit": limit,
                "cursor": cursor,
            },
        )
        _data(payload)
        return cast(MessageList, payload)

    def iterate_messages(
        self,
        *,
        device_ids: Sequence[str] | None = None,
        sms_batch_id: str | None = None,
        direction: Direction | None = None,
        status: MessageStatus | None = None,
        search: str | None = None,
        from_: DateInput | None = None,
        to: DateInput | None = None,
        order: Order | None = None,
        page_size: int | None = None,
    ) -> Iterator[Message]:
        """Iterate every message matching the filters, following the cursor to the end.

        Takes the ``get_messages`` filters minus the paging knobs it drives
        itself. ``page_size`` is how many messages each request fetches (the
        API's ``limit``), not a cap on the total. Use ``order="asc"`` with a
        ``from_`` bound to drain forward when polling. ``from_`` is inclusive,
        so a poll that resumes from the last ``createdAt`` sees that message
        again; skip ids you have already handled.

        ```python
        for message in textbee.iterate_messages(direction="received"):
            handle(message)
        ```
        """
        filters: dict[str, Any] = {
            "device_ids": device_ids,
            "sms_batch_id": sms_batch_id,
            "direction": direction,
            "status": status,
            "search": search,
            "from_": from_,
            "to": to,
            "order": order,
            "limit": page_size,
        }

        page = self.get_messages(**filters)
        yield from page["data"]

        while page["meta"].get("nextCursor"):
            page = self.get_messages(**filters, cursor=page["meta"].get("nextCursor"))
            yield from page["data"]

    def get_sms(self, device_id: str, sms_id: str) -> Sms:
        """Fetch a single message, including its delivery status."""
        payload = self._request(
            "GET",
            f"/gateway/devices/{_segment(device_id)}/sms/{_segment(sms_id)}",
        )
        return cast(Sms, _data(payload))

    def get_sms_batch(self, device_id: str, sms_batch_id: str) -> SmsBatchResult:
        """Fetch a batch and every message in it, by the ``smsBatchId`` a send returns.

        Needs the id of the device that sent the batch. When textbee picked the
        device, use ``get_messages(sms_batch_id=...)`` instead.
        """
        payload = self._request(
            "GET",
            f"/gateway/devices/{_segment(device_id)}/sms-batch/{_segment(sms_batch_id)}",
        )
        return cast(SmsBatchResult, _data(payload))

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, QueryValue] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        params = {key: str(value) for key, value in (query or {}).items() if value is not None}
        if params:
            url = f"{url}?{urlencode(params)}"

        headers = {
            "x-api-key": self._api_key,
            "user-agent": CLIENT_ID,
            "x-sdk-client": CLIENT_ID,
            "accept": "application/json",
        }
        data = None
        if body is not None:
            headers["content-type"] = "application/json"
            data = json.dumps(body).encode("utf-8")

        request = Request(url, data=data, headers=headers, method=method)
        location = None
        try:
            with _OPENER.open(request, timeout=self._timeout) as response:
                status: int = response.status
                raw: bytes = response.read()
        except HTTPError as error:
            status = error.code
            location = error.headers.get("location") if error.headers else None
            raw = error.read()
            error.close()

        payload = _parse_body(raw)

        if 300 <= status < 400:
            # Redirects are refused so the key never follows one to another
            # host or to plain http.
            target = f" to {location}" if location else ""
            raise TextbeeError(
                status, f"base_url redirects{target}. Set base_url to the final URL.", payload
            )
        if not 200 <= status < 300:
            raise TextbeeError(status, _error_message(payload, status), payload)
        if not isinstance(payload, dict):
            raise TextbeeError(
                status,
                "The response was not a JSON object. Check that base_url points at the "
                "textbee API.",
                payload,
            )

        return payload


class _RefuseRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = build_opener(_RefuseRedirects)


def _data(payload: dict[str, Any]) -> Any:
    if "data" not in payload:
        raise TextbeeError(
            200,
            "The response had no data field. Check that base_url points at the textbee API.",
            payload,
        )
    return payload["data"]


def _normalize_base_url(raw: str | None) -> str:
    """Resolve the API root from a base URL. Same rule as textbee-js.

    A bare origin gets /api/v1, a path containing /api/v1 is cut there, and
    any other path is kept as given for proxies that map a prefix onto the API.
    """
    value = (raw or "").strip()
    if not value:
        return DEFAULT_BASE_URL

    with_scheme = value if _HAS_SCHEME.match(value) else f"https://{value}"
    parts = urlsplit(with_scheme)
    scheme = parts.scheme.lower()

    if scheme not in ("http", "https"):
        raise ValueError(f"base_url must be http or https, got {scheme!r}")
    if "@" in parts.netloc:
        # textbee never uses basic auth, and https://api.textbee.dev@evil.com
        # reads like textbee while sending the key to evil.com.
        raise ValueError("base_url must not contain a username or password")

    try:
        port = parts.port
    except ValueError:
        port = -1
    host = _ascii_host(parts.hostname)
    if port == -1 or not host or not _VALID_HOST.fullmatch(host):
        raise ValueError(
            "base_url is not a valid URL. Use your textbee API, for example "
            "https://sms.example.com."
        )

    if ":" in host:
        host = f"[{host}]"
    origin = f"{scheme}://{host}"
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        origin = f"{origin}:{port}"

    segments = [segment for segment in parts.path.split("/") if segment]
    lower = [segment.lower() for segment in segments]
    cut = -1
    for index in range(len(lower) - 2, -1, -1):
        if lower[index] == "api" and lower[index + 1] == "v1":
            cut = index + 2
            break

    if not segments:
        path = ["api", "v1"]
    elif cut == -1:
        path = segments
    else:
        # Anything after /api/v1 is a route, not part of the root, so it is dropped.
        path = [*segments[: cut - 2], "api", "v1"]

    return "/".join([origin, *(quote(segment, safe=_PATH_SAFE) for segment in path)])


def _ascii_host(host: str | None) -> str | None:
    """Punycode an internationalized hostname, as browsers and fetch do."""
    if not host or host.isascii():
        return host
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return None


_HAS_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_VALID_HOST = re.compile(r"[A-Za-z0-9._~-]+|[0-9A-Fa-f:.]+")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_PATH_SAFE = "!$&'()*+,;=:@~%"


def _segment(value: str) -> str:
    return quote(value, safe="")


def _to_iso_string(value: DateInput | None, name: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, datetime):
        raise TypeError(
            f"{name} must be an ISO 8601 string or a timezone-aware datetime, "
            f"not {type(value).__name__}"
        )

    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")

    utc = value.astimezone(timezone.utc)
    return utc.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_body(raw: bytes) -> Any:
    if not raw:
        return None

    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _error_message(payload: Any, status: int) -> str:
    if isinstance(payload, dict):
        # Framework errors ({statusCode, message, error}) carry the detail in
        # message, and error is only the reason phrase such as "Not Found".
        keys = ("message", "error") if "statusCode" in payload else ("error", "message")
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
                return "; ".join(value)

    return f"HTTP {status}"
