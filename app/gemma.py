"""Calls to local Gemma through Ollama: transcribing scans, and picking the MVP tests
out of a page's text with the reply held to a JSON schema.

Scans are read in two steps. Asked to read the image, pick the tests and fill the
schema all at once, Gemma returned 2 of 6 tests on a scanned page; asked only to
transcribe it, it read every line, and the text step then found all 6.
"""

from importlib import resources
from string import Template

import httpx
import ollama
from pydantic import BaseModel

from app.config import settings
from app.errors import ExtractError
from app.lab_tests import CATALOG, TestCode
from app.pages import PageInput


class ExtractedResult(BaseModel):
    test_code: TestCode
    raw_name: str
    value_text: str
    unit: str | None
    ref_text: str | None


# What Gemma returns for one page; every key is required, values may be null.
# (No docstring: Pydantic would put it into the schema sent to Gemma.)
class PageExtraction(BaseModel):
    patient_name: str | None
    age: str | None
    sex: str | None
    lab_name: str | None
    sample_date: str | None
    report_date: str | None
    results: list[ExtractedResult]


PAGE_SCHEMA = PageExtraction.model_json_schema()  # Ollama constrains Gemma's reply to this


def _prompt(name: str) -> str:
    return resources.files("app").joinpath("prompts", name).read_text(encoding="utf-8").rstrip("\n")


SYSTEM_PROMPT = Template(_prompt("extract_page.txt")).substitute(
    test_list="\n".join(f"  {test.code:<6} = {test.prompt}" for test in CATALOG.tests),
    exclusions=", ".join(CATALOG.prompt_exclusions),
)
TRANSCRIBE_PROMPT = _prompt("transcribe_page.txt")


def transcribe(page: PageInput, model: str) -> str:
    """Gemma reads a scanned page's image into plain text, line by line."""
    response = _chat(
        model=model,
        options=settings.ollama_options(),
        messages=[{"role": "user", "content": TRANSCRIBE_PROMPT, "images": [page.image]}],
    )
    return response.message.content


def ask_gemma(page: PageInput, model: str, retry: bool = False) -> PageExtraction:
    """Gemma picks the MVP tests out of one page's text (a scan's transcription for vision pages).

    Raises pydantic.ValidationError if the reply does not fit the schema, and
    ExtractError if Ollama is unreachable, the model is missing or the connection drops.
    """
    if not page.text:
        raise ValueError(f"page {page.number} has no text; transcribe scanned pages first")
    response = _chat(
        model=model,
        options=settings.ollama_options(retry),
        format=PAGE_SCHEMA,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Page {page.number} of {page.total}. Page text:\n\n{page.text}"},
        ],
    )
    return PageExtraction.model_validate_json(response.message.content)


def _chat(model: str, **request) -> ollama.ChatResponse:
    """ollama.chat with thinking off, and Ollama problems turned into one-sentence ExtractErrors."""
    try:
        return ollama.chat(model=model, think=False, **request)
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
