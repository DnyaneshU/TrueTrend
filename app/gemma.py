"""One call to local Gemma (through Ollama) per page, with the reply held to a JSON schema."""
import httpx
import ollama
from pydantic import BaseModel

from app.errors import ExtractError
from app.lab_tests import LAB_TESTS, TestCode
from app.pages import PageInput

DEFAULT_MODEL = "gemma4:e4b"
OLLAMA_OPTIONS = {"temperature": 0, "num_ctx": 8192, "num_predict": 2048}
RETRY_TEMPERATURE = 0.3   # a second temperature-0 call would usually repeat the same broken reply


class ExtractedResult(BaseModel):
    test_code: TestCode
    raw_name: str
    value_text: str
    unit: str | None
    ref_text: str | None


class PageExtraction(BaseModel):
    patient_name: str | None
    age: str | None
    sex: str | None
    lab_name: str | None
    sample_date: str | None
    report_date: str | None
    results: list[ExtractedResult]


PAGE_SCHEMA = PageExtraction.model_json_schema()  # Ollama constrains Gemma's reply to this

_TEST_LIST = "\n".join(f"  {test.code:<6} = {test.described_as}" for test in LAB_TESTS)

SYSTEM_PROMPT = f"""You read one page of an Indian medical laboratory report and copy data exactly as printed.
Never calculate, convert, round, translate or guess. If something is not printed on this page, use null.

Fields:
- patient_name, age, sex, lab_name: exactly as printed on this page, or null.
- sample_date: the sample COLLECTION date and time as printed (labels such as "Collected", "Sample Collected On", "Collection Date", "Drawn"). Not the registration date and not the report date.
- report_date: the date the report was released, as printed (labels such as "Reported", "Report Date", "Reported On").
- results: one entry for each result of ONLY these tests:
{_TEST_LIST}
  Do NOT include: Total T4 or T4, T3, LDL/HDL or other ratios, VLDL, non-HDL cholesterol, Estimated Average Glucose, Mean Blood Glucose, Random Blood Sugar, MCH, MCHC, BUN / Blood Urea Nitrogen, any urine test, or any other test.
  Haemoglobin (HB) and HbA1c are different tests.
  For each result: raw_name = the test name exactly as printed; value_text = the result exactly as printed (keep "<", ">" and all decimals); unit = as printed, or null; ref_text = the reference range exactly as printed, or null.
  If none of these tests are on this page, results is [].
In the page text, each line is one printed line and " | " separates table columns.
Reply with compact JSON on a single line, no indentation."""


def ask_gemma(page: PageInput, model: str, retry: bool = False) -> PageExtraction:
    """One Gemma call for one page.

    Raises pydantic.ValidationError if the reply does not fit the schema, and
    ExtractError if Ollama is unreachable, the model is missing or the connection drops.
    """
    intro = f"Page {page.number} of {page.total}."
    if page.mode == "vision":
        user = {"role": "user", "content": f"{intro} The page is attached as an image.",
                "images": [page.image]}
    else:
        user = {"role": "user", "content": f"{intro} Page text:\n\n{page.text}"}
    options = {**OLLAMA_OPTIONS, "temperature": RETRY_TEMPERATURE} if retry else OLLAMA_OPTIONS
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, user],
            format=PAGE_SCHEMA,
            options=options,
            think=False,
        )
    except ConnectionError:
        raise ExtractError(
            "Can't reach Ollama. Start the Ollama app (or run: ollama serve) and try again."
        ) from None
    except ollama.ResponseError as error:
        if error.status_code == 404:
            raise ExtractError(f"Model {model} is not installed. Run: ollama pull {model}") from None
        raise ExtractError(f"Ollama error: {error.error}") from None
    except httpx.TransportError:
        raise ExtractError(
            "Lost the connection to Ollama while reading the report. Is it still running?"
        ) from None
    return PageExtraction.model_validate_json(response.message.content)
