import json
import os
from pathlib import Path

from . import config


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write_json(path: Path, data, mode: int = 0o644) -> None:
    """Atomic: write tmp file, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load() -> dict:
    st = read_json(config.STATE_FILE, {})
    st.setdefault("version", 1)
    st.setdefault("followers", {})
    st.setdefault("history", [])
    return st


def save(st: dict) -> None:
    st["history"] = st["history"][-config.HISTORY_CAP:]
    write_json(config.STATE_FILE, st)
