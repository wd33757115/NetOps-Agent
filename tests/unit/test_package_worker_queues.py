import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.platform.kernel import worker


def test_discovered_app_queue_is_consumed_without_stealing_patrol(monkeypatch):
    callback = Mock()
    monkeypatch.setattr(worker, "package_queues", lambda: ["netops.patrol", "app.new-business"])
    monkeypatch.setitem(
        sys.modules,
        "src.platform.kernel.package_celery",
        SimpleNamespace(app=SimpleNamespace(worker_main=callback)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker",
            "--packages-only",
            "--queues",
            "netops.ai",
            "--exclude-queues",
            "netops.patrol",
            "-P",
            "solo",
        ],
    )
    worker.main()
    callback.assert_called_once_with(["worker", "-Q", "app.new-business,netops.ai", "-P", "solo"])


def test_excluding_every_queue_fails_before_starting_worker(monkeypatch):
    monkeypatch.setattr(worker, "package_queues", lambda: ["netops.patrol"])
    monkeypatch.setattr(sys, "argv", ["worker", "--exclude-queues", "netops.patrol"])
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 2
