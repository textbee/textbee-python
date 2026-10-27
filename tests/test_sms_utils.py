from __future__ import annotations

import pytest

from textbee import (
    SmsSegmentInfo,
    count_sms_segments,
    find_non_gsm7_characters,
    get_sms_encoding,
    is_valid_e164,
    normalize_phone_number,
    sanitize_for_gsm7,
)

EURO = "\u20ac"
THUMBS_UP = "\U0001f44d"
EM_DASH = "\u2014"
EN_DASH = "\u2013"
ELLIPSIS = "\u2026"
LEFT_DOUBLE_QUOTE = "\u201c"
RIGHT_DOUBLE_QUOTE = "\u201d"
LEFT_SINGLE_QUOTE = "\u2018"
RIGHT_SINGLE_QUOTE = "\u2019"
NON_BREAKING_SPACE = "\u00a0"
THIN_SPACE = "\u2009"
ZERO_WIDTH_SPACE = "\u200b"
BYTE_ORDER_MARK = "\ufeff"
SOFT_HYPHEN = "\u00ad"
ZERO_WIDTH_JOINER = "\u200d"
TURKISH_S_CEDILLA = "\u015f"
I_DIAERESIS = "\u00ef"
E_ACUTE = "\u00e9"
LONE_SURROGATE = "\ud83d"


class TestGetSmsEncoding:
    def test_treats_an_empty_string_as_gsm7(self) -> None:
        assert get_sms_encoding("") == "gsm-7"

    def test_accepts_plain_ascii_and_the_accented_letters_gsm7_carries(self) -> None:
        assert get_sms_encoding("hello") == "gsm-7"
        assert get_sms_encoding("é ü ñ à Ω") == "gsm-7"

    def test_accepts_every_extension_table_character(self) -> None:
        for char in ["|", "^", EURO, "{", "}", "[", "]", "~", "\\"]:
            assert get_sms_encoding(char) == "gsm-7"

    def test_falls_back_to_ucs2_for_characters_outside_the_alphabet(self) -> None:
        assert get_sms_encoding(TURKISH_S_CEDILLA) == "ucs-2"
        assert get_sms_encoding(EM_DASH) == "ucs-2"
        assert get_sms_encoding(THUMBS_UP) == "ucs-2"
        assert get_sms_encoding("`") == "ucs-2"
        assert get_sms_encoding(LONE_SURROGATE) == "ucs-2"


class TestCountSmsSegmentsGsm7:
    def test_reports_zero_segments_for_an_empty_message(self) -> None:
        assert count_sms_segments("") == SmsSegmentInfo("gsm-7", 0, 0, 160)

    def test_counts_a_short_message(self) -> None:
        assert count_sms_segments("hello") == SmsSegmentInfo(
            encoding="gsm-7", length=5, segments=1, remaining_in_segment=155
        )

    def test_fits_exactly_160_characters_in_one_segment(self) -> None:
        assert count_sms_segments("a" * 160) == SmsSegmentInfo("gsm-7", 160, 1, 0)

    def test_splits_at_161_characters_and_drops_to_the_153_limit(self) -> None:
        assert count_sms_segments("a" * 161) == SmsSegmentInfo("gsm-7", 161, 2, 145)

    def test_fills_two_segments_exactly_at_306_and_needs_a_third_at_307(self) -> None:
        assert count_sms_segments("a" * 306).segments == 2
        assert count_sms_segments("a" * 306).remaining_in_segment == 0
        assert count_sms_segments("a" * 307).segments == 3

    def test_charges_two_septets_for_extension_characters(self) -> None:
        assert count_sms_segments(EURO).length == 2
        assert count_sms_segments("{}").length == 4

    def test_keeps_an_extension_character_inside_a_single_segment_at_the_limit(self) -> None:
        assert count_sms_segments("a" * 158 + EURO) == SmsSegmentInfo("gsm-7", 160, 1, 0)
        assert count_sms_segments("a" * 159 + EURO).segments == 2

    def test_never_straddles_an_extension_character_across_a_boundary(self) -> None:
        message = "a" * 152 + EURO + "a" * 152

        # 306 septets would fit two 153 septet segments, but the euro sign
        # cannot be split, so one septet is wasted and a third segment is needed.
        assert count_sms_segments(message) == SmsSegmentInfo("gsm-7", 306, 3, 152)

    def test_counts_newline_and_carriage_return_as_one_septet_each(self) -> None:
        assert count_sms_segments("a\nb\rc") == SmsSegmentInfo("gsm-7", 5, 1, 155)


class TestCountSmsSegmentsUcs2:
    def test_counts_an_astral_character_as_two_code_units(self) -> None:
        assert count_sms_segments(THUMBS_UP) == SmsSegmentInfo("ucs-2", 2, 1, 68)

    def test_flips_the_whole_message_to_ucs2_when_one_character_requires_it(self) -> None:
        assert count_sms_segments(f"hello {THUMBS_UP}") == SmsSegmentInfo("ucs-2", 8, 1, 62)

    def test_fits_exactly_70_code_units_in_one_segment_and_splits_at_71(self) -> None:
        assert count_sms_segments(TURKISH_S_CEDILLA * 70) == SmsSegmentInfo("ucs-2", 70, 1, 0)
        assert count_sms_segments(TURKISH_S_CEDILLA * 71) == SmsSegmentInfo("ucs-2", 71, 2, 63)

    def test_never_straddles_a_surrogate_pair_across_a_boundary(self) -> None:
        message = "a" * 66 + THUMBS_UP + "a" * 100

        assert count_sms_segments(message) == SmsSegmentInfo("ucs-2", 168, 3, 32)

    def test_handles_a_lone_surrogate_without_crashing(self) -> None:
        assert count_sms_segments(LONE_SURROGATE) == SmsSegmentInfo("ucs-2", 1, 1, 69)


class TestFindNonGsm7Characters:
    def test_returns_nothing_for_a_gsm7_safe_message(self) -> None:
        assert find_non_gsm7_characters("hello, world!") == []

    def test_lists_unique_offenders_in_first_appearance_order(self) -> None:
        message = (
            f"{LEFT_DOUBLE_QUOTE}hi{RIGHT_DOUBLE_QUOTE} {EM_DASH} {THUMBS_UP} "
            f"{LEFT_DOUBLE_QUOTE}again{RIGHT_DOUBLE_QUOTE}"
        )

        assert find_non_gsm7_characters(message) == [
            LEFT_DOUBLE_QUOTE,
            RIGHT_DOUBLE_QUOTE,
            EM_DASH,
            THUMBS_UP,
        ]

    def test_reports_a_lone_surrogate(self) -> None:
        assert find_non_gsm7_characters(LONE_SURROGATE) == [LONE_SURROGATE]


class TestSanitizeForGsm7:
    def test_returns_gsm7_safe_input_unchanged(self) -> None:
        assert sanitize_for_gsm7("") == ""
        assert sanitize_for_gsm7("Your code is 1234.") == "Your code is 1234."
        assert sanitize_for_gsm7("café über señor") == "café über señor"

    def test_straightens_curly_quotes(self) -> None:
        message = (
            f"{LEFT_SINGLE_QUOTE}a{RIGHT_SINGLE_QUOTE} {LEFT_DOUBLE_QUOTE}b{RIGHT_DOUBLE_QUOTE}"
        )

        assert sanitize_for_gsm7(message) == "'a' \"b\""

    def test_replaces_dashes_and_the_ellipsis(self) -> None:
        assert sanitize_for_gsm7(f"a{EN_DASH}b{EM_DASH}c") == "a-b-c"
        assert sanitize_for_gsm7(f"wait{ELLIPSIS}") == "wait..."

    def test_normalizes_exotic_spaces_and_drops_invisible_characters(self) -> None:
        assert sanitize_for_gsm7(f"a{NON_BREAKING_SPACE}b{THIN_SPACE}c") == "a b c"
        assert sanitize_for_gsm7(f"a{ZERO_WIDTH_SPACE}{BYTE_ORDER_MARK}{SOFT_HYPHEN}b") == "ab"

    def test_rescues_a_message_from_ucs2(self) -> None:
        message = f"{LEFT_DOUBLE_QUOTE}Hi{RIGHT_DOUBLE_QUOTE}{ELLIPSIS}"

        assert count_sms_segments(message).encoding == "ucs-2"
        assert count_sms_segments(sanitize_for_gsm7(message)).encoding == "gsm-7"

    def test_preserves_zero_width_joiners_so_emoji_sequences_survive(self) -> None:
        family = f"👨{ZERO_WIDTH_JOINER}👩{ZERO_WIDTH_JOINER}👧"

        assert sanitize_for_gsm7(family) == family

    def test_leaves_unmapped_characters_alone_by_default(self) -> None:
        assert sanitize_for_gsm7(f"na{I_DIAERESIS}ve") == f"na{I_DIAERESIS}ve"
        assert sanitize_for_gsm7(TURKISH_S_CEDILLA) == TURKISH_S_CEDILLA

    def test_strips_accents_when_asked(self) -> None:
        assert sanitize_for_gsm7(f"na{I_DIAERESIS}ve", transliterate_accents=True) == "naive"
        assert sanitize_for_gsm7(TURKISH_S_CEDILLA, transliterate_accents=True) == "s"
        assert sanitize_for_gsm7("Łódź", transliterate_accents=True) == "Lodz"
        assert sanitize_for_gsm7("Œuvre", transliterate_accents=True) == "OEuvre"

    def test_keeps_letters_gsm7_already_carries_even_when_transliterating(self) -> None:
        assert sanitize_for_gsm7(f"caf{E_ACUTE}", transliterate_accents=True) == f"caf{E_ACUTE}"
        assert sanitize_for_gsm7("über señor à", transliterate_accents=True) == "über señor à"

    def test_does_not_touch_non_latin_scripts_or_emoji_when_transliterating(self) -> None:
        assert sanitize_for_gsm7("こんにちは", transliterate_accents=True) == "こんにちは"
        assert sanitize_for_gsm7("مرحبا", transliterate_accents=True) == "مرحبا"
        assert sanitize_for_gsm7(THUMBS_UP, transliterate_accents=True) == THUMBS_UP

    def test_is_idempotent(self) -> None:
        message = (
            f"{LEFT_DOUBLE_QUOTE}a{RIGHT_DOUBLE_QUOTE}{EM_DASH}b{ELLIPSIS}{NON_BREAKING_SPACE}c"
        )
        once = sanitize_for_gsm7(message)

        assert sanitize_for_gsm7(once) == once


class TestIsValidE164:
    def test_accepts_well_formed_numbers(self) -> None:
        assert is_valid_e164("+12025550123")
        assert is_valid_e164("+12")
        assert is_valid_e164("+123456789012345")

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "+",
            "+1",
            "+02025550123",
            "12025550123",
            "+1202 555 0123",
            "+1234567890123456",
            "0012025550123",
            " +12 ",
            # Python's $ would match before a trailing newline; fullmatch must not.
            "+12025550123\n",
            # Non-ASCII digits are not E.164 digits.
            "+\u0661\u0662\u0663",
        ],
    )
    def test_rejects_anything_else(self, value: str) -> None:
        assert not is_valid_e164(value)


class TestNormalizePhoneNumber:
    def test_strips_formatting_from_an_international_number(self) -> None:
        assert normalize_phone_number("+1 202-555-0123") == "+12025550123"
        assert normalize_phone_number("+1 (202) 555.0123") == "+12025550123"
        assert normalize_phone_number("  +12025550123  ") == "+12025550123"

    def test_converts_a_leading_00_to_a_plus(self) -> None:
        assert normalize_phone_number("0012025550123") == "+12025550123"
        assert normalize_phone_number("00 1 202 555 0123") == "+12025550123"

    def test_applies_a_default_country_code_to_national_input(self) -> None:
        assert normalize_phone_number("(202) 555-0123", default_country_code="1") == "+12025550123"
        assert normalize_phone_number("2025550123", default_country_code="+1") == "+12025550123"

    def test_drops_a_single_trunk_zero(self) -> None:
        assert normalize_phone_number("020 7946 0123", default_country_code="44") == (
            "+442079460123"
        )

    def test_returns_none_for_national_input_without_a_country_code(self) -> None:
        assert normalize_phone_number("2025550123") is None
        assert normalize_phone_number("020 7946 0123") is None

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "not a number",
            "+1 202 555 0123 ext 22",
            "+1+2025550123",
            "+02025550123",
            "+1202555012345678",
            "+\u0661\u0662\u0663\u0664",
        ],
    )
    def test_returns_none_for_input_it_cannot_normalize(self, value: str) -> None:
        assert normalize_phone_number(value) is None

    def test_raises_on_an_unusable_default_country_code(self) -> None:
        with pytest.raises(ValueError):
            normalize_phone_number("2025550123", default_country_code="0")
        with pytest.raises(ValueError):
            normalize_phone_number("2025550123", default_country_code="abc")


class TestJavaScriptParity:
    def test_trims_a_byte_order_mark_like_javascript(self) -> None:
        assert normalize_phone_number(BYTE_ORDER_MARK + "+12025550123") == "+12025550123"

    def test_does_not_treat_control_separators_as_formatting(self) -> None:
        # JavaScript's whitespace set excludes the information separators.
        assert normalize_phone_number("+1" + chr(0x1C) + "2025550123") is None

    def test_accepts_an_int_country_code(self) -> None:
        assert normalize_phone_number("2025550123", default_country_code=1) == "+12025550123"
