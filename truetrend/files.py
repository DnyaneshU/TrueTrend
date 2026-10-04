"""Writing files so that a crash never leaves half of one."""

import os
import tempfile
from pathlib import Path

from truetrend.errors import UserError


def write_whole(path: Path, data: bytes) -> None:
    """Write the file completely or not at all: to a temporary file beside it, then renamed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as part:
        part.write(data)
        part.flush()
        os.fsync(part.fileno())
    try:
        os.replace(part.name, path)
    except PermissionError:
        raise UserError(f"{path.name} is open in another program; close it and try again.") from None
    finally:
        Path(part.name).unlink(missing_ok=True)
