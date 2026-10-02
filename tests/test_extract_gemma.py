import typing
from types import SimpleNamespace

import httpx
import ollama
import pytest
from pydantic import ValidationError

from app import extract
from app.extract import ExtractError, PageExtraction, PageInput, ask_gemma

VALID_REPLY = (
    '{"patient_name":"Mrs. Sunita Patil","age":"62","sex":"F","lab_name":"SUNRISE DIAGNOSTICS",'
    '"sample_date":"12/09/2026 08:10","report_date":null,"results":[{"test_code":"HBA1C",'
    '"raw_name":"Glycosylated Haemoglobin (HbA1c)","value_text":"7.2","unit":"%","ref_text":"4.0 - 5.6"}]}'
)
TEXT_PAGE = PageInput(number=1, total=2, mode="text", text="Glycosylated Haemoglobin (HbA1c) | 7.2 | % | 4.0 - 5.6")
VISION_PAGE = PageInput(number=2, total=2, mode="vision", image=b"\x89PNG fake")


class FakeChat:
    """Stands in for ollama.chat: records the call and returns `reply` or raises `error`."""

    def __init__(self, reply=VALID_REPLY, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(message=SimpleNamespace(content=self.reply))


@pytest.fixture
def fake_chat(monkeypatch):
    def install(**kwargs):
        fake = FakeChat(**kwargs)
        monkeypatch.setattr(extract.ollama, "chat", fake)
        return fake
    return install


def test_text_page_is_sent_as_text_with_fixed_settings(fake_chat):
    fake = fake_chat()
    result = ask_gemma(TEXT_PAGE, "gemma4:e4b")
    assert result.results[0].value_text == "7.2"
    call = fake.calls[0]
    assert call["model"] == "gemma4:e4b"
    assert call["think"] is False
    assert call["options"] == {"temperature": 0, "num_ctx": 8192, "num_predict": 2048}
    assert call["format"] == PageExtraction.model_json_schema()
    system, user = call["messages"]
    assert system == {"role": "system", "content": extract.SYSTEM_PROMPT}
    assert user["content"].startswith("Page 1 of 2.")
    assert "(HbA1c) | 7.2 | %" in user["content"]
    assert "images" not in user


def test_vision_page_is_sent_as_image(fake_chat):
    fake = fake_chat()
    ask_gemma(VISION_PAGE, "gemma4:e4b")
    user = fake.calls[0]["messages"][1]
    assert user["images"] == [b"\x89PNG fake"]
    assert user["content"].startswith("Page 2 of 2.")


def test_retry_uses_slightly_higher_temperature(fake_chat):
    fake = fake_chat()
    ask_gemma(TEXT_PAGE, "gemma4:e4b", retry=True)
    assert fake.calls[0]["options"] == {"temperature": 0.3, "num_ctx": 8192, "num_predict": 2048}


def test_truncated_reply_raises_validation_error(fake_chat):
    fake_chat(reply='{"patient_name": "Mrs. Sun')
    with pytest.raises(ValidationError):
        ask_gemma(TEXT_PAGE, "gemma4:e4b")


def test_unknown_test_code_raises_validation_error(fake_chat):
    fake_chat(reply=VALID_REPLY.replace('"HBA1C"', '"T3"'))
    with pytest.raises(ValidationError):
        ask_gemma(TEXT_PAGE, "gemma4:e4b")


@pytest.mark.parametrize("error, message", [
    (ConnectionError("refused"), "Can't reach Ollama"),
    (ollama.ResponseError("model 'gemma4:e4b' not found", 404), "Run: ollama pull gemma4:e4b"),
    (ollama.ResponseError("out of memory", 500), "Ollama error: out of memory"),
    (httpx.ReadError("connection reset"), "Lost the connection to Ollama"),
])
def test_ollama_problems_become_clear_errors(fake_chat, error, message):
    fake_chat(error=error)
    with pytest.raises(ExtractError, match=message):
        ask_gemma(TEXT_PAGE, "gemma4:e4b")


def test_schema_requires_every_field_and_limits_test_codes():
    schema = PageExtraction.model_json_schema()
    assert set(schema["required"]) == {
        "patient_name", "age", "sex", "lab_name", "sample_date", "report_date", "results",
    }
    codes = set(typing.get_args(extract.TestCode))
    assert len(codes) == 15
    assert set(schema["$defs"]["ExtractedResult"]["properties"]["test_code"]["enum"]) == codes


def test_prompt_describes_every_test_code():
    for code in typing.get_args(extract.TestCode):
        assert f"\n  {code} " in extract.SYSTEM_PROMPT
