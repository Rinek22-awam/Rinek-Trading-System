"""Persist the live executor's HTTP 403 pause across process restarts."""
import math
from pathlib import Path
import time

PATH = Path("data") / "bybit-ip-cooldown"


def remaining():
    try:
        deadline = float(PATH.read_text(encoding="ascii"))
    except (OSError, ValueError):
        return 0
    return max(0, math.ceil(deadline - time.time())) if math.isfinite(deadline) else 0


def save(seconds):
    temporary = PATH.with_suffix(".tmp")
    temporary.write_text(str(time.time() + seconds), encoding="ascii")
    temporary.replace(PATH)
