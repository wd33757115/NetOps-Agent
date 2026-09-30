import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import yaml
from django.test import SimpleTestCase, TestCase
from sqlalchemy import create_engine

from src.platform.kernel.registry import AppManifest, discover

from .app_lifecycle import active_requests, operate
from .models import AppInstallation


class LifecycleTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite:///" + str(Path(self.directory.name) / "business.sqlite3")
        )
        self.patch = patch(
            "src.platform.kernel.migrations.business_engine", return_value=self.engine
        )
        self.patch.start()
        from src.platform.kernel.migrations import migrate
        migrate(discover()["delivery-probe"], engine=self.engine)

    def tearDown(self):
        self.patch.stop()
        self.engine.dispose()
        self.directory.cleanup()

    def test_install_enable_disable_and_repeat(self):
        record = operate("install", "delivery-probe")
        self.assertEqual(record.migration_head, "delivery_probe_0002")
        self.assertFalse(record.desired_enabled)
        with patch("bff.app_lifecycle.probe", side_effect=ValueError("old worker")):
            with self.assertRaisesMessage(ValueError, "old worker"):
                operate("enable", "delivery-probe")
        record.refresh_from_db()
        self.assertFalse(record.desired_enabled)
        with patch("bff.app_lifecycle.probe", return_value={"checked": True}):
            self.assertTrue(operate("enable", "delivery-probe").desired_enabled)
        with self.assertRaisesMessage(ValueError, "Disable"):
            operate("upgrade", "delivery-probe")
        self.assertEqual(operate("disable", "delivery-probe").state, "disabled")
        self.assertEqual(operate("drain", "delivery-probe").state, "disabled")
        self.assertEqual(operate("upgrade", "delivery-probe").state, "installed")

    def test_failed_install_preserves_successful_version_and_retry(self):
        previous = operate("install", "delivery-probe")
        with patch("bff.app_lifecycle.migrate", side_effect=RuntimeError("injected")):
            with self.assertRaises(ValueError):
                operate("upgrade", "delivery-probe")
        previous.refresh_from_db()
        self.assertEqual(previous.state, "failed")
        self.assertEqual(previous.deployment_digest, discover()["delivery-probe"].digest)
        self.assertEqual(operate("upgrade", "delivery-probe").state, "installed")

    def test_install_requires_platform_migration_and_never_creates_tables(self):
        from sqlalchemy import inspect
        empty = create_engine('sqlite://')
        try:
            with patch('src.platform.kernel.migrations.business_engine', return_value=empty):
                with self.assertRaisesMessage(ValueError, 'bootstrap apply'):
                    operate('install', 'delivery-probe')
            self.assertEqual(inspect(empty).get_table_names(), [])
        finally:
            empty.dispose()

    def test_drain_does_not_finish_while_work_is_active(self):
        operate("install", "delivery-probe")
        with patch("bff.app_lifecycle.pending", return_value=1):
            record = operate("disable", "delivery-probe")
        self.assertEqual(record.state, "draining")
        self.assertFalse(record.desired_enabled)
        with self.assertRaises(ValueError):
            operate("enable", "delivery-probe")
        self.assertEqual(operate("drain", "delivery-probe").state, "disabled")

    def test_old_sync_enable_cannot_bypass_probe(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        operate("install", "delivery-probe")
        with patch("bff.app_lifecycle.probe", side_effect=ValueError("deployment not ready")):
            with self.assertRaises(CommandError):
                call_command("sync_apps", enable="delivery-probe")
        self.assertFalse(AppInstallation.objects.get(pk="delivery-probe").desired_enabled)


class DeployedReleaseDrainTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.app_id, self.old_digest = "legacy-test", "a" * 64
        self.old_data = {
            "id": self.app_id,
            "backend": {
                "queue": "app.legacy-test.old",
                "legacy_queues": ["shared.old"],
                "task_names": ["legacy.task"],
            },
        }
        # Removing legacy queues from the new source must not bypass the old
        # release's worker inspection while it is still running.
        self.current = AppManifest(
            self.root / "app.yaml",
            {"id": self.app_id, "backend": {"queue": "app.legacy-test.new"}},
            "b" * 64,
        )
        self.release = (
            self.root / ".runtime/app-package-releases" / self.app_id
            / (self.old_digest + ".zip")
        )
        self.release.parent.mkdir(parents=True)
        self.write_release(self.old_data)
        self.record = SimpleNamespace(
            app_id=self.app_id,
            deployment_digest=self.old_digest,
            readiness={"workers": ["worker"], "gateways": []},
        )
        for target, value in (
            ("bff.app_lifecycle.ROOT", self.root),
            ("bff.app_lifecycle.discover", lambda: {self.app_id: self.current}),
        ):
            mocked = patch(target, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch("src.core.celery_tasks.celery_app.celery")
        self.celery = mocked.start()
        self.addCleanup(mocked.stop)
        self.inspector = self.celery.control.inspect.return_value
        self.inspector.active_queues.return_value = {
            "worker": [{"name": "app.legacy-test.old"}],
            "shared": [{"name": "shared.old"}],
        }
        self.report = {"apps": {self.app_id: self.old_digest}, "tasks": ["legacy.task"]}
        self.celery.control.broadcast.return_value = [
            {"worker": self.report}, {"shared": self.report}
        ]
        self.inspector.active.return_value = {"worker": [], "shared": []}

    def write_release(self, data):
        with ZipFile(self.release, "w") as retained:
            retained.writestr("app.yaml", yaml.safe_dump(data))

    def test_changed_source_drains_deployed_digest_and_queue_contract(self):
        self.assertEqual(active_requests(self.record), 0)
        self.celery.control.inspect.assert_called_with(
            destination=["worker", "shared"], timeout=5
        )

    def test_old_owned_and_shared_active_tasks_still_block_drain(self):
        self.inspector.active.return_value = {
            "worker": [{"name": "legacy.task"}, {"name": "unrelated.task"}],
            "shared": [{"name": "execute_skill_task"}],
        }
        self.assertEqual(active_requests(self.record), 2)

    def test_drain_rejects_mismatched_missing_or_incomplete_worker_reports(self):
        cases = [
            ([], "mismatch"),
            ([{"shared": self.report}], "mismatch"),
            ([{"worker": self.report}], "mismatch"),
            ([{"worker": {**self.report, "apps": {self.app_id: self.current.digest}}}], "mismatch"),
            ([{"worker": {**self.report, "tasks": []}}], "registration incomplete"),
            (
                [{"worker": {**self.report, "dependency_errors": {self.app_id: "missing"}}}],
                "dependencies",
            ),
        ]
        for reports, message in cases:
            with self.subTest(message=message, reports=reports):
                self.celery.control.broadcast.return_value = reports
                with self.assertRaisesRegex(ValueError, message):
                    active_requests(self.record)

    def test_drain_requires_package_queue_and_complete_activity_inspection(self):
        self.inspector.active_queues.return_value = {"shared": [{"name": "shared.old"}]}
        with self.assertRaisesRegex(ValueError, "No Worker"):
            active_requests(self.record)
        self.inspector.active_queues.return_value["worker"] = [{"name": "app.legacy-test.old"}]
        self.inspector.active.return_value = {"worker": []}
        with self.assertRaisesRegex(ValueError, "activity inspection incomplete"):
            active_requests(self.record)

    def test_missing_or_mismatched_retained_manifest_fails_closed(self):
        self.write_release({**self.old_data, "id": "another-app"})
        with self.assertRaisesRegex(ValueError, "does not match"):
            active_requests(self.record)
        self.release.unlink()
        with self.assertRaises(FileNotFoundError):
            active_requests(self.record)
        self.celery.control.broadcast.assert_not_called()
