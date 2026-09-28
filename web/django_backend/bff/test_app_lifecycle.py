import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from sqlalchemy import create_engine

from src.platform.kernel.registry import discover

from .app_lifecycle import operate
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
