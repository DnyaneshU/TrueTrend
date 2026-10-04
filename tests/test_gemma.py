import os
import typing
from pathlib import Path
from types import SimpleNamespace

import httpx
import ollama
import pytest
from pydantic import ValidationError

from truetrend import config, gemma, lab_tests
from truetrend.config import Settings
from truetrend.errors import UserError
from truetrend.gemma import _client as _real_client
from truetrend.gemma import extract_results, transcribe
from truetrend.models import PageExtraction, PageInput

VALID_REPLY = (
    '{"patient_name":"Mrs. Sunita Patil","age":"62","sex":"F","lab_name":"SUNRISE DIAGNOSTICS",'
    '"sample_date":"12/09/2026 08:10","report_date":null,"results":[{"test_code":"HBA1C",'
    '"raw_name":"Glycosylated Haemoglobin (HbA1c)","value_text":"7.2","unit":"%","ref_text":"4.0 - 5.6"}]}'
)
TEXT_PAGE = PageInput(
    number=1, total=2, mode="text", text="Glycosylated Haemoglobin (HbA1c) | 7.2 | % | 4.0 - 5.6"
)
VISION_PAGE = PageInput(number=2, total=2, mode="vision", image=b"\x89PNG fake")


class FakeChat:
    """Stands in for the Ollama client's chat: records the call and returns `reply` or raises `error`."""

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
        monkeypatch.setattr(gemma, "_client", lambda: SimpleNamespace(chat=fake))
        return fake

    return install


def test_text_page_is_sent_as_text_with_fixed_settings(fake_chat):
    fake = fake_chat()
    result = extract_results(TEXT_PAGE, "gemma4:e4b")
    assert result.results[0].value_text == "7.2"
    call = fake.calls[0]
    assert call["model"] == "gemma4:e4b"
    assert call["think"] is False
    assert call["options"] == {"temperature": 0, "num_ctx": 8192, "num_predict": 2048}
    assert call["format"] == PageExtraction.model_json_schema()
    system, user = call["messages"]
    assert system == {"role": "system", "content": gemma.SYSTEM_PROMPT}
    assert user["content"].startswith("Page 1 of 2.")
    assert "(HbA1c) | 7.2 | %" in user["content"]
    assert "images" not in user


def test_transcribe_sends_the_page_image_without_a_schema(fake_chat):
    fake = fake_chat(reply="Haemoglobin 11.8 g/dL 12.0 - 15.0")
    assert transcribe(VISION_PAGE, "gemma4:e4b") == "Haemoglobin 11.8 g/dL 12.0 - 15.0"
    call = fake.calls[0]
    (message,) = call["messages"]
    assert message["images"] == [VISION_PAGE.image]
    assert "format" not in call and call["think"] is False
    assert call["options"] == {"temperature": 0, "num_ctx": 8192, "num_predict": 2048}


def test_transcribe_turns_ollama_problems_into_clear_errors(fake_chat):
    fake_chat(error=ConnectionError("refused"))
    with pytest.raises(UserError, match="Can't reach Ollama"):
        transcribe(VISION_PAGE, "gemma4:e4b")


def test_scanned_page_is_extracted_from_its_transcription(fake_chat):
    fake = fake_chat()
    extract_results(
        VISION_PAGE.model_copy(update={"text": "Glycosylated Haemoglobin (HbA1c) 7.2 % 4.0 - 5.6"}),
        "gemma4:e4b",
    )
    user = fake.calls[0]["messages"][1]
    assert "images" not in user
    assert "(HbA1c) 7.2 %" in user["content"]


def test_extract_results_refuses_a_scan_that_was_not_transcribed(fake_chat):
    fake_chat()
    with pytest.raises(ValueError, match="transcribe"):
        extract_results(VISION_PAGE, "gemma4:e4b")


def test_retry_uses_slightly_higher_temperature(fake_chat):
    fake = fake_chat()
    extract_results(TEXT_PAGE, "gemma4:e4b", retry=True)
    assert fake.calls[0]["options"] == {"temperature": 0.3, "num_ctx": 8192, "num_predict": 2048}


def test_truncated_reply_raises_validation_error(fake_chat):
    fake_chat(reply='{"patient_name": "Mrs. Sun')
    with pytest.raises(ValidationError):
        extract_results(TEXT_PAGE, "gemma4:e4b")


def test_unknown_test_code_raises_validation_error(fake_chat):
    fake_chat(reply=VALID_REPLY.replace('"HBA1C"', '"T3"'))
    with pytest.raises(ValidationError):
        extract_results(TEXT_PAGE, "gemma4:e4b")


@pytest.mark.parametrize(
    "error, message",
    [
        (ConnectionError("refused"), "Can't reach Ollama"),
        (ollama.ResponseError("model 'gemma4:e4b' not found", 404), "Run: ollama pull gemma4:e4b"),
        (ollama.ResponseError("out of memory", 500), "Ollama error: out of memory"),
        (httpx.ReadError("connection reset"), "Lost the connection to Ollama"),
    ],
)
def test_ollama_problems_become_clear_errors(fake_chat, error, message):
    fake_chat(error=error)
    with pytest.raises(UserError, match=message):
        extract_results(TEXT_PAGE, "gemma4:e4b")


def test_schema_requires_every_field_and_limits_test_codes():
    schema = PageExtraction.model_json_schema()
    assert set(schema["required"]) == {
        "patient_name",
        "age",
        "sex",
        "lab_name",
        "sample_date",
        "report_date",
        "results",
    }
    codes = {test.code for test in lab_tests.CATALOG.tests}
    assert set(typing.get_args(lab_tests.TestCode)) == codes
    assert set(schema["$defs"]["ExtractedResult"]["properties"]["test_code"]["enum"]) == codes


def test_prompt_excludes_the_cbc_look_alikes_of_haemoglobin():
    exclusions = gemma.SYSTEM_PROMPT.split("Do NOT include:")[1].split("\n")[0]
    assert "MCH" in exclusions and "MCHC" in exclusions
    assert "Hb A" in exclusions and "HbF" in exclusions


def test_prompt_describes_every_test_code():
    for code in typing.get_args(lab_tests.TestCode):
        assert f"\n  {code} " in gemma.SYSTEM_PROMPT


def test_the_ollama_client_uses_the_configured_local_host(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://example.com:11434")  # ignored: it could send reports away
    _real_client.cache_clear()
    try:
        assert str(_real_client()._client.base_url).startswith("http://127.0.0.1:11434")
    finally:
        _real_client.cache_clear()


@pytest.mark.parametrize("host", ["http://192.168.1.20:11434", "https://ollama.example.com"])
def test_a_remote_ollama_must_be_allowed_explicitly(host):
    with pytest.raises(ValueError, match="is not this computer"):
        Settings(ollama_host=host, _env_file=None)
    assert Settings(ollama_host=host, allow_remote_ollama=True, _env_file=None).ollama_host == host


def test_a_host_without_a_scheme_is_http():
    assert Settings(ollama_host="localhost:11434", _env_file=None).ollama_host == "http://localhost:11434"


def test_an_invalid_setting_is_one_line_not_a_traceback(monkeypatch):
    monkeypatch.setenv("TRUETREND_NUM_CTX", "lots")
    message = "error: invalid setting: TRUETREND_NUM_CTX: Input should be a valid integer"
    with pytest.raises(SystemExit, match=message):
        config._load()


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
def test_long_paths_work_on_windows_including_network_shares():
    assert str(config._long_path(Path("C:/x/originals"))) == r"\\?\C:\x\originals"
    assert str(config._long_path(Path("//server/share/originals"))) == r"\\?\UNC\server\share\originals"
