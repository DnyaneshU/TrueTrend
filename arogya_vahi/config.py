"""Every setting in one place.

Each can be overridden with an AROGYA_* environment variable or a .env file in the
current folder, for example AROGYA_MODEL=gemma4:e2b or AROGYA_STORAGE_DIR=D:/arogya.
"""

import os
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from platformdirs import user_data_dir
from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CHECKOUT = Path(__file__).resolve().parent.parent  # the repo, when run from a source checkout


def _default_storage() -> Path:
    """storage/ in a source checkout (gitignored); otherwise the user's data folder."""
    if (_CHECKOUT / "pyproject.toml").is_file():
        return _CHECKOUT / "storage"
    return Path(user_data_dir("arogya-vahi", appauthor=False))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AROGYA_", env_file=".env", extra="ignore")

    # Gemma, through Ollama. Reports are sent only to this host, which must be this
    # computer unless allow_remote_ollama is set: patient data must not leave the house.
    ollama_host: str = "http://127.0.0.1:11434"
    allow_remote_ollama: bool = False
    model: str = "gemma4:e4b"
    temperature: float = 0.0
    retry_temperature: float = 0.3  # a repeated temperature-0 call would usually repeat a broken reply
    num_ctx: int = 8192
    num_predict: int = 2048

    # Reading pages
    scan_text_threshold: int = 50  # fewer visible characters than this: the page is a scan
    image_page_text_threshold: int = 200  # ...or fewer than this while images cover image_page_coverage
    image_page_coverage: float = 0.5  # of the page (a scan with a typed header or footer)
    render_dpi: int = 150
    max_image_side: int = 2000  # pixels; huge photo-to-PDF pages are rendered smaller
    column_gap: float = 0.6  # a gap wider than this × text height separates table columns

    # Verifying
    max_label_words: int = 8  # a longer "label" left of a number is a sentence, not a test name

    # Files: the database and the stored original PDFs. Never commit them.
    storage_dir: Path = _default_storage()
    db_timeout: float = 30.0  # seconds to wait while another command is writing to the database

    # The web app
    max_upload_bytes: int = 50 * 2**20  # a phone photo or a long report fits; a mistake doesn't
    highlight_max_width: int = 1400  # pixels across for the page picture that marks a value
    session_cookie: str = "arogya_session"  # where a browser keeps its sign-in token

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "arogya.db"

    @property
    def originals_dir(self) -> Path:
        """Stored originals, named <sha256>.pdf: long names, so long paths must work on Windows."""
        return _long_path(self.storage_dir / "originals")

    @property
    def inbox_dir(self) -> Path:
        """Sent files waiting to be read, as PDFs named <sha256>.pdf."""
        return _long_path(self.storage_dir / "inbox")

    @field_validator("ollama_host")
    @classmethod
    def _has_a_scheme(cls, host: str) -> str:
        return host if "://" in host else f"http://{host}"

    @model_validator(mode="after")
    def _ollama_is_local(self) -> "Settings":
        hostname = urlsplit(self.ollama_host).hostname or ""
        if not self.allow_remote_ollama and not _is_loopback(hostname):
            raise ValueError(
                f"ollama_host {self.ollama_host!r} is not this computer, so reports would leave it. "
                "Use a local Ollama, or set AROGYA_ALLOW_REMOTE_OLLAMA=true if that is intended."
            )
        return self


_WINDOWS_LONG_PATH = "\\\\?\\"  # the \\?\ prefix: no 260-character limit
_WINDOWS_SHARE = "\\\\"  # a network share, \\server\share


def _long_path(path: Path) -> Path:
    """On Windows, the path in a form that works past the 260-character limit."""
    if os.name != "nt":
        return path
    resolved = str(path.resolve())
    if resolved.startswith(_WINDOWS_LONG_PATH):
        return Path(resolved)
    if resolved.startswith(_WINDOWS_SHARE):  # \\server\share takes the prefix as \\?\UNC\server\share
        return Path(_WINDOWS_LONG_PATH + "UNC\\" + resolved[len(_WINDOWS_SHARE) :])
    return Path(_WINDOWS_LONG_PATH + resolved)


def _is_loopback(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _problem(error: dict) -> str:
    """One validation error as the user can act on it: "AROGYA_NUM_CTX: ..." or the message alone."""
    message = error["msg"].removeprefix("Value error, ")
    return f"AROGYA_{'_'.join(map(str, error['loc'])).upper()}: {message}" if error["loc"] else message


def _load() -> Settings:
    """The settings, or one line saying which is wrong: they load before any command runs."""
    try:
        return Settings()
    except ValidationError as error:
        problems = "; ".join(_problem(e) for e in error.errors())
        raise SystemExit(f"error: invalid setting: {problems}") from None


settings = _load()
