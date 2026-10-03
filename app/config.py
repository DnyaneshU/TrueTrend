"""Every setting in one place.

Each can be overridden with an AROGYA_* environment variable or a .env file in the
repo root, for example AROGYA_MODEL=gemma4:e2b or AROGYA_STORAGE_DIR=D:/arogya.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AROGYA_", env_file=ROOT / ".env", extra="ignore")

    # Gemma, through Ollama
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

    # Files
    storage_dir: Path = ROOT / "storage"  # gitignored: the database and stored original PDFs
    data_dir: Path = ROOT / "data"  # the lab test catalog

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "arogya.db"

    @property
    def originals_dir(self) -> Path:
        return self.storage_dir / "originals"

    def ollama_options(self, retry: bool = False) -> dict[str, float | int]:
        return {
            "temperature": self.retry_temperature if retry else self.temperature,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }


settings = Settings()
