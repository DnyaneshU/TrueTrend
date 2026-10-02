"""The 15 MVP lab tests: how Gemma is told about each, and how code checks its answers.

Code checks Gemma's test code against the printed test name because Gemma (vision
mode especially) sometimes files a look-alike under an MVP code: seen "Estimated
Average Glucose" as GLU_F and "Total T4" as FT4.
"""
import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LabTest:
    code: str
    described_as: str          # what the prompt calls it
    name_must: str             # the printed name must match this (regex, any case)
    name_must_not: str | None  # ...and must not match this


LAB_TESTS = (
    LabTest("HBA1C", "HbA1c, Glycated / Glycosylated Haemoglobin",
            r"a1c|glyc", None),
    LabTest("GLU_F", "Fasting blood or plasma glucose, FBS, Fasting Blood Sugar",
            r"fasting|\bf(bs|bg|pg)?\b",
            r"estimated|average|mean|random|\brbs\b|post|prandial|\bpp|urine"),
    LabTest("GLU_PP", "Post-prandial glucose, PPBS, PP blood sugar, 2-hour glucose",
            r"post|prandial|\bpp|\b(2|two)[\s-]*h(ou)?rs?\b",
            r"estimated|average|mean|random|\brbs\b|fasting|urine"),
    LabTest("TSH", "TSH, Thyroid Stimulating Hormone (including ultrasensitive TSH)",
            r"\bt\.?s\.?h\b|thyroid stimulating", r"\bf?t[34]\b"),
    LabTest("FT4", "Free T4, FT4, Free Thyroxine",
            r"free|\bft4\b", r"total|\bf?t3\b|\btsh\b"),
    LabTest("CHOL", "Total Cholesterol",
            r"cholesterol|\bchol\b|\btc\b", r"hdl|ldl|density|\bnon|ratio"),
    LabTest("LDL", "LDL Cholesterol (direct or calculated)",
            r"\bldl|low[\s-]*density", r"vldl|very|hdl|high[\s-]*density|\bnon|ratio"),
    LabTest("HDL", "HDL Cholesterol",
            r"\bhdl|high[\s-]*density", r"ldl|low[\s-]*density|\bnon|ratio"),
    LabTest("TG", "Triglycerides",
            r"triglyceride|\btg\b|\btrig\b", r"ratio"),
    LabTest("CREAT", "Serum Creatinine",
            r"creat", r"urine|clearance|ratio|egfr|\bcreatine\b|kinase|\bc?pk\b|\bck\b"),
    LabTest("HB", "Haemoglobin / Hemoglobin / Hb",
            r"h(a)?emoglobin|\bhb\b|\bhgb\b",
            r"a1c|glyc|corpuscular|\bmchc?\b|urine|\bhb\s*(a\d?|f|s)\b|f(o)?etal|electrophoresis|hplc"),
    LabTest("VITD", "25-Hydroxy (25-OH) Vitamin D, Vitamin D Total",
            r"vit(amin)?\.?\s*d[23]?\b|cholecalciferol|25[\s(-]*(oh|hydroxy)", r"1[,\s]*25|dihydroxy"),
    LabTest("B12", "Vitamin B12, Cyanocobalamin",
            r"b\s*12|cobalamin", None),
    LabTest("URIC", "Uric Acid",
            r"uric", r"urine"),
    LabTest("UREA", "Urea, Blood Urea, Serum Urea",
            r"urea", r"nitrogen|\bbun\b|urine"),
)

TestCode = Literal[tuple(test.code for test in LAB_TESTS)]

_NAME_PATTERNS = {
    test.code: (re.compile(test.name_must, re.IGNORECASE),
                test.name_must_not and re.compile(test.name_must_not, re.IGNORECASE))
    for test in LAB_TESTS
}


def name_conflict(test_code: str, raw_name: str) -> str | None:
    """Why the printed test name can't be `test_code`, or None if it fits."""
    must, must_not = _NAME_PATTERNS[test_code]
    if must.search(raw_name) and not (must_not and must_not.search(raw_name)):
        return None
    return f"'{raw_name}' is not {test_code}"
