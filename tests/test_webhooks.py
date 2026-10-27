from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from textbee import verify_webhook_signature

SIGNING_SECRET = "whsec_test_secret"

payload: dict[str, Any] = {
    "event": "MESSAGE_RECEIVED",
    "smsId": "65f0000000000000000000bb",
    "sender": "+12025550123",
    "message": "hello",
}


def to_js_json(body: Any) -> str:
    """What JSON.stringify produces for plain payloads like these. Pinned below too."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def sign(body: Any, secret: str = SIGNING_SECRET) -> str:
    """Exactly how the api signs a delivery, so this asserts cross-implementation parity."""
    return hmac.new(secret.encode(), to_js_json(body).encode(), hashlib.sha256).hexdigest()


def test_accepts_a_signature_produced_the_way_the_api_produces_it() -> None:
    assert verify_webhook_signature(
        payload=to_js_json(payload),
        signature=sign(payload),
        signing_secret=SIGNING_SECRET,
    )


def test_accepts_a_raw_bytes_body() -> None:
    assert verify_webhook_signature(
        payload=to_js_json(payload).encode(),
        signature=sign(payload),
        signing_secret=SIGNING_SECRET,
    )


def test_accepts_a_dict_payload_as_a_convenience() -> None:
    assert verify_webhook_signature(
        payload=payload,
        signature=sign(payload),
        signing_secret=SIGNING_SECRET,
    )


def test_matches_json_stringify_for_non_ascii_text() -> None:
    unicode_payload = {**payload, "message": "café 👍"}
    # Computed in Node: createHmac('sha256', secret).update(JSON.stringify(body)).digest('hex')
    node_digest = hmac.new(
        SIGNING_SECRET.encode(),
        '{"event":"MESSAGE_RECEIVED","smsId":"65f0000000000000000000bb",'
        '"sender":"+12025550123","message":"café 👍"}'.encode(),
        hashlib.sha256,
    ).hexdigest()

    assert verify_webhook_signature(
        payload=unicode_payload, signature=node_digest, signing_secret=SIGNING_SECRET
    )


def test_accepts_an_uppercase_hex_signature() -> None:
    assert verify_webhook_signature(
        payload=payload,
        signature=sign(payload).upper(),
        signing_secret=SIGNING_SECRET,
    )


def test_rejects_a_tampered_payload() -> None:
    assert not verify_webhook_signature(
        payload={**payload, "message": "tampered"},
        signature=sign(payload),
        signing_secret=SIGNING_SECRET,
    )


def test_rejects_the_wrong_signing_secret() -> None:
    assert not verify_webhook_signature(
        payload=payload,
        signature=sign(payload, "whsec_other_secret"),
        signing_secret=SIGNING_SECRET,
    )


def test_rejects_a_truncated_empty_or_missing_signature() -> None:
    signature = sign(payload)

    assert not verify_webhook_signature(
        payload=payload, signature=signature[:32], signing_secret=SIGNING_SECRET
    )
    assert not verify_webhook_signature(
        payload=payload, signature="", signing_secret=SIGNING_SECRET
    )
    assert not verify_webhook_signature(
        payload=payload, signature=None, signing_secret=SIGNING_SECRET
    )
    assert not verify_webhook_signature(payload=payload, signature=signature, signing_secret="")


def test_rejects_a_non_ascii_signature_without_raising() -> None:
    assert not verify_webhook_signature(
        payload=payload, signature="é" * 64, signing_secret=SIGNING_SECRET
    )


# JSON.stringify output and its HMAC, both computed in Node 22. The payload has
# escapes, a raw line separator, a lone surrogate from a truncated emoji, and
# numbers whose formatting differs between the two languages.
NODE_BODY = (
    '{"event":"MESSAGE_RECEIVED","sms":{"id":"x",'
    '"message":"line1\\nline2\\ttab\\u0001' + chr(0x2028) + 'end \\ud83d",'
    '"n":[1,2.5,0,1e+21]},"sender":"+12025550123"}'
)
NODE_SIGNATURE = "97c7f512b0789f23607ce84fafdf97732134e011360595276419ba56db7e7cc1"


def test_matches_node_for_a_raw_body() -> None:
    assert verify_webhook_signature(
        payload=NODE_BODY, signature=NODE_SIGNATURE, signing_secret=SIGNING_SECRET
    )


def test_matches_node_after_a_json_round_trip() -> None:
    assert verify_webhook_signature(
        payload=json.loads(NODE_BODY), signature=NODE_SIGNATURE, signing_secret=SIGNING_SECRET
    )


def test_accepts_a_bytearray_body() -> None:
    assert verify_webhook_signature(
        payload=bytearray(NODE_BODY.encode("utf-8")),
        signature=NODE_SIGNATURE,
        signing_secret=SIGNING_SECRET,
    )
