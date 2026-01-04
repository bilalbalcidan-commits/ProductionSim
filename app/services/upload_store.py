from __future__ import annotations

import uuid
from pathlib import Path
from typing import Tuple


UPLOAD_DIR = Path("app/data/uploads")


def save_upload(content: bytes, filename: str) -> Tuple[str, Path]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    safe_name = filename.replace("/", "_").replace("\\", "_") or "upload.bin"
    path = UPLOAD_DIR / f"{token}__{safe_name}"
    path.write_bytes(content)
    return token, path


def load_upload(token: str) -> Path | None:
    if not UPLOAD_DIR.exists():
        return None
    for path in UPLOAD_DIR.glob(f"{token}__*"):
        return path
    return None
