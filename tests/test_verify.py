"""Verification: each result checked against its PDF, its believable limits and the report's other results."""

import pymupdf
import pytest
from factories import table
from hypothesis import given
from hypothesis import strategies as st

from arogya_vahi.lab_tests import CATALOG
from arogya_vahi.models import Result
from arogya_vahi.text import QUALIFIERS, printed_number
from arogya_vahi.verify import verify_results

DEVANAGARI = str.maketrans("0123456789", "०१२३४५६७८९")


def result(code, value, unit, name="as Gemma named it", page=1, ref=None):
    return Result(page=page, test_code=code, raw_name=name, value_text=value, unit=unit, ref_text=ref)


@pytest.fixture
def verify(make_pdf):
    """verify_results against a synthetic PDF made of the given pages."""

    def _verify(pages, *results):
        with pymupdf.open(make_pdf(pages)) as doc:
            return verify_results(results, doc)

    return _verify


# ---------------------------------------------------------------- the value is printed on its page


def test_a_value_printed_in_its_tests_row_is_verified(verify, report_page):
    hba1c, hb = verify(
        [report_page],
        result("HBA1C", "7.2", "%", name="HbA1c (Glycosylated Haemoglobin)"),  # Gemma's name needn't match
        result("HB", "12.1", "g/dL"),
    )
    assert (hba1c.status, hba1c.notes, hb.status) == ("verified", [], "verified")
    x0, y0, x1, y1 = hba1c.bbox  # where "7.2" is printed, for highlighting it later
    assert 255 < x0 < x1 < 280 and y0 < 150 < y1


def test_a_value_from_another_tests_row_needs_a_check(verify, report_page):
    # 160 is printed, but in the Estimated Average Glucose row
    (glucose,) = verify([report_page], result("GLU_F", "160", "mg/dL"))
    assert glucose.status == "needs_check" and glucose.bbox is None
    assert glucose.notes == ["160 is not printed on page 1 in a row naming Fasting glucose"]


def test_a_value_not_printed_anywhere_needs_a_check(verify, report_page):
    (hb,) = verify([report_page], result("HB", "12.7", "g/dL"))
    assert hb.notes == ["12.7 is not printed on page 1 in a row naming Haemoglobin"]


def test_a_value_on_the_wrong_page_needs_a_check(verify, report_page):
    (hb,) = verify([report_page, table(("Urea", "30", "mg/dL"))], result("HB", "12.1", "g/dL", page=2))
    assert hb.status == "needs_check"


def test_a_row_label_stops_at_the_previous_number(verify):
    # a two-column row: 250 belongs to Platelets, and 13.0 is part of the reference range
    page = [(50, 100, "Hemoglobin"), (150, 100, "14.5"), (190, 100, "g/dL"), (240, 100, "13.0 - 16.5"),
            (340, 100, "Platelets"), (420, 100, "250")]  # fmt: skip
    right, platelets, range_low = verify(
        [page], result("HB", "14.5", "g/dL"), result("HB", "250", "g/dL"), result("HB", "13.0", "g/dL")
    )
    assert [r.status for r in (right, platelets, range_low)] == ["verified", "needs_check", "needs_check"]


def test_a_number_inside_a_tests_name_is_part_of_its_label(verify):
    # as Dr Lal prints it: "Vitamin D, 25 Hydroxy | 150.00 | nmol/L | 75.00 - 250.00"
    page = [(50, 100, "Vitamin D, 25 Hydroxy"), (200, 100, "150.00"), (260, 100, "nmol/L")]
    (vitd,) = verify([page], result("VITD", "150.00", "nmol/L"))
    assert vitd.status == "verified"


def test_a_number_in_a_sentence_is_not_a_result(verify):
    page = [(50, 100, "Vitamin D deficiency is defined by most experts as a level of less than 20")]
    (vitd,) = verify([page], result("VITD", "20", "ng/mL"))
    assert vitd.status == "needs_check"


def test_a_flag_and_qualifier_printed_beside_the_value_are_fine(verify):
    page = [
        (50, 100, "Vitamin B12"),
        (200, 100, "L"),
        (240, 100, "<"),
        (250, 100, "148"),
        (300, 100, "pg/mL"),
    ]
    (b12,) = verify([page], result("B12", "< 148", "pg/mL"))
    assert b12.status == "verified"


def test_a_value_on_a_scanned_page_needs_a_check(verify):
    (hb,) = verify([[]], result("HB", "12.1", "g/dL"))
    assert hb.status == "needs_check"
    assert "page 1 is scanned" in hb.notes[0]


def test_without_the_original_pdf_nothing_is_verified():
    (hb,) = verify_results([result("HB", "12.1", "g/dL")], doc=None)
    assert hb.status == "needs_check"
    assert "could not be opened" in hb.notes[0]


def test_a_page_number_outside_the_pdf_needs_a_check(verify, report_page):
    (hb,) = verify([report_page], result("HB", "12.1", "g/dL", page=5))
    assert hb.notes == ["page 5 is not in the PDF"]


@given(
    number=st.decimals(min_value=0, max_value=99_999, places=3, allow_nan=False, allow_infinity=False),
    decimals=st.integers(0, 3),
    qualifier=st.sampled_from(["", "<", ">", "<=", "≥"]),
    suffix=st.sampled_from(["", "%", "*"]),
    devanagari=st.booleans(),
)
def test_a_printed_value_cell_reads_back(number, decimals, qualifier, suffix, devanagari):
    digits = f"{number:.{decimals}f}"
    printed = qualifier + (digits.translate(DEVANAGARI) if devanagari else digits) + suffix
    assert printed_number(printed) == (QUALIFIERS.get(qualifier), float(digits)), printed


@pytest.mark.parametrize("printed", ["4.0-5.6", "B12", "H", "25(OH)", "1.2.3", "mg/dL", "", "<"])
def test_a_cell_that_is_not_one_value_reads_as_none(printed):
    assert printed_number(printed) is None


def test_a_normal_range_printed_in_the_values_row_is_verified(verify, report_page):
    (hba1c,) = verify([report_page], Result(page=1, test_code="HBA1C", raw_name="HbA1c", value_text="7.2",
                                            unit="%", ref_text="4.0 - 5.6"))  # fmt: skip
    assert hba1c.ref_verified and (hba1c.ref_low, hba1c.ref_high) == (4.0, 5.6)


@pytest.mark.parametrize("ref_text", ["4.0 - 56", "70 - 100", "Desirable: <5.6 ; Diabetes: >6.5", None])
def test_a_normal_range_not_printed_in_the_values_row_is_not_verified(verify, report_page, ref_text):
    # a misread limit, another row's range, a list of categories, no range
    (hba1c,) = verify([report_page], Result(page=1, test_code="HBA1C", raw_name="HbA1c", value_text="7.2",
                                            unit="%", ref_text=ref_text))  # fmt: skip
    assert hba1c.status == "verified" and not hba1c.ref_verified


def test_a_one_sided_range_is_verified_by_its_one_limit(verify):
    page = [(50, 100, "Total Cholesterol"), (200, 100, "189"), (260, 100, "mg/dL"), (320, 100, "< 200")]
    (chol,) = verify([page], Result(page=1, test_code="CHOL", raw_name="Cholesterol", value_text="189",
                                    unit="mg/dL", ref_text="Desirable : <200"))  # fmt: skip
    assert chol.ref_verified and chol.ref_high == 200.0


def test_a_value_printed_as_a_limit_is_never_verified_as_exact(verify):
    # the PDF prints "< 148"; Gemma dropped the "<"
    page = [(50, 100, "Vitamin B12"), (200, 100, "<"), (210, 100, "148"), (260, 100, "pg/mL")]
    exact, limit = verify([page], result("B12", "148", "pg/mL"), result("B12", "< 148", "pg/mL"))
    assert (exact.status, limit.status) == ("needs_check", "verified")


def test_a_qualifier_gemma_added_is_not_verified(verify, report_page):
    (hba1c,) = verify([report_page], result("HBA1C", "< 7.2", "%"))
    assert hba1c.status == "needs_check"


def test_a_unit_not_printed_in_the_row_needs_a_check(verify):
    # 30.0 ng/mL read as nmol/L would be saved as 12.0 ng/mL, still within believable limits
    page = table(("25-OH Vitamin D", "30.0", "ng/mL"))
    right, wrong = verify([page], result("VITD", "30.0", "ng/mL"), result("VITD", "30.0", "nmol/L"))
    assert (right.status, wrong.status) == ("verified", "needs_check")
    assert wrong.notes == ["unit 'nmol/L' is not printed in the value's row"]


def test_a_unit_printed_with_the_value_counts(verify):
    (hba1c,) = verify([[(50, 100, "HbA1c"), (260, 100, "7.2%")]], result("HBA1C", "7.2", "%"))
    assert hba1c.status == "verified"


def test_a_number_in_a_sentence_is_not_a_value_even_in_a_short_row(verify):
    page = [(50, 100, "Interpretation: HbA1c >= 6.5 Diabetes")]
    (hba1c,) = verify([page], result("HBA1C", ">= 6.5", "%"))
    assert hba1c.status == "needs_check"


def test_the_value_itself_does_not_verify_its_range(verify):
    # a hallucinated range "4.0 - 6.8" next to the value 6.8: only 6.8 is printed in the row
    (hba1c,) = verify([table(("HbA1c", "6.8", "%"))], result("HBA1C", "6.8", "%", ref="6.8 - 9.0"))
    assert hba1c.status == "verified" and not hba1c.ref_verified


def test_each_page_is_read_once_for_all_its_results(verify, report_page, monkeypatch):
    from arogya_vahi import verify as module

    calls = []
    original = module.page_rows
    monkeypatch.setattr(module, "page_rows", lambda page: calls.append(page.number) or original(page))
    verify([report_page], result("HBA1C", "7.2", "%"), result("HB", "12.1", "g/dL"))
    assert calls == [0]


# ---------------------------------------------------------------- believable values and units


def test_a_value_outside_the_believable_limits_needs_a_check(verify):
    # 6.9 % printed as 69 %: a dropped decimal point
    (hba1c,) = verify([table(("HbA1c", "69", "%"))], result("HBA1C", "69", "%"))
    assert hba1c.status == "needs_check"
    assert hba1c.notes == ["69 % is outside the believable limits for HbA1c (3–20 %); probably misread"]


def test_believable_limits_apply_in_the_standard_unit(verify):
    # 53 mmol/mol is a normal IFCC HbA1c: 6.99 % once converted
    (hba1c,) = verify([table(("HbA1c", "53", "mmol/mol"))], result("HBA1C", "53", "mmol/mol"))
    assert hba1c.status == "verified"


def test_a_normalisation_problem_keeps_the_result_unverified(verify):
    (hba1c,) = verify([table(("HbA1c", "7.2", "mg"))], result("HBA1C", "7.2", "mg"))
    assert (hba1c.status, hba1c.notes) == ("needs_check", ["unit 'mg' is not a known unit for HBA1C"])


# ---------------------------------------------------------------- results checked against each other


LIPIDS = (
    ("CHOL", "Total Cholesterol"),
    ("HDL", "HDL Cholesterol"),
    ("TG", "Triglycerides"),
    ("LDL", "LDL Cholesterol"),
)


def lipids(verify, total, hdl, tg, ldl):
    values = dict(zip(("CHOL", "HDL", "TG", "LDL"), (total, hdl, tg, ldl), strict=True))
    page = table(*[(name, values[code], "mg/dL") for code, name in LIPIDS])
    return dict(
        zip(values, verify([page], *[result(code, values[code], "mg/dL") for code, _ in LIPIDS]), strict=True)
    )


def test_consistent_lipids_are_all_verified_without_notes(verify):
    checked = lipids(verify, total="189", hdl="60", tg="168", ldl="100.39")
    assert {code: (r.status, r.notes) for code, r in checked.items()} == dict.fromkeys(
        checked, ("verified", [])
    )


def test_ldl_more_than_total_cholesterol_needs_a_check(verify):
    checked = lipids(verify, total="150", hdl="40", tg="100", ldl="180")
    problem = (
        "LDL cholesterol 180 mg/dL is more than Total cholesterol 150 mg/dL, which includes it; "
        "one of them is probably misread"
    )
    assert checked["LDL"].status == checked["CHOL"].status == "needs_check"
    assert problem in checked["LDL"].notes and problem in checked["CHOL"].notes
    assert checked["HDL"].status == checked["TG"].status == "verified"


def test_ldl_far_from_the_friedewald_estimate_gets_a_note_only(verify):
    # estimate: 200 - 50 - 100 / 5 = 130
    ldl = lipids(verify, total="200", hdl="50", tg="100", ldl="60")["LDL"]
    assert ldl.status == "verified"
    assert ldl.notes == [
        "LDL cholesterol 60 mg/dL differs from the Friedewald estimate of 130 mg/dL "
        "(total cholesterol - HDL - triglycerides / 5)"
    ]


def test_friedewald_is_skipped_when_triglycerides_are_high(verify):
    assert lipids(verify, total="300", hdl="40", tg="450", ldl="100")["LDL"].notes == []


def test_hba1c_far_from_fasting_glucose_gets_a_note_only(verify):
    page = table(("HbA1c", "10.0", "%"), ("Fasting Blood Sugar", "90", "mg/dL"))
    hba1c, fasting = verify([page], result("HBA1C", "10.0", "%"), result("GLU_F", "90", "mg/dL"))
    assert hba1c.status == fasting.status == "verified"
    assert hba1c.notes == [
        "HbA1c 10 % corresponds to an average glucose of about 240 mg/dL (ADAG), while fasting "
        "glucose on this report is 90 mg/dL; worth asking the doctor about"
    ]


def test_hba1c_close_to_fasting_glucose_gets_no_note(verify):
    page = table(("HbA1c", "7.10", "%"), ("Fasting Blood Sugar", "141.0", "mg/dL"))
    hba1c, _ = verify([page], result("HBA1C", "7.10", "%"), result("GLU_F", "141.0", "mg/dL"))
    assert hba1c.notes == []


def test_values_below_a_detection_limit_are_left_out_of_cross_checks(verify):
    # "< 50" is not a number to compare with
    checked = lipids(verify, total="40", hdl="30", tg="100", ldl="< 50")
    assert checked["CHOL"].status == "verified"


def test_cross_checks_use_the_catalogs_units():
    # the formulas in arogya_vahi.verify are written in mg/dL and %
    assert {code: CATALOG.test(code).unit for code in ("CHOL", "HDL", "TG", "LDL", "GLU_F")} == dict.fromkeys(
        ("CHOL", "HDL", "TG", "LDL", "GLU_F"), "mg/dL"
    )
    assert CATALOG.test("HBA1C").unit == "%"
