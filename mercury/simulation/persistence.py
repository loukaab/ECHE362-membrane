"""Atomic, finite-number JSON documents."""

import json
import os
from pathlib import Path
import tempfile


def read_json(path: Path) -> dict:
    def invalid(value: str) -> None:
        raise ValueError(f"Invalid JSON number: {value}")
    value = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError("JSON document must be an object")
    return value


def write_json(path: Path, document: dict) -> None:
    path = Path(path)
    contents = json.dumps(document, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
