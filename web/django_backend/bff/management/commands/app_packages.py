import json
import time

from django.core.management.base import BaseCommand, CommandError

from bff.app_lifecycle import live_changes, operate
from bff.models import AppInstallation
from src.platform.kernel.registry import discover


class Command(BaseCommand):
    help = "Install/upgrade, verify deployment, enable, disable and drain capability packages."

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            choices=[
                "prepare",
                "install",
                "upgrade",
                "verify",
                "enable",
                "disable",
                "drain",
                "status",
            ],
        )
        parser.add_argument("app_id", nargs="?")
        parser.add_argument("--compact", action="store_true", help="Print concise App status")
        parser.add_argument(
            "--gateway-url", action="append", help="Repeat for every Gateway instance"
        )
        parser.add_argument("--frontend-url", help="Production frontend URL, with netops-apps.json")
        parser.add_argument(
            "--wait-seconds",
            type=int,
            default=0,
            help="Retry startup deployment probes while Worker/Beat become ready",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="drain: the grace period has expired; stop waiting for in-flight work",
        )

    def handle(self, *args, **options):
        if options["action"] == "status":
            if options["compact"]:
                for row in AppInstallation.objects.order_by("app_id"):
                    self.stdout.write(f"{row.app_id} {row.installed_version} {row.state}")
                return
            self.stdout.write(
                json.dumps(
                    list(
                        AppInstallation.objects.values(
                            "app_id",
                            "installed_version",
                            "state",
                            "desired_enabled",
                            "migration_head",
                            "deployment_digest",
                            "target_digest",
                            "error",
                            "readiness",
                        )
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if options["action"] == "prepare":
            self.prepare(options["app_id"])
            return
        if not options["app_id"] and options["action"] not in {"install", "upgrade", "verify"}:
            raise CommandError("Specify APP_ID")
        try:
            # Discovery already gives dependency order. Earlier successful package
            # migrations remain installed if a later independent package fails.
            manifests = discover()
            ids = [options["app_id"]] if options["app_id"] else list(manifests)
            if options["action"] == "verify" and not options["app_id"]:
                enabled = set(
                    AppInstallation.objects.filter(desired_enabled=True).values_list(
                        "app_id", flat=True
                    )
                )
                if enabled - manifests.keys():
                    raise ValueError("An enabled application package is missing from this release")
                ids = [key for key in ids if key in enabled]
                if not ids:
                    self.stdout.write("No enabled applications; use enable APP_ID explicitly.")
            for index, app_id in enumerate(ids, 1):
                if options["action"] in {"verify", "enable"}:
                    self.stdout.write(
                        f"[{index}/{len(ids)}] Checking {app_id}: "
                        "Gateway, frontend, Worker and Beat "
                        "(this can take tens of seconds)..."
                    )
                    self.stdout.flush()
                deadline = time.monotonic() + max(0, options["wait_seconds"])
                while True:
                    try:
                        record = operate(
                            options["action"],
                            app_id,
                            gateway_urls=options["gateway_url"],
                            frontend_url=options["frontend_url"],
                            force=options["force"] and options["action"] == "drain",
                        )
                        break
                    except Exception:
                        if (
                            options["action"] not in {"verify", "enable"}
                            or time.monotonic() >= deadline
                        ):
                            raise
                        self.stdout.write(f"Waiting for deployed application: {app_id}")
                        time.sleep(2)
                self.stdout.write(f"{record.app_id} {record.installed_version} {record.state}")
                if record.error:
                    self.stdout.write(f"  {record.error}")
        except Exception as exc:
            raise CommandError(
                str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            ) from exc

    def prepare(self, app_id=None):
        """Install discovered/inactive packages without changing grants or enablement."""
        manifests = discover()
        if app_id and app_id not in manifests:
            raise CommandError("Unknown application")
        records = {r.app_id: r for r in AppInstallation.objects.all()}
        ids = [app_id] if app_id else list(manifests)
        # A live package with changed code is upgraded only through disable/drain;
        # it stays closed meanwhile and never holds back the others.
        held = live_changes(records, manifests)
        if app_id in held:
            raise CommandError(f"{app_id}: {held[app_id]}")
        for key, reason in held.items():
            self.stdout.write(f"{key} skipped: {reason}")
        for key in ids:
            if key in held:
                continue
            row = records.get(key)
            if (
                row
                and row.deployment_digest == manifests[key].digest
                and row.state
                in {
                    "installed",
                    "disabled",
                    "enabled",
                }
            ):
                self.stdout.write(f"{key} {row.installed_version} {row.state} (unchanged)")
                continue
            try:
                row = operate("upgrade" if row else "install", key)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                if app_id:
                    raise CommandError(f"{key}: {reason}") from exc
                # In the startup sweep one package failing stays that package's problem.
                self.stdout.write(f"{key} failed: {reason}")
                continue
            self.stdout.write(f"{key} {row.installed_version} {row.state}")
