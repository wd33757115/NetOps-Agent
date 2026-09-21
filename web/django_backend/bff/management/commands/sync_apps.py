"""Install manifest metadata without granting access or touching business tables."""

from django.core.management.base import BaseCommand, CommandError
from bff.models import AppInstallation, AppGrant
from bff.roles import get_user_role
from django.contrib.auth import get_user_model
from src.platform.kernel.registry import discover
from bff.app_lifecycle import operate


class Command(BaseCommand):
    help = "Validate/discover deployed application packages (disabled by default)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--grant-legacy-document-writers",
            action="store_true",
            help="Explicitly preserve document access for existing active admin/operator users; never grant viewers.",
        )
        parser.add_argument(
            "--enable", metavar="APP_ID", help="Operator confirms this deployed package is ready."
        )
        parser.add_argument(
            "--disable",
            metavar="APP_ID",
            help="Stop new access; in-flight work drains with code retained.",
        )
        parser.add_argument('--gateway-url', action='append')
        parser.add_argument('--frontend-url')

    def handle(self, *args, **options):
        manifests = discover()
        for key in ("enable", "disable"):
            if options[key] and options[key] not in manifests:
                raise CommandError("Unknown application")
        for m in manifests.values():
            record, created = AppInstallation.objects.get_or_create(
                app_id=m.id,
                defaults={"installed_version": '', "deployment_digest": '', 'state': 'discovered'},
            )
            # Discovery never overwrites the successfully installed version.
            # Install/upgrade owns migration and deployment state transitions.
            if options["enable"] == m.id:
                try:
                    record = operate('enable', m.id, gateway_urls=options['gateway_url'], frontend_url=options['frontend_url'])
                except Exception as exc:
                    raise CommandError(str(exc)) from exc
            if options["disable"] == m.id:
                try:
                    record = operate('disable', m.id)
                except Exception as exc:
                    raise CommandError(str(exc)) from exc
            self.stdout.write(f"{m.id} {record.installed_version} {record.state}")
        if options["grant_legacy_document_writers"]:
            app = AppInstallation.objects.get(pk="official-documents")
            count = 0
            for user in get_user_model().objects.filter(is_active=True):
                if get_user_role(user) == "operator":
                    _, created = AppGrant.objects.get_or_create(app=app, user=user)
                    count += int(created)
            self.stdout.write(
                f"Preserved existing document writer access: {count} users; viewers unchanged."
            )
