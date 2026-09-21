"""Beat gates package schedules against the authority and reports loaded code."""

import json
import logging
import os
import socket
import time

import httpx
from celery.beat import PersistentScheduler
from redis.exceptions import RedisError

from .runtime import snapshot, token

PREFIX = "netops:app-packages:beat:"


def authority_url():
    return os.getenv("NETOPS_AUTHORITY_URL", "http://localhost:8001").rstrip("/")


def beat_reports(app):
    client = app.backend.client
    result = []
    for key in client.scan_iter(match=PREFIX + "*"):
        raw = client.get(key)
        if raw:
            result.append(json.loads(raw))
    return result


class AppScheduler(PersistentScheduler):
    def __init__(self, *args, **kwargs):
        self.loaded = snapshot()
        self.instance = f"{socket.gethostname()}:{os.getpid()}"
        self.last_report = 0
        super().__init__(*args, **kwargs)

    def tick(self, *args, **kwargs):
        if time.monotonic() - self.last_report > 10:
            # No heartbeat if Redis is unavailable; enable will remain blocked.
            try:
                self.app.backend.client.setex(
                    PREFIX + self.instance,
                    45,
                    json.dumps(
                        {
                            "instance": self.instance,
                            "apps": self.loaded,
                            "schedules": {
                                name: entry.task
                                for name, entry in self.schedule.items()
                                if name.startswith("app.")
                            },
                        }
                    ),
                )
            except RedisError:
                logging.getLogger(__name__).warning("Package Beat heartbeat unavailable")
            self.last_report = time.monotonic()
        return min(10, super().tick(*args, **kwargs))

    def apply_async(self, entry, producer=None, advance=True, **kwargs):
        if entry.name.startswith("app."):
            app_id = entry.name.split(".")[1]
            try:
                response = httpx.post(
                    authority_url() + "/api/platform/apps/runtime/",
                    json={"app_id": app_id, "digest": self.loaded.get(app_id)},
                    headers={"Authorization": "Bearer " + token()},
                    timeout=3,
                )
                allowed = response.status_code == 200
            except (httpx.HTTPError, ValueError):
                allowed = False
            if not allowed:
                if advance:
                    self.reserve(entry)
                return None
        return super().apply_async(entry, producer=producer, advance=advance, **kwargs)

    def close(self):
        try:
            self.app.backend.client.delete(PREFIX + self.instance)
        finally:
            super().close()
