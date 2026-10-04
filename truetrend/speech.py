"""Reading the Marathi summary aloud, with a voice that runs on this computer.

Windows ships no Marathi text-to-speech voice, and a browser can only use what the
system has, so `speechSynthesis` cannot say these sentences on a laptop. A phone has
one; a laptop never will.

So the laptop speaks for itself: Piper (https://github.com/OHF-Voice/piper1-gpl) runs a
small ONNX model here and returns WAV audio, exactly like every other part of this app.
Nothing is sent anywhere, and it works with the internet unplugged.

The voice is downloaded once, into settings.voices_dir, by `truetrend-voice install`. Until
then the app says so and falls back to the browser's own voice, which on a phone is the
right one anyway.
"""

import argparse
import io
import logging
import sys
import urllib.error
import urllib.request
import wave
from functools import lru_cache
from pathlib import Path

from truetrend import cli
from truetrend.config import settings
from truetrend.errors import UserError
from truetrend.files import write_whole

logger = logging.getLogger(__name__)

# The Marathi voice published by the Piper project, trained on Google's Marathi corpus.
VOICE = "mr_IN-google-medium"
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/mr/mr_IN/google/medium/"
# Its two files: the model, and the JSON beside it that says how to read the text.
VOICE_FILES = (f"{VOICE}.onnx", f"{VOICE}.onnx.json")
DOWNLOAD_TIMEOUT = 300  # seconds; the model is ~77 MB

_SPEAKER = 0  # the voice is multi-speaker; speaker 0 reads clearly and evenly
_LENGTH_SCALE = 1.08  # a little slower than the model's default: these are numbers, not chat


def voice_path() -> Path:
    """Where the model is kept, once installed."""
    return settings.voices_dir / f"{VOICE}.onnx"


def installed() -> bool:
    """Whether the Marathi voice has been downloaded."""
    return voice_path().is_file() and voice_path().with_suffix(".onnx.json").is_file()


@lru_cache(maxsize=1)
def _voice():
    """The loaded model, kept for the life of the process: loading costs ~2 seconds."""
    try:
        from piper import PiperVoice  # imported here: only this command needs it
    except ImportError:
        raise UserError("The Marathi voice needs piper-tts. Install it with: pip install piper-tts") from None
    if not installed():
        raise UserError("The Marathi voice is not installed yet. Run: truetrend-voice install")
    return PiperVoice.load(str(voice_path()))


def say(text: str) -> bytes:
    """`text` spoken in Marathi, as a WAV file."""
    if not text.strip():
        raise UserError("There is nothing to say.")
    from piper import SynthesisConfig  # beside PiperVoice; imported with it

    voice = _voice()  # before opening the file: a missing voice is not a half-written WAV
    audio = io.BytesIO()
    with wave.open(audio, "wb") as out:
        voice.synthesize_wav(
            text,
            out,
            syn_config=SynthesisConfig(speaker_id=_SPEAKER, length_scale=_LENGTH_SCALE),
        )
    return audio.getvalue()


def install(force: bool = False) -> Path:
    """Download the voice into settings.voices_dir. About 77 MB, once."""
    settings.voices_dir.mkdir(parents=True, exist_ok=True)
    for name in VOICE_FILES:
        target = settings.voices_dir / name
        if target.is_file() and not force:
            logger.info("Already have %s.", name)
            continue
        logger.info("Downloading %s ...", name)
        try:
            with urllib.request.urlopen(VOICE_URL + name, timeout=DOWNLOAD_TIMEOUT) as reply:
                write_whole(target, reply.read())
        except (urllib.error.URLError, TimeoutError) as problem:
            raise UserError(
                f"Could not download the Marathi voice ({problem}). "
                "This is the one step that needs the internet; everything else works without it."
            ) from None
        logger.info("Saved %s (%.0f MB).", name, target.stat().st_size / 1e6)
    return voice_path()


def _command(args: argparse.Namespace) -> int:
    if args.what == "install":
        path = install(force=args.force)
        logger.info("The Marathi voice is ready: %s", path)
    elif args.what == "say":
        write_whole(Path(args.out), say(args.text))
        logger.info("Wrote %s", args.out)
    else:  # where
        logger.info("%s (%s)", voice_path(), "installed" if installed() else "not installed yet")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = cli.parser("voice", "The Marathi voice that reads the summary aloud.")
    parser.add_argument(
        "what",
        nargs="?",
        default="where",
        choices=("install", "where", "say"),
        help="install the voice (~77 MB, once), say where it is, or speak some text",
    )
    parser.add_argument("text", nargs="?", help="for 'say': the Marathi text to speak")
    parser.add_argument("--out", default="said.wav", help="for 'say': the WAV file to write")
    parser.add_argument("--force", action="store_true", help="download again even if it is there")
    return cli.run_command(parser, _command, argv)


if __name__ == "__main__":
    sys.exit(main())
