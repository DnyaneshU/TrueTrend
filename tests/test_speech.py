"""The Marathi voice: what the app does with it, and without it."""

import pytest

from truetrend import speech
from truetrend.config import settings
from truetrend.errors import UserError


def test_the_voice_is_not_installed_on_a_fresh_machine(storage):
    assert not speech.installed()


def test_asking_it_to_speak_before_it_is_installed_says_how_to_install_it(storage):
    speech._voice.cache_clear()
    with pytest.raises(UserError, match="truetrend-voice install"):
        speech.say("काहीतरी")


def test_both_files_are_needed_not_just_the_model(storage):
    settings.voices_dir.mkdir(parents=True)
    (settings.voices_dir / f"{speech.VOICE}.onnx").write_bytes(b"not really a model")
    assert not speech.installed()  # the JSON beside it says how to read the text
    (settings.voices_dir / f"{speech.VOICE}.onnx.json").write_text("{}")
    assert speech.installed()


def test_nothing_to_say_is_refused_rather_than_making_an_empty_file(storage):
    with pytest.raises(UserError, match="nothing to say"):
        speech.say("   ")


def test_the_voice_is_downloaded_from_one_place_that_is_named_in_the_code():
    # Every file the app fetches should be traceable to a source, like the CVi constants.
    assert speech.VOICE_URL.startswith("https://huggingface.co/rhasspy/piper-voices/")
    assert (f"{speech.VOICE}.onnx", f"{speech.VOICE}.onnx.json") == speech.VOICE_FILES
