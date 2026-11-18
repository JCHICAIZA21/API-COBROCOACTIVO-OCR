import pytest

from docreader.normalize import normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Juan   Pérez\n Gómez ", "Juan Pérez Gómez"),
        ("   ", None),
        ("", None),
        (None, None),
    ],
)
def test_text_collapses_whitespace(raw: str | None, expected: str | None) -> None:
    assert normalize("text", raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.023.456.789", "1023456789"),
        ("80,123,456", "80123456"),
        ("900 123 456-7", "9001234567"),
        (" 123 ", "123"),
        ("12", None),  # too short
        ("1" * 26, None),  # too long
        ("12a45", None),  # letters are not separators
        ("", None),
        (None, None),
    ],
)
def test_id_number_keeps_digits_only(raw: str | None, expected: str | None) -> None:
    assert normalize("id_number", raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("007", "7"), ("1.200", "1200"), ("12 345", "12345"), ("abc", None), ("", None)],
)
def test_integer_canonical_digits(raw: str, expected: str | None) -> None:
    assert normalize("integer", raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15/03/2024", "2024-03-15"),
        ("5-3-24", "2024-03-05"),
        ("05.03.2024", "2024-03-05"),
        ("2024-03-15", "2024-03-15"),
        ("15 de marzo de 2024", "2024-03-15"),
        ("1 de Septiembre del 2023", "2023-09-01"),
        ("15-MAR-2024", "2024-03-15"),
        ("15/mar/24", "2024-03-15"),
        ("3 ene. 2025", "2025-01-03"),
        ("10 sept 2023", "2023-09-10"),
        ("12 DICIEMBRE 2022", "2022-12-12"),
        ("  15/03/2024 ", "2024-03-15"),
        ("31/02/2024", None),  # impossible date
        ("29/02/2023", None),  # not a leap year
        ("29/02/2024", "2024-02-29"),
        ("15/13/2024", None),
        ("15 de marzi de 2024", None),
        ("2024", None),
        ("", None),
    ],
)
def test_date_day_first_to_iso(raw: str, expected: str | None) -> None:
    assert normalize("date", raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$ 1.234.567,89", "1234567.89"),
        ("1.234.567", "1234567"),
        ("1,234,567.89", "1234567.89"),
        ("$1.234.567", "1234567"),
        ("1.234", "1234"),  # single kind, 3-digit group: thousands
        ("1234,5", "1234.5"),  # single kind, not 3 digits: decimal
        ("1234.00", "1234"),  # zero fraction dropped
        ("1.234.567,00", "1234567"),
        ("1234,50", "1234.5"),
        ("0,50", "0.5"),
        ("850000", "850000"),
        ("1.23.4", None),  # repeated separator with irregular groups
        ("12,34.5", None),  # irregular thousands group
        ("1.234,56,7", None),  # decimal separator appears twice
        ("abc", None),
        ("", None),
    ],
)
def test_money_colombian_format_first(raw: str, expected: str | None) -> None:
    assert normalize("money", raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abc123", "ABC123"),
        ("ABC-123", "ABC123"),
        (" abc 12d ", "ABC12D"),  # motorcycle
        ("AB1234", None),
        ("ABCD123", None),
        ("ABC1", None),
        ("", None),
    ],
)
def test_plate_uppercase_compact(raw: str, expected: str | None) -> None:
    assert normalize("plate", raw) == expected
