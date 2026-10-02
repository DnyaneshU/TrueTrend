import typing

import pytest

from app import extract
from app.extract import name_conflict

# Names as Indian lab reports print them; each must be accepted for its code.
MATCHING = [
    ("HBA1C", "Glycosylated Haemoglobin (HbA1c)"), ("HBA1C", "HbA1c"), ("HBA1C", "Glycated Hemoglobin"),
    ("HBA1C", "Hb A1c"),
    ("GLU_F", "Fasting Blood Sugar"), ("GLU_F", "Glucose Fasting (Plasma)"), ("GLU_F", "FBS"),
    ("GLU_F", "Blood Glucose (F)"), ("GLU_F", "Plasma Glucose - F"),
    ("GLU_PP", "Post Prandial Blood Sugar"), ("GLU_PP", "PPBS"), ("GLU_PP", "Glucose PP"),
    ("GLU_PP", "Glucose, 2 Hours Post Meal"), ("GLU_PP", "Plasma Glucose 2 hr"),
    ("TSH", "TSH (Ultrasensitive)"), ("TSH", "Thyroid Stimulating Hormone"),
    ("FT4", "Free T4"), ("FT4", "FT4"), ("FT4", "Free Thyroxine"),
    ("CHOL", "Total Cholesterol"), ("CHOL", "Cholesterol, Total"), ("CHOL", "S. Cholesterol"),
    ("LDL", "LDL Cholesterol (Direct)"), ("LDL", "LDL-C"), ("LDL", "LDL Cholesterol - Calculated"),
    ("HDL", "HDL Cholesterol"), ("HDL", "Cholesterol - HDL"),
    ("TG", "Triglycerides"), ("TG", "Serum Triglyceride"),
    ("CREAT", "Serum Creatinine"), ("CREAT", "Creatinine"),
    ("HB", "Haemoglobin"), ("HB", "Hemoglobin (Hb)"), ("HB", "HGB"),
    ("VITD", "25-Hydroxy Vitamin D"), ("VITD", "Vitamin D Total (25-OH)"), ("VITD", "Vit. D3"),
    ("B12", "Vitamin B12"), ("B12", "Cyanocobalamin"), ("B12", "Vit B 12"),
    ("URIC", "Uric Acid"), ("URIC", "Serum Uric Acid"),
    ("UREA", "Urea"), ("UREA", "Blood Urea"), ("UREA", "Serum Urea"),
]

# A different test given an MVP code. The first two are what Gemma vision really did.
CONFLICTING = [
    ("GLU_F", "Estimated Average Glucose"), ("FT4", "Total T4"),
    ("GLU_F", "Random Blood Sugar"), ("GLU_F", "Post Prandial Blood Sugar"), ("GLU_F", "Mean Blood Glucose"),
    ("GLU_F", "Glucose, Urine (Fasting)"),
    ("GLU_PP", "Fasting Blood Sugar"), ("GLU_PP", "Estimated Average Glucose"),
    ("FT4", "T4"), ("FT4", "Free T3"),
    ("TSH", "T3, T4, TSH"),
    ("CHOL", "HDL Cholesterol"), ("CHOL", "Non-HDL Cholesterol"), ("CHOL", "Total Cholesterol / HDL Ratio"),
    ("CHOL", "VLDL Cholesterol"),
    ("LDL", "VLDL Cholesterol"), ("LDL", "LDL/HDL Ratio"),
    ("HDL", "Non-HDL Cholesterol"), ("HDL", "LDL Cholesterol"),
    ("TG", "TG/HDL Ratio"),
    ("CREAT", "Creatinine Clearance"), ("CREAT", "Creatine Kinase (CK)"), ("CREAT", "Urine Creatinine"),
    ("CREAT", "BUN/Creatinine Ratio"),
    ("HB", "Glycosylated Haemoglobin (HbA1c)"), ("HBA1C", "Haemoglobin"),
    ("VITD", "1,25-Dihydroxy Vitamin D"),
    ("UREA", "Blood Urea Nitrogen"), ("UREA", "BUN"),
    ("URIC", "Uric Acid, Urine"),
]


@pytest.mark.parametrize("code, name", MATCHING)
def test_matching_names_are_accepted(code, name):
    assert name_conflict(code, name) is None


@pytest.mark.parametrize("code, name", CONFLICTING)
def test_different_tests_are_caught(code, name):
    assert name_conflict(code, name) is not None


def test_conflict_reason_names_the_printed_test():
    assert name_conflict("GLU_F", "Estimated Average Glucose") == (
        "'Estimated Average Glucose' is not GLU_F"
    )


def test_every_test_code_has_a_name_rule():
    assert set(extract.NAME_RULES) == set(typing.get_args(extract.TestCode))
