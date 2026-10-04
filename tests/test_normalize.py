"""Normalisation: printed values, units and reference ranges -> numbers in standard units."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from truetrend.lab_tests import CATALOG
from truetrend.models import Result
from truetrend.normalize import normalize, parse_range, parse_value

numbers = st.decimals(min_value=0, max_value=99_999, places=3, allow_nan=False, allow_infinity=False)
spaces = st.sampled_from(["", " ", "  "])


def result(code="HBA1C", value="7.2", unit="%", ref="4.0 - 5.6", name="HbA1c"):
    return Result(page=1, test_code=code, raw_name=name, value_text=value, unit=unit, ref_text=ref)


def normalize_printed(printed):
    return normalize(printed, CATALOG.test(printed.test_code))


# ---------------------------------------------------------------- values


@given(
    number=numbers,
    decimals=st.integers(0, 3),
    qualifier=st.sampled_from(["", "<", ">", "<=", ">="]),
    gap=spaces,
)
def test_a_printed_number_reads_back_exactly(number, decimals, qualifier, gap):
    printed = f"{qualifier}{gap}{number:.{decimals}f}"
    expected = round(float(f"{number:.{decimals}f}"), decimals)
    assert parse_value(printed) == ((qualifier or None), expected), printed


@pytest.mark.parametrize(
    "printed, expected",
    [
        ("1,234", (None, 1234.0)),  # thousands separator
        ("1,23,456", (None, 123456.0)),  # Indian grouping
        ("7,2", (None, 7.2)),  # decimal comma
        ("≤ 0.5", ("<=", 0.5)),
        ("7.2 %", (None, 7.2)),  # unit copied into the value
        ("12.5 (Low)", (None, 12.5)),
    ],
)
def test_number_formats(printed, expected):
    assert parse_value(printed) == expected


@pytest.mark.parametrize("printed", ["Positive", "Nil", "1-2", "", "6 .0", "Reactive (1:8)"])
def test_text_that_is_not_one_number_gives_none(printed):
    assert parse_value(printed) is None


# ---------------------------------------------------------------- reference ranges


@given(
    low=numbers,
    width=numbers,
    separator=st.sampled_from(["-", " - ", "–", " to ", "  -  "]),
    label=st.sampled_from(["", "Normal : ", "Desirable: ", "Reference range - "]),
    unit=st.sampled_from(["", " mg/dL", " %"]),
)
def test_a_printed_range_reads_back(low, width, separator, label, unit):
    high = low + width
    printed = f"{label}{low}{separator}{high}{unit}"
    assert parse_range(printed) == (float(low), float(high)), printed


@pytest.mark.parametrize(
    "printed, expected",
    [
        ("<200", (None, 200.0)),
        ("< 200.00", (None, 200.0)),
        ("Up to 5.0", (None, 5.0)),
        ("≤ 100", (None, 100.0)),
        ("> 40", (40.0, None)),
        (">=45", (45.0, None)),
        ("Normal : <150", (None, 150.0)),
        ("Optimal: <100", (None, 100.0)),
    ],
)
def test_one_sided_ranges(printed, expected):
    assert parse_range(printed) == expected


@pytest.mark.parametrize(
    "printed",
    [
        "Low: <40.0",  # a risk category, not the normal range
        "Deficiency : <10 Insufficiency : 10 - 30",
        "For Screening: Diabetes: >6.5% Pre-Diabetes: 5.7% - 6.4%",
        "6 .0 - 8.0 pH",  # mis-spaced number
        "150 - 410 x 10³/µL",  # a count unit with digits
        "Negative",
        "",
        None,
    ],
)
def test_ranges_that_are_not_one_normal_range_give_none(printed):
    assert parse_range(printed) is None


# ---------------------------------------------------------------- whole results


@pytest.mark.parametrize(
    "code, value, unit, standard",
    [
        ("GLU_F", "5.5", "mmol/L", 99.088),
        ("HBA1C", "53", "mmol/mol", 6.9995),  # 0.0915 × 53 + 2.15
        ("VITD", "150.00", "nmol/L", 60.096),
        ("CREAT", "88.4", "µmol/L", 1.0),
        ("UREA", "5", "mmol/L", 30.03),
        ("URIC", "297.4", "µmol/L", 5.0),
        ("B12", "300", "pmol/L", 406.5),
        ("FT4", "15", "pmol/L", 1.166),
        ("HB", "125", "g/L", 12.5),
        ("HB", "13.6", "gm/dL", 13.6),
        ("TSH", "0.91", "mlU/L", 0.91),  # a lab font's "mlU/L" for mIU/L
        ("TSH", "2.5", "microIU/mL", 2.5),
        ("CHOL", "5.2", "mmol/L", 201.084),
    ],
)
def test_values_are_converted_to_the_standard_unit(code, value, unit, standard):
    normalized = normalize_printed(result(code=code, value=value, unit=unit, ref=None))
    assert normalized.value_std == pytest.approx(standard, abs=0.001)
    assert normalized.unit_std == CATALOG.test(code).unit


def test_reference_range_is_converted_with_the_value():
    normalized = normalize_printed(result(code="VITD", value="150.00", unit="nmol/L", ref="75.00 - 250.00"))
    assert (normalized.ref_low, normalized.ref_high) == pytest.approx((30.048, 100.160), abs=0.001)


def test_printed_precision_is_kept():
    assert (
        normalize_printed(result(code="TSH", value="0.8199", unit="microIU/mL", ref=None)).value_std == 0.8199
    )


def test_qualifier_is_kept():
    normalized = normalize_printed(result(code="B12", value="< 148", unit="pg/mL", ref="187 - 833"))
    assert (normalized.qualifier, normalized.value, normalized.value_std) == ("<", 148.0, 148.0)


def test_unknown_unit_is_never_assumed():
    normalized = normalize_printed(result(code="HBA1C", value="7.2", unit="mg"))
    assert normalized.value == 7.2
    assert (normalized.value_std, normalized.unit_std, normalized.ref_low) == (None, None, None)
    assert normalized.notes == ["unit 'mg' is not a known unit for HBA1C"]


def test_missing_unit_is_never_assumed():
    normalized = normalize_printed(result(code="HBA1C", value="7.2", unit=None))
    assert normalized.value_std is None
    assert normalized.notes == ["no unit printed"]


def test_word_result_has_no_number():
    normalized = normalize_printed(result(code="HB", value="Not detected", unit="g/dL", ref=None))
    assert (normalized.value, normalized.value_std) == (None, None)
    assert normalized.notes == ["value 'Not detected' is not a number"]


def test_every_standard_unit_converts_one_to_one():
    for test in CATALOG.tests:
        assert test.conversion(test.unit).apply(7.25) == 7.25, test.code


@pytest.mark.parametrize("printed", ["106 - 74", "1,000 - 200"])
def test_a_range_whose_low_limit_is_above_its_high_one_is_not_a_range(printed):
    assert parse_range(printed) is None


def test_a_range_with_thousands_separators():
    assert parse_range("1,000 - 2,500") == (1000.0, 2500.0)
