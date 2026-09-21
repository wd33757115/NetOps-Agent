"""Startup may reconcile code, but never infer application enablement or grants."""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import AppInstallation

COMMAND = "bff.management.commands.app_packages"


class AppStartupTests(TestCase):
    def setUp(self):
        self.manifests = {
            key: SimpleNamespace(digest="current") for key in ("new", "disabled", "enabled")
        }
        self.discovery = patch(COMMAND + ".discover", return_value=self.manifests)
        self.discovery.start()
        self.addCleanup(self.discovery.stop)

    def record(self, key, state, digest="current"):
        return AppInstallation.objects.create(
            app_id=key,
            state=state,
            desired_enabled=state == "enabled",
            installed_version="1.0.0",
            deployment_digest=digest,
        )

    def command(self, action, **options):
        call_command("app_packages", action, stdout=StringIO(), **options)

    def test_prepare_installs_only_missing_and_preserves_enabled_and_disabled(self):
        self.record("disabled", "disabled")
        self.record("enabled", "enabled")
        with patch(
            COMMAND + ".operate",
            return_value=SimpleNamespace(
                app_id="new",
                installed_version="1.0.0",
                state="installed",
            ),
        ) as operation:
            self.command("prepare")
        operation.assert_called_once_with("install", "new")
        self.assertEqual(AppInstallation.objects.get(pk="disabled").state, "disabled")
        self.assertTrue(AppInstallation.objects.get(pk="enabled").desired_enabled)

    def test_prepare_upgrades_inactive_package_without_enabling(self):
        row = self.record("disabled", "disabled", "old")
        with patch(COMMAND + ".operate", return_value=row) as operation:
            self.command("prepare", app_id="disabled")
        operation.assert_called_once_with("upgrade", "disabled")
        self.assertFalse(row.desired_enabled)

    def test_changed_active_package_blocks_before_any_install(self):
        self.record("enabled", "enabled", "old")
        with patch(COMMAND + ".operate") as operation:
            with self.assertRaisesMessage(CommandError, "disable/drain"):
                self.command("prepare")
        operation.assert_not_called()

    def test_draining_package_is_not_reset_by_startup(self):
        self.record("disabled", "draining")
        with self.assertRaisesMessage(CommandError, "disable/drain"):
            self.command("prepare")

    def test_verify_checks_enabled_only_and_retries_starting_worker(self):
        self.record("disabled", "disabled")
        row = self.record("enabled", "enabled")
        with (
            patch(COMMAND + ".operate", side_effect=[ValueError("Worker not ready"), row]) as op,
            patch(COMMAND + ".time.sleep"),
        ):
            self.command(
                "verify",
                wait_seconds=5,
                gateway_url=["http://gateway"],
                frontend_url="http://frontend",
            )
        self.assertEqual(op.call_count, 2)
        for call in op.call_args_list:
            self.assertEqual(call.args, ("verify", "enabled"))
            self.assertEqual(call.kwargs["gateway_urls"], ["http://gateway"])

    def test_verify_fails_if_enabled_package_is_missing(self):
        self.record("missing", "enabled")
        with self.assertRaisesMessage(CommandError, "missing"):
            self.command("verify")

    def test_no_enabled_app_is_valid_and_does_not_call_probe(self):
        with patch(COMMAND + ".operate") as operation:
            self.command("verify")
        operation.assert_not_called()
