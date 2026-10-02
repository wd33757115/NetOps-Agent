"""Full live outputs read in a diagnosis turn, kept so rows can be looked up later.

A comparison or a run returns summaries and excerpts; the model then asks for the
rows it needs (compare_rows) without logging in again. Kept in this gateway
process only, bounded by size and age; a lookup that misses says so.
"""

import time
from collections import OrderedDict
from threading import Lock

MAX_BYTES = 64 * 1024 * 1024
MAX_AGE_SECONDS = 3600

_lock = Lock()
_items = OrderedDict()  # (turn, host, command) -> (output, observed_at, saved_at, size)
_size = 0


def normal(command):
    return " ".join(str(command).lower().split())


def keep(turn_id, host, command, output, observed_at):
    global _size
    size = len(output.encode("utf-8", errors="ignore"))
    key = (turn_id, host, normal(command))
    with _lock:
        if key in _items:
            _size -= _items.pop(key)[3]
        _items[key] = (output, observed_at, time.monotonic(), size)
        _size += size
        while _items and (
            _size > MAX_BYTES
            or time.monotonic() - next(iter(_items.values()))[2] > MAX_AGE_SECONDS
        ):
            _size -= _items.popitem(last=False)[1][3]


def get(turn_id, host, command):
    """(output, observed_at) for this turn's live read of the command, or None."""
    with _lock:
        item = _items.get((turn_id, host, normal(command)))
    return (item[0], item[1]) if item else None


def outputs(turn_id, host):
    """Every live output this turn read on the host: {command: (output, observed_at)}."""
    with _lock:
        return {
            command: (item[0], item[1])
            for (turn, address, command), item in _items.items()
            if turn == turn_id and address == host
        }
