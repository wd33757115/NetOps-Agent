"""Server-owned DSH homes. DSH owns log format/recovery; NetOps owns isolation."""

import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path


def key(value):
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def native_logs(home):
    """DSH includes escaped cwd in its log path, exceeding MAX_PATH on Windows."""
    path = str(Path(home).resolve())
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        path = "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    return Path(path).rglob("session.v3.jsonl")


@contextmanager
def session_home(root, actor_id, conversation_id):
    root = Path(root).resolve()
    home = root / key(json.dumps([actor_id, conversation_id]))
    home.mkdir(parents=True, exist_ok=True)
    # OS-owned lock survives stale lock files and prevents two Gateway processes
    # from replacing a workspace or writing the same native session concurrently.
    with (home / "session.lock").open("a+b") as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            workspace = home / "documents"
            if workspace.exists():
                if workspace.is_symlink() or workspace.resolve().parent != home.resolve():
                    raise ValueError("Invalid generated document workspace")
                shutil.rmtree(workspace)
            yield home
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def scope_key(catalog, tools):
    # Changed publication, filters or allowed tools starts a fresh native scope;
    # old tool results must not silently cross a permission/scope boundary.
    return key(
        json.dumps(
            {
                "files": sorted(catalog.files),
                "filters": catalog.filters,
                "tools": sorted(tools),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
    )
