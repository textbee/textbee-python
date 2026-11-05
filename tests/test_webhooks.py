from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from textbee import (
    verify_timestamped_webhook_signature,
    verify_webhook_request,
    verify_webhook_signature,
)

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


NOW = 1_762_300_000
RAW_BODY = to_js_json({**payload, "idempotencyKey": "evt_1"})


def sign_timestamped(body: str, t: int | str = NOW, secret: str = SIGNING_SECRET) -> str:
    """Exactly how the api builds X-Textbee-Signature."""
    v1 = hmac.new(secret.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


def valid_v1() -> str:
    return sign_timestamped(RAW_BODY).split("v1=")[1]


def verify(header: str | None, *, body: str | bytes = RAW_BODY, now: float = NOW) -> bool:
    return verify_timestamped_webhook_signature(
        payload=body, signature_header=header, signing_secret=SIGNING_SECRET, now=now
    )


def test_timestamped_accepts_a_header_produced_the_way_the_api_produces_it() -> None:
    assert verify(sign_timestamped(RAW_BODY))


def test_timestamped_accepts_raw_bytes() -> None:
    assert verify(sign_timestamped(RAW_BODY), body=RAW_BODY.encode())


def test_timestamped_accepts_a_non_ascii_body() -> None:
    body = to_js_json({**payload, "message": "café 👍"})
    assert verify(sign_timestamped(body), body=body)
    assert verify(sign_timestamped(body), body=body.encode("utf-8"))


def test_timestamped_rejects_the_wrong_signing_secret() -> None:
    assert not verify(sign_timestamped(RAW_BODY, secret="whsec_other_secret"))


def test_timestamped_rejects_a_tampered_body() -> None:
    assert not verify(sign_timestamped(RAW_BODY), body=RAW_BODY.replace("hello", "tampered"))


def test_timestamped_rejects_a_stale_timestamp() -> None:
    header = sign_timestamped(RAW_BODY)
    assert verify(header, now=NOW + 300)
    assert not verify(header, now=NOW + 301)


def test_timestamped_rejects_a_future_timestamp() -> None:
    header = sign_timestamped(RAW_BODY)
    assert verify(header, now=NOW - 300)
    assert not verify(header, now=NOW - 301)


def test_timestamped_honors_a_custom_tolerance() -> None:
    assert not verify_timestamped_webhook_signature(
        payload=RAW_BODY,
        signature_header=sign_timestamped(RAW_BODY),
        signing_secret=SIGNING_SECRET,
        tolerance_seconds=10,
        now=NOW + 11,
    )


def test_timestamped_rejects_a_nan_clock() -> None:
    assert not verify(sign_timestamped(RAW_BODY), now=float("nan"))


def test_timestamped_rejects_a_signature_moved_to_another_t() -> None:
    assert not verify(f"t={NOW + 1},v1={valid_v1()}")


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "garbage",
        "v1=" + "a" * 64,
        f"t={NOW}",
        f"t={NOW},v1=",
        f"t=-{NOW},v1=" + "a" * 64,
        f"t={NOW}.5,v1=" + "a" * 64,
        "t=abc,v1=" + "a" * 64,
        "t=,v1=" + "a" * 64,
        "t=\u0661\u0662,v1=" + "a" * 64,
        "t=" + "9" * 5000 + ",v1=" + "a" * 64,
    ],
)
def test_timestamped_rejects_malformed_headers(header: str | None) -> None:
    assert not verify(header)


def test_timestamped_rejects_a_valid_v1_with_a_malformed_t() -> None:
    assert not verify(f"t={NOW}x,v1={valid_v1()}")


def test_timestamped_accepts_any_matching_v1() -> None:
    other = "a" * 64
    assert verify(f"t={NOW},v1={other},v1={valid_v1()}")
    assert verify(f"t={NOW}, v1={valid_v1()}, v1={other}")
    assert verify(f"t={NOW},v0={other},v1={valid_v1()}")
    assert not verify(f"t={NOW},v1={other},v1={other}")


def test_timestamped_rejects_a_duplicate_t() -> None:
    assert not verify(f"t={NOW},t={NOW},v1={valid_v1()}")
    assert not verify(f"t={NOW},v1={valid_v1()},t={NOW - 1000}")


def test_timestamped_rejects_a_non_ascii_v1_without_raising() -> None:
    assert not verify(f"t={NOW},v1=" + "é" * 64)


def test_timestamped_accepts_an_uppercase_hex_v1() -> None:
    assert verify(f"t={NOW},v1={valid_v1().upper()}")


LEGACY_SIGNATURE = hmac.new(SIGNING_SECRET.encode(), RAW_BODY.encode(), hashlib.sha256).hexdigest()


def test_request_verifies_the_signature_header() -> None:
    assert verify_webhook_request(
        payload=RAW_BODY.encode(),
        headers={"X-Textbee-Signature": sign_timestamped(RAW_BODY)},
        signing_secret=SIGNING_SECRET,
        now=NOW,
    )


def test_request_matches_header_names_in_any_case() -> None:
    for name in ("x-textbee-signature", "X-TEXTBEE-SIGNATURE", "X-Textbee-Signature"):
        assert verify_webhook_request(
            payload=RAW_BODY,
            headers={name: sign_timestamped(RAW_BODY)},
            signing_secret=SIGNING_SECRET,
            now=NOW,
        )


def test_request_rejects_a_stale_signature_header() -> None:
    assert not verify_webhook_request(
        payload=RAW_BODY,
        headers={"X-Textbee-Signature": sign_timestamped(RAW_BODY)},
        signing_secret=SIGNING_SECRET,
        now=NOW + 301,
    )


def test_request_ignores_x_signature_by_default() -> None:
    assert not verify_webhook_request(
        payload=RAW_BODY,
        headers={"X-Signature": LEGACY_SIGNATURE},
        signing_secret=SIGNING_SECRET,
        now=NOW,
    )


def test_request_falls_back_to_x_signature_only_with_allow_legacy() -> None:
    assert verify_webhook_request(
        payload=RAW_BODY.encode(),
        headers={"x-signature": LEGACY_SIGNATURE},
        signing_secret=SIGNING_SECRET,
        allow_legacy=True,
    )
    assert not verify_webhook_request(
        payload=RAW_BODY,
        headers={"x-signature": sign(payload, "whsec_other_secret")},
        signing_secret=SIGNING_SECRET,
        allow_legacy=True,
    )
    assert not verify_webhook_request(
        payload=RAW_BODY,
        headers={"x-signature": "é" * 64},
        signing_secret=SIGNING_SECRET,
        allow_legacy=True,
    )


def test_request_does_not_fall_back_when_the_signature_header_is_present() -> None:
    assert not verify_webhook_request(
        payload=RAW_BODY,
        headers={
            "X-Textbee-Signature": sign_timestamped(RAW_BODY),
            "X-Signature": LEGACY_SIGNATURE,
        },
        signing_secret=SIGNING_SECRET,
        now=NOW + 301,
        allow_legacy=True,
    )


def test_request_rejects_a_request_with_no_signature_headers() -> None:
    assert not verify_webhook_request(
        payload=RAW_BODY, headers={}, signing_secret=SIGNING_SECRET, allow_legacy=True
    )
