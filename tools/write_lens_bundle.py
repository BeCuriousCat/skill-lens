"""Stage and validate a Bundle before replacing its published directory."""

import json
import shutil
import tempfile
from pathlib import Path

from validate_lens_bundle import REQUIRED, validate


def write_bundle(destination, values):
    destination = Path(destination).absolute()
    if set(values) != set(REQUIRED):
        raise ValueError("Bundle must contain exactly the six required documents")
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / f".{destination.name}.write-lock"
    # Exclusive creation prevents two cooperating writers from interleaving.
    lock.mkdir()
    workspace = None
    try:
        if destination.is_symlink():
            raise ValueError("Bundle destination must not be a symlink")
        if destination.exists() and (
            not destination.is_dir()
            or any(p.name not in REQUIRED or not p.is_file() or p.is_symlink()
                   for p in destination.iterdir())
        ):
            raise ValueError("Refusing to replace a directory containing non-Bundle files")
        workspace = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
        staged = workspace / "next"
        backup = workspace / "previous"
        staged.mkdir()
        for name in REQUIRED:
            (staged / name).write_text(json.dumps(values[name], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = validate(staged)
        if not result["valid"]:
            raise ValueError("invalid Bundle: " + "; ".join(result["errors"]))
        if destination.exists():
            destination.rename(backup)
        try:
            staged.rename(destination)
        except BaseException:
            if backup.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        # Keep a backup if rollback itself failed; never discard the old result.
        if workspace is not None and not (workspace / "previous").exists():
            shutil.rmtree(workspace)
        lock.rmdir()
