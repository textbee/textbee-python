from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

DEFAULT_TOLERANCE_SECONDS = 300
"""Default maximum distance, in seconds, between a signed timestamp and now."""


class _Headers(Protocol):
    def items(self) -> Iterable[tuple[str, str]]: ...


def verify_webhook_signature(
    *,
    payload: str | bytes | bytearray | Mapping[str, Any],
    signature: str | None,
    signing_secret: str | None,
) -> bool:
    """Check that a webhook really came from textbee, using ``X-Signature``.

    textbee signs deliveries with HMAC-SHA256 over the request body and sends
    the hex digest in the ``X-Signature`` header. Comparison is constant time.

    .. deprecated:: 0.3.0
        ``X-Signature`` has no timestamp, so a captured delivery can be
        replayed. textbee still sends it, so this keeps working. Use
        :func:`verify_webhook_request` or
        :func:`verify_timestamped_webhook_signature`.

    ``payload`` is the request body. Prefer the raw body (``bytes`` or ``str``).
    A parsed dict works too, re-serialized the way the API serializes it, but
    only while key order and number formatting survive the round trip.
    ``signing_secret`` is shown with the webhook subscription in the dashboard.
    """
    if not signature or not signing_secret:
        return False

    expected = hmac.new(
        signing_secret.encode("utf-8"),
        _body_bytes(payload),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected.encode("ascii"), signature.lower().encode("utf-8"))


def verify_timestamped_webhook_signature(
    *,
    payload: str | bytes | bytearray,
    signature_header: str | None,
    signing_secret: str | None,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """Check the timestamped signature in the ``X-Textbee-Signature`` header.

    The header is ``t=<unix seconds>,v1=<hex>``, where the hex value is
    HMAC-SHA256 over ``<t>.<raw body>``. Any matching ``v1`` entry passes.
    Returns False when the header is malformed, no ``v1`` entry matches, or
    ``t`` is more than ``tolerance_seconds`` away from ``now`` in either
    direction. ``payload`` is the raw request body, exactly as received.
    ``now`` defaults to the system clock, in unix seconds.
    """
    if not signature_header or not signing_secret or not _is_raw_body(payload):
        return False

    parsed = _parse_signature_header(signature_header)
    if parsed is None:
        return False
    timestamp, candidates = parsed

    current = time.time() if now is None else now
    # Written as a negated <= so a NaN tolerance or clock rejects instead of passing.
    if not abs(current - int(timestamp)) <= tolerance_seconds:
        return False

    expected = _hmac_hex(signing_secret, timestamp.encode("ascii") + b"." + _raw_bytes(payload))

    matched = False
    for candidate in candidates:
        if hmac.compare_digest(expected, candidate.lower().encode("utf-8")):
            matched = True
    return matched


def verify_webhook_request(
    *,
    payload: str | bytes | bytearray,
    headers: Mapping[str, str] | _Headers,
    signing_secret: str | None,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
    allow_legacy: bool = False,
) -> bool:
    """Check that a webhook request came from textbee, from its headers.

    Verifies the timestamped signature in ``X-Textbee-Signature``. Header
    names match in any case. When that header is absent and ``allow_legacy``
    is True, falls back to ``X-Signature``, which has no replay protection.
    ``payload`` is the raw request body, exactly as received.
    """
    signature_header = _get_header(headers, "x-textbee-signature")
    if signature_header is not None:
        return verify_timestamped_webhook_signature(
            payload=payload,
            signature_header=signature_header,
            signing_secret=signing_secret,
            tolerance_seconds=tolerance_seconds,
            now=now,
        )

    if not allow_legacy:
        return False

    legacy_signature = _get_header(headers, "x-signature")
    if not legacy_signature or not signing_secret or not _is_raw_body(payload):
        return False

    expected = _hmac_hex(signing_secret, _raw_bytes(payload))
    return hmac.compare_digest(expected, legacy_signature.lower().encode("utf-8"))


# 1 to 15 digits: ample for unix seconds, and short enough that int() never
# hits Python's limit on converting long digit strings.
_TIMESTAMP = re.compile("[0-9]{1,15}")


def _parse_signature_header(header: str) -> tuple[str, list[str]] | None:
    timestamp: str | None = None
    candidates: list[str] = []

    for part in header.split(","):
        key, separator, value = part.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()

        if key == "t":
            if timestamp is not None or not _TIMESTAMP.fullmatch(value):
                return None
            timestamp = value
        elif key == "v1" and value:
            candidates.append(value)

    if timestamp is None or not candidates:
        return None
    return timestamp, candidates


def _get_header(headers: Mapping[str, str] | _Headers, name: str) -> str | None:
    values = [value for key, value in headers.items() if key.lower() == name]
    if not values:
        return None
    return ",".join(values)


def _hmac_hex(secret: str, message: bytes) -> bytes:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest().encode("ascii")


def _is_raw_body(payload: object) -> bool:
    # A framework that did not read the body can hand over None or a dict.
    return isinstance(payload, (str, bytes, bytearray))


def _raw_bytes(payload: str | bytes | bytearray) -> bytes:
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return bytes(payload)


def _body_bytes(payload: str | bytes | bytearray | Mapping[str, Any]) -> bytes:
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")

    # Matches JSON.stringify: no spaces, non-ASCII left unescaped, and a lone
    # surrogate (from a truncated emoji) escaped instead of raising.
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return _LONE_SURROGATE.sub(_escape, text).encode("utf-8")


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def _escape(match: re.Match[str]) -> str:
    return f"\\u{ord(match.group()):04x}"
