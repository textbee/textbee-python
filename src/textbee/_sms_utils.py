"""Pure SMS helpers. No API key, no network, no dependencies.

They work with any SMS provider, not just textbee.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# GSM 03.38 character tables
# ---------------------------------------------------------------------------

# The GSM 03.38 default alphabet, one septet per character.
#
# @ £ $ ¥ è é ù ì ò Ç LF Ø ø CR Å å Δ _ Φ Γ Λ Ω Π Ψ Σ Θ Ξ Æ æ ß É
# space ! " # ¤ % & ' ( ) * + , - . / 0-9 : ; < = > ?
# ¡ A-Z Ä Ö Ñ Ü § ¿ a-z ä ö ñ ü à
#
# Non-ASCII entries are escaped so lookalike characters cannot sneak in.
GSM7_BASE = (
    "@\u00a3$\u00a5\u00e8\u00e9\u00f9\u00ec\u00f2\u00c7\n\u00d8\u00f8\r\u00c5\u00e5"
    "\u0394_\u03a6\u0393\u039b\u03a9\u03a0\u03a8\u03a3\u0398\u039e"
    "\u00c6\u00e6\u00df\u00c9"
    " !\"#\u00a4%&'()*+,-./0123456789:;<=>?"
    "\u00a1ABCDEFGHIJKLMNOPQRSTUVWXYZ\u00c4\u00d6\u00d1\u00dc\u00a7"
    "\u00bfabcdefghijklmnopqrstuvwxyz\u00e4\u00f6\u00f1\u00fc\u00e0"
)

# The GSM 03.38 extension table. Each of these costs two septets because it is
# sent as an escape byte followed by the character.
#
# form feed, ^ { } \ [ ~ ] | and the euro sign
GSM7_EXTENSION = "\f^{}\\[~]|\u20ac"

GSM7_BASE_SET = frozenset(GSM7_BASE)
GSM7_EXTENSION_SET = frozenset(GSM7_EXTENSION)

GSM7_SINGLE_SEGMENT_LIMIT = 160
GSM7_CONCATENATED_SEGMENT_LIMIT = 153
UCS2_SINGLE_SEGMENT_LIMIT = 70
UCS2_CONCATENATED_SEGMENT_LIMIT = 67


def _gsm7_cost(char: str) -> int | None:
    """Septets a character costs in GSM-7, or None when it is not encodable."""
    if char in GSM7_BASE_SET:
        return 1
    if char in GSM7_EXTENSION_SET:
        return 2
    return None


def _is_gsm7_char(char: str) -> bool:
    return _gsm7_cost(char) is not None


def _utf16_units(char: str) -> int:
    """UTF-16 code units a code point occupies: 2 for astral characters like emoji."""
    return 2 if ord(char) > 0xFFFF else 1


# ---------------------------------------------------------------------------
# Segments and encoding
# ---------------------------------------------------------------------------

SmsEncoding = Literal["gsm-7", "ucs-2"]
"""How the message body will be encoded on the wire."""


@dataclass(frozen=True)
class SmsSegmentInfo:
    encoding: SmsEncoding
    """``gsm-7`` when every character fits the GSM 03.38 alphabet, else ``ucs-2``."""

    length: int
    """Encoded length: septets for gsm-7 (extension characters cost 2), UTF-16
    code units for ucs-2 (emoji and other astral characters cost 2)."""

    segments: int
    """Number of SMS segments the message occupies. 0 for an empty string."""

    remaining_in_segment: int
    """How many more single-unit characters fit before another segment is
    needed. A two-unit character (extension character or emoji) may not fit
    even when this is 1."""


def get_sms_encoding(message: str) -> SmsEncoding:
    """Detect the encoding a message requires.

    ``gsm-7`` when every character is in the GSM 03.38 alphabet or its
    extension table, ``ucs-2`` otherwise. An empty string reports ``gsm-7``.
    """
    for char in message:
        if not _is_gsm7_char(char):
            return "ucs-2"
    return "gsm-7"


def count_sms_segments(message: str) -> SmsSegmentInfo:
    """Compute encoding, encoded length, segment count and remaining room.

    Splits the way a standards-following handset does. Single segment limits
    are 160 septets (gsm-7) and 70 code units (ucs-2). Concatenated messages
    carry a header, shrinking each segment to 153 and 67. A two-unit character
    never straddles a boundary: it moves whole to the next segment and wastes
    one unit.
    """
    encoding = get_sms_encoding(message)
    costs = _character_costs(message, encoding)
    length = sum(costs)

    single_limit = GSM7_SINGLE_SEGMENT_LIMIT if encoding == "gsm-7" else UCS2_SINGLE_SEGMENT_LIMIT

    if length == 0:
        return SmsSegmentInfo(encoding, 0, 0, single_limit)

    if length <= single_limit:
        return SmsSegmentInfo(encoding, length, 1, single_limit - length)

    concatenated_limit = (
        GSM7_CONCATENATED_SEGMENT_LIMIT if encoding == "gsm-7" else UCS2_CONCATENATED_SEGMENT_LIMIT
    )

    segments = 1
    used_in_segment = 0

    for cost in costs:
        if used_in_segment + cost > concatenated_limit:
            segments += 1
            used_in_segment = cost
        else:
            used_in_segment += cost

    return SmsSegmentInfo(encoding, length, segments, concatenated_limit - used_in_segment)


def find_non_gsm7_characters(message: str) -> list[str]:
    """The unique characters forcing a message into ucs-2, in first appearance order.

    Empty when the message is already gsm-7 safe. Pair it with
    ``sanitize_for_gsm7`` to see what is left after sanitizing.
    """
    found: dict[str, None] = {}

    for char in message:
        if not _is_gsm7_char(char):
            found.setdefault(char, None)

    return list(found)


def _character_costs(message: str, encoding: SmsEncoding) -> list[int]:
    """Per-character unit costs, in message order."""
    if encoding == "gsm-7":
        return [_gsm7_cost(char) or 1 for char in message]
    return [_utf16_units(char) for char in message]


# ---------------------------------------------------------------------------
# GSM-7 sanitization
# ---------------------------------------------------------------------------

# Unicode characters that have a safe GSM-7 equivalent. Keys are escaped so
# this file stays readable and never carries the very characters it replaces.
GSM7_REPLACEMENTS: dict[str, str] = {
    # curly single quotes, prime, acute accent, backtick
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u2032": "'",
    "\u00b4": "'",
    "`": "'",
    # curly double quotes, double prime, guillemets
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u2033": '"',
    "\u00ab": '"',
    "\u00bb": '"',
    # ellipsis
    "\u2026": "...",
    # hyphen variants, en dash, em dash, horizontal bar, minus sign
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
    # non-breaking and typographic spaces
    "\u00a0": " ",
    "\u2000": " ",
    "\u2001": " ",
    "\u2002": " ",
    "\u2003": " ",
    "\u2004": " ",
    "\u2005": " ",
    "\u2006": " ",
    "\u2007": " ",
    "\u2008": " ",
    "\u2009": " ",
    "\u200a": " ",
    "\u202f": " ",
    "\u205f": " ",
    "\u3000": " ",
    # invisible characters worth dropping outright
    "\u200b": "",
    "\ufeff": "",
    "\u00ad": "",
    # fraction slash
    "\u2044": "/",
}

# Letters NFD cannot decompose into an ASCII base letter.
TRANSLITERATION_EXTRAS: dict[str, str] = {
    "\u0152": "OE",
    "\u0153": "oe",
    "\u0141": "L",
    "\u0142": "l",
    "\u0110": "D",
    "\u0111": "d",
    "\u0131": "i",
}

COMBINING_MARKS = re.compile("[\u0300-\u036f]")


def sanitize_for_gsm7(message: str, *, transliterate_accents: bool = False) -> str:
    """Replace common Unicode lookalikes with GSM-7 equivalents.

    Curly quotes, ellipsis, exotic spaces and dashes are swapped, so pasted
    text does not silently fall into ucs-2 and more than double its segment
    count.

    ``transliterate_accents`` strips accents from Latin letters that GSM-7 does
    not carry, so ``ï`` becomes ``i``. Letters GSM-7 already has (é, ü, ñ, à
    and friends) are never touched.

    Best effort by design: characters with no safe replacement are left
    alone. Check the result with ``get_sms_encoding`` and inspect leftovers
    with ``find_non_gsm7_characters``.
    """
    parts: list[str] = []

    for char in message:
        replacement = GSM7_REPLACEMENTS.get(char)
        if replacement is not None:
            parts.append(replacement)
        elif _is_gsm7_char(char) or not transliterate_accents:
            parts.append(char)
        else:
            parts.append(_transliterate(char))

    return "".join(parts)


def _transliterate(char: str) -> str:
    """Best-effort ASCII form of a single character, or the character unchanged."""
    extra = TRANSLITERATION_EXTRAS.get(char)
    if extra is not None:
        return extra

    stripped = COMBINING_MARKS.sub("", unicodedata.normalize("NFD", char))
    if len(stripped) == 1 and stripped.isascii() and stripped.isalpha():
        return stripped

    return char


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------

E164_PATTERN = re.compile(r"\+[1-9][0-9]{1,14}")
# JavaScript's whitespace set, so trimming matches textbee-js. Python's own
# set misses the byte order mark that leads many CSV exports.
JS_WHITESPACE = (
    "\t\n\x0b\x0c\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
FORMATTING_CHARACTERS = re.compile(f"[{re.escape(JS_WHITESPACE)}.\\-()]")
DIGITS_ONLY = re.compile(r"[0-9]+")
COUNTRY_CODE_PATTERN = re.compile(r"[1-9][0-9]{0,2}")


def is_valid_e164(value: str) -> bool:
    """Check that a string is strictly E.164.

    A plus sign, a leading digit 1-9, then 1 to 14 more digits, nothing else.
    This is a format check only. It does not know country dialing plans, so a
    well-formed but unassigned number still passes.
    """
    return E164_PATTERN.fullmatch(value) is not None


def normalize_phone_number(
    value: str,
    *,
    default_country_code: str | int | None = None,
) -> str | None:
    """Normalize free-form phone input to E.164, or return None when it cannot be done safely.

    Strips spaces, dots, hyphens and parentheses, converts a leading ``00`` to
    a plus, and optionally applies a default country code.

    ``default_country_code`` applies to national-format input, with or without
    a leading plus: ``"1"`` and ``"+1"`` both work. With it, ``(202) 555-0123``
    becomes ``+12025550123`` and one leading trunk zero is dropped. Without it,
    national-format input returns None.

    This is a formatter, not libphonenumber: it never validates a number
    against a country dialing plan. Bad input returns None, a bad
    ``default_country_code`` raises ``ValueError``.
    """
    trimmed = value.strip(JS_WHITESPACE)
    if not trimmed:
        return None

    has_plus = trimmed.startswith("+")
    without_plus = trimmed[1:] if has_plus else trimmed
    digits = FORMATTING_CHARACTERS.sub("", without_plus)

    if DIGITS_ONLY.fullmatch(digits) is None:
        return None

    if has_plus:
        return _as_e164_or_none(f"+{digits}")

    if digits.startswith("00"):
        return _as_e164_or_none(f"+{digits[2:]}")

    country_code = _normalize_country_code(default_country_code)
    if country_code is None:
        return None

    national = digits[1:] if digits.startswith("0") else digits

    return _as_e164_or_none(f"+{country_code}{national}")


def _as_e164_or_none(candidate: str) -> str | None:
    return candidate if is_valid_e164(candidate) else None


def _normalize_country_code(value: str | int | None) -> str | None:
    if value is None:
        return None

    text = str(value)
    code = text[1:] if text.startswith("+") else text
    if COUNTRY_CODE_PATTERN.fullmatch(code) is None:
        raise ValueError(
            f"default_country_code must be 1 to 3 digits starting with 1-9, got {value!r}"
        )

    return code
