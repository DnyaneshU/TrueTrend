"""Files shipped inside the package: the catalog and summary TOML, prompts and the SQL schema."""

import tomllib
from importlib import resources
from typing import Any


def read_text(*path: str) -> str:
    """A packaged text file, e.g. read_text("prompts", "extract_page.txt")."""
    return resources.files("truetrend").joinpath(*path).read_text(encoding="utf-8")


def load_toml(name: str) -> dict[str, Any]:
    """A packaged data/<name> TOML file."""
    return tomllib.loads(read_text("data", name))
