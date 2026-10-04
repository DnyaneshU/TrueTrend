"""The Marathi voice: what the app does with it, and without it."""

import pytest

from truetrend import speech
from truetrend.config import settings
from truetrend.errors import UserError


def test_the_voice_is_not_installed_on_a_fresh_machine(storage):
    assert not speech.installed()


def test_asking_it_to_speak_before_it_is_installed_says_how_to_install_it(storage):
    # Two things can be missing, and each has its own sentence: piper itself (an optional
    # extra, so a machine running the tests may not have it) and the voice model. Which
    # one this machine is missing decides which sentence is right.
    speech._voice.cache_clear()
    try:
        import piper  # noqa: F401

        expected = "truetrend-voice install"
    except ImportError:
        expected = "pip install piper-tts"
    with pytest.raises(UserError, match=expected):
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


def test_without_piper_installed_it_says_how_to_install_it(storage, monkeypatch):
    # The voice is an optional extra, so most machines will not have piper. They should
    # get one sentence they can act on, not a ModuleNotFoundError traceback.
    import builtins

    real_import = builtins.__import__

    def no_piper(name, *args, **kwargs):
        if name == "piper" or name.startswith("piper."):
            raise ImportError("No module named 'piper'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_piper)
    speech._voice.cache_clear()
    with pytest.raises(UserError, match="pip install piper-tts"):
        speech.say("काहीतरी")
