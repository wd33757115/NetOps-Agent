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

    def test_changed_active_package_is_held_and_others_still_install(self):
        # Platform and application readiness are separate: one changed live
        # package stays closed until it is upgraded; it blocks nobody.
        self.record("enabled", "enabled", "old")
        self.record("disabled", "disabled")
        output = StringIO()
        installed = SimpleNamespace(app_id="new", installed_version="1.0.0", state="installed")
        with patch(COMMAND + ".operate", return_value=installed) as operation:
            call_command("app_packages", "prepare", stdout=output)
        operation.assert_called_once_with("install", "new")
        self.assertIn("enabled skipped: code changed since deployment", output.getvalue())
        self.assertEqual(AppInstallation.objects.get(pk="enabled").deployment_digest, "old")

    def test_draining_package_is_not_reset_by_startup(self):
        self.record("disabled", "draining")
        output = StringIO()
        with patch(COMMAND + ".operate", return_value=SimpleNamespace(
            app_id="x", installed_version="1.0.0", state="installed"
        )):
            call_command("app_packages", "prepare", stdout=output)
        self.assertIn("disabled skipped: draining", output.getvalue())
        self.assertEqual(AppInstallation.objects.get(pk="disabled").state, "draining")
        with self.assertRaisesMessage(CommandError, "draining"):
            self.command("prepare", app_id="disabled")

    def test_one_failing_package_does_not_stop_the_startup_sweep(self):
        output = StringIO()
        with patch(COMMAND + ".operate", side_effect=[
            ValueError("broken package"),
            SimpleNamespace(app_id="disabled", installed_version="1.0.0", state="installed"),
            SimpleNamespace(app_id="enabled", installed_version="1.0.0", state="installed"),
        ]):
            call_command("app_packages", "prepare", stdout=output)
        self.assertIn("new failed: broken package", output.getvalue())
        self.assertIn("enabled 1.0.0 installed", output.getvalue())

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
