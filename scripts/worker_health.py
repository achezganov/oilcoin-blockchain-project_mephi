"""Read-only health check for the background game process."""
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import default_path


def is_healthy(path, now=None):
    now = time.time() if now is None else now
    try:
        with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=5) as connection:
            row = connection.execute("SELECT payload FROM runtime_state WHERE key='heartbeat'").fetchone()
        heartbeat = json.loads(row[0]) if row else None
        return (type(heartbeat) in (int, float) and math.isfinite(heartbeat)
                and 0 <= now - heartbeat < 20)
    except (OSError, ValueError, TypeError, OverflowError, sqlite3.Error):
        return False


if __name__ == '__main__':
    raise SystemExit(0 if is_healthy(default_path()) else 1)
