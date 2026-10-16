import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
import uuid


def _backup_name(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return path.with_name(f"blockchain.backup.{stamp}.{uuid.uuid4().hex}.json")


def migrate_blockchain_file(filename: str = "blockchain.json") -> tuple[bool, str]:
    path = Path(filename)
    if not path.exists():
        return False, f"File not found: {filename}"

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        return False, f"Invalid JSON: {error}"

    chain = payload.get("chain") if isinstance(payload, dict) else None
    if not isinstance(chain, list) or any(not isinstance(block, dict) for block in chain):
        return False, "Invalid payload: 'chain' must be a list of block objects."

    backup_path = _backup_name(path)
    try:
        with path.open('rb') as source, backup_path.open('xb') as destination:
            shutil.copyfileobj(source, destination)
        shutil.copystat(path, backup_path)
    except OSError as error:
        return False, f"Backup failed: {error}"

    changed = False
    for block in chain:
        if "version" not in block:
            block["version"] = 1
            changed = True
        if "mining_started_at" not in block:
            block["mining_started_at"] = None
            changed = True
        if "mining_finished_at" not in block:
            block["mining_finished_at"] = None
            changed = True
        if "mining_duration_seconds" not in block:
            block["mining_duration_seconds"] = None
            changed = True

    if changed:
        descriptor, temporary = tempfile.mkstemp(prefix='.oilcoin-migration-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump(payload, output, indent=2, ensure_ascii=False, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            os.replace(temporary, path)
        except (OSError, ValueError) as error:
            return False, f"Migration not written; original preserved: {error}"
        finally:
            Path(temporary).unlink(missing_ok=True)
        return True, f"Migration complete. Backup: {backup_path.name}"

    return True, f"No changes required. Backup: {backup_path.name}"


if __name__ == "__main__":
    ok, message = migrate_blockchain_file()
    print(message)
    raise SystemExit(0 if ok else 1)
