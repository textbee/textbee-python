from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any


def verify_webhook_signature(
    *,
    payload: str | bytes | bytearray | Mapping[str, Any],
    signature: str | None,
    signing_secret: str | None,
) -> bool:
    """Check that a webhook really came from textbee.

    textbee signs deliveries with HMAC-SHA256 over the request body and sends
    the hex digest in the ``X-Signature`` header. Comparison is constant time.

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
