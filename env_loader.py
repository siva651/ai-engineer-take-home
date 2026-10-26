"""Small standard-library-only loader for the project's simple .env file."""
from __future__ import annotations

import os
import re
from pathlib import Path

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def load_env(path: Path | None = None) -> None:
    """Load KEY=value lines from .env without overriding existing environment variables.

    This intentionally supports the simple format used by .env.example; it is not
    a full implementation of shell syntax or python-dotenv's extended features.
    """
    env_path = Path(path) if path is not None else Path(__file__).resolve().parent / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    except OSError as error:
        raise RuntimeError(f"Cannot read environment file {env_path}: {error}") from None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not _KEY.fullmatch(key) or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ[key] = value
