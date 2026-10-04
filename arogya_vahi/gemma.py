"""Calls to local Gemma through Ollama: transcribing scans, and picking the supported tests
out of a page's text with the reply held to a JSON schema.

Scans are read in two steps. Asked to read the image, pick the tests and fill the
schema all at once, Gemma returned 2 of 6 tests on a scanned page; asked only to
transcribe it, it read every line, and the text step then found all 6.
"""

import logging
from functools import cache
from string import Template

import httpx
import ollama
from pydantic import BaseModel, ValidationError

from arogya_vahi.config import settings
from arogya_vahi.errors import UserError
from arogya_vahi.lab_tests import CATALOG
from arogya_vahi.models import PageExtraction, PageInput
from arogya_vahi.resources import read_text

logger = logging.getLogger(__name__)

PAGE_SCHEMA = PageExtraction.model_json_schema()  # Ollama constrains Gemma's reply to this
SYSTEM_PROMPT = Template(read_text("prompts", "extract_page.txt").rstrip("\n")).substitute(
    test_list="\n".join(f"  {test.code:<6} = {test.prompt}" for test in CATALOG.tests),
    exclusions=", ".join(CATALOG.prompt_exclusions),
)
TRANSCRIBE_PROMPT = read_text("prompts", "transcribe_page.txt").rstrip("\n")


def transcribe(page: PageInput, model: str) -> str:
    """Gemma reads a scanned page's image into plain text, line by line."""
    response = _chat(
        model=model,
        options=_options(),
        messages=[{"role": "user", "content": TRANSCRIBE_PROMPT, "images": [page.image]}],
    )
    return response.message.content


def extract_results(page: PageInput, model: str, retry: bool = False) -> PageExtraction:
    """Gemma picks the supported tests out of one page's text (a scan's transcription for vision pages).

    Raises pydantic.ValidationError if the reply does not fit the schema, and
    UserError if Ollama is unreachable, the model is missing or the connection drops.
    """
    if not page.text:
        raise ValueError(f"page {page.number} has no text; transcribe scanned pages first")
    response = _chat(
        model=model,
        options=_options(retry),
        format=PAGE_SCHEMA,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Page {page.number} of {page.total}. Page text:\n\n{page.text}"},
        ],
    )
    return PageExtraction.model_validate_json(response.message.content)


def ask_json(prompt: str, schema: type[BaseModel], model: str) -> BaseModel | None:
    """One question whose answer must fit `schema`, or None when the reply does not.

    Used where Gemma chooses between fixed options rather than writing anything -- see
    arogya_vahi.ask, where it reads a question but never answers it.
    """
    response = _chat(
        model=model,
        options=_options(),
        format=schema.model_json_schema(),
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        return schema.model_validate_json(response.message.content)
    except ValidationError:
        logger.debug("Gemma's reply did not fit %s: %r", schema.__name__, response.message.content)
        return None


def _options(retry: bool = False) -> dict[str, float | int]:
    return {
        "temperature": settings.retry_temperature if retry else settings.temperature,
        "num_ctx": settings.num_ctx,
        "num_predict": settings.num_predict,
    }


@cache
def _client() -> ollama.Client:
    """The Ollama client for settings.ollama_host; OLLAMA_HOST in the environment is ignored."""
    return ollama.Client(host=settings.ollama_host)


def _chat(model: str, **request) -> ollama.ChatResponse:
    """A chat with thinking off, and Ollama problems turned into one-sentence UserErrors."""
    try:
        return _client().chat(model=model, think=False, **request)
    except ConnectionError:
        raise UserError(
            "Can't reach Ollama. Start the Ollama app (or run: ollama serve) and try again."
        ) from None
    except ollama.ResponseError as error:
        if error.status_code == 404:
            raise UserError(f"Model {model} is not installed. Run: ollama pull {model}") from None
        raise UserError(f"Ollama error: {error.error}") from None
    except httpx.TransportError:
        raise UserError(
            "Lost the connection to Ollama while reading the report. Is it still running?"
        ) from None
