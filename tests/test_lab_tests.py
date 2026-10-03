import typing

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.lab_tests import CATALOG, TestCode

# Names as Indian lab reports print them; each must be accepted for its code.
MATCHING = [
    ("HBA1C", "Glycosylated Haemoglobin (HbA1c)"),
    ("HBA1C", "HbA1c"),
    ("HBA1C", "Glycated Hemoglobin"),
    ("HBA1C", "Hb A1c"),
    ("GLU_F", "Fasting Blood Sugar"),
    ("GLU_F", "Glucose Fasting (Plasma)"),
    ("GLU_F", "FBS"),
    ("GLU_F", "Blood Glucose (F)"),
    ("GLU_F", "Plasma Glucose - F"),
    ("GLU_PP", "Post Prandial Blood Sugar"),
    ("GLU_PP", "PPBS"),
    ("GLU_PP", "Glucose PP"),
    ("GLU_PP", "Glucose, 2 Hours Post Meal"),
    ("GLU_PP", "Plasma Glucose 2 hr"),
    ("TSH", "TSH (Ultrasensitive)"),
    ("TSH", "Thyroid Stimulating Hormone"),
    ("TSH", "T.S.H."),
    ("LDL", "Low Density Lipoprotein Cholesterol"),
    ("HDL", "High Density Lipoprotein (HDL)"),
    ("FT4", "Free T4"),
    ("FT4", "FT4"),
    ("FT4", "Free Thyroxine"),
    ("CHOL", "Total Cholesterol"),
    ("CHOL", "Cholesterol, Total"),
    ("CHOL", "S. Cholesterol"),
    ("LDL", "LDL Cholesterol (Direct)"),
    ("LDL", "LDL-C"),
    ("LDL", "LDL Cholesterol - Calculated"),
    ("HDL", "HDL Cholesterol"),
    ("HDL", "Cholesterol - HDL"),
    ("TG", "Triglycerides"),
    ("TG", "Serum Triglyceride"),
    ("CREAT", "Serum Creatinine"),
    ("CREAT", "Creatinine"),
    ("HB", "Haemoglobin"),
    ("HB", "Hemoglobin (Hb)"),
    ("HB", "HGB"),
    ("VITD", "25-Hydroxy Vitamin D"),
    ("VITD", "Vitamin D Total (25-OH)"),
    ("VITD", "Vit. D3"),
    ("VITD", "25(OH)D"),
    ("VITD", "25-OH Cholecalciferol"),
    ("B12", "Vitamin B12"),
    ("B12", "Cyanocobalamin"),
    ("B12", "Vit B 12"),
    ("B12", "Vitamin B-12"),
    ("URIC", "Uric Acid"),
    ("URIC", "Serum Uric Acid"),
    ("UREA", "Urea"),
    ("UREA", "Blood Urea"),
    ("UREA", "Serum Urea"),
]

# A different test given an MVP code. The first two are what Gemma vision really did.
CONFLICTING = [
    ("GLU_F", "Estimated Average Glucose"),
    ("FT4", "Total T4"),
    ("GLU_F", "Random Blood Sugar"),
    ("GLU_F", "Post Prandial Blood Sugar"),
    ("GLU_F", "Mean Blood Glucose"),
    ("GLU_F", "Glucose, Urine (Fasting)"),
    ("GLU_PP", "Fasting Blood Sugar"),
    ("GLU_PP", "Estimated Average Glucose"),
    ("FT4", "T4"),
    ("FT4", "Free T3"),
    ("TSH", "T3, T4, TSH"),
    ("CHOL", "HDL Cholesterol"),
    ("CHOL", "Non-HDL Cholesterol"),
    ("CHOL", "Total Cholesterol / HDL Ratio"),
    ("CHOL", "VLDL Cholesterol"),
    ("LDL", "VLDL Cholesterol"),
    ("LDL", "LDL/HDL Ratio"),
    ("LDL", "Very Low Density Lipoprotein"),
    ("LDL", "High Density Lipoprotein"),
    ("HDL", "Low Density Lipoprotein"),
    ("CHOL", "Low Density Lipoprotein Cholesterol"),
    ("HDL", "Non-HDL Cholesterol"),
    ("HDL", "LDL Cholesterol"),
    ("TG", "TG/HDL Ratio"),
    ("CREAT", "Creatinine Clearance"),
    ("CREAT", "Creatine Kinase (CK)"),
    ("CREAT", "Urine Creatinine"),
    ("CREAT", "BUN/Creatinine Ratio"),
    ("HB", "Glycosylated Haemoglobin (HbA1c)"),
    ("HBA1C", "Haemoglobin"),
    # every CBC prints these next to haemoglobin
    ("HB", "Mean Corpuscular Haemoglobin (MCH)"),
    ("HB", "Mean Corpuscular Hemoglobin Concentration"),
    ("HB", "MCHC"),
    ("HB", "Haemoglobin, Urine"),
    # fractions from haemoglobin electrophoresis (seen on a real report: "Hb A 84.4 %")
    ("HB", "Hb A"),
    ("HB", "HbA2"),
    ("HB", "Hb A2"),
    ("HB", "Foetal Hb"),
    ("HB", "Hb F"),
    ("HB", "HB Electrophoresis By HPLC"),
    ("VITD", "1,25-Dihydroxy Vitamin D"),
    ("VITD", "17-Hydroxy Progesterone"),
    ("VITD", "Vitamin B12"),
    ("CREAT", "Creatine"),
    ("UREA", "Blood Urea Nitrogen"),
    ("UREA", "BUN"),
    ("URIC", "Uric Acid, Urine"),
]


@pytest.mark.parametrize("code, name", MATCHING)
def test_matching_names_are_accepted(code, name):
    assert CATALOG.test(code).conflict(name) is None


@pytest.mark.parametrize("code, name", CONFLICTING)
def test_different_tests_are_caught(code, name):
    assert CATALOG.test(code).conflict(name) is not None


def test_conflict_reason_names_the_printed_test():
    assert CATALOG.test("GLU_F").conflict("Estimated Average Glucose") == (
        "'Estimated Average Glucose' is not GLU_F"
    )


@given(data=st.data())
def test_every_catalog_name_matches_in_any_capitalisation(data):
    test = data.draw(st.sampled_from(CATALOG.tests))
    phrase = data.draw(st.sampled_from(test.names))
    printed = "".join(data.draw(st.sampled_from([c.lower(), c.upper()])) for c in phrase)
    assert test.conflict(printed) is None, (test.code, printed)


def test_catalog_has_the_15_mvp_tests_once_each():
    codes = [test.code for test in CATALOG.tests]
    assert len(codes) == len(set(codes)) == 15
    assert set(typing.get_args(TestCode)) == set(codes)


def test_every_variation_constant_cites_its_source():
    for test in CATALOG.tests:
        if test.variation is None:
            continue
        assert "biologicalvariation.eu" in test.variation.source, test.code  # CVI from the EFLM database
        if test.variation.between_lab_cv is not None:
            assert "Between-lab CV:" in test.variation.source, test.code
