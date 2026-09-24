"""Preserve existing device-maintainer access once when separating the App."""
from django.db import migrations


def preserve_device_grants(apps, schema_editor):
    Installation = apps.get_model('bff', 'AppInstallation')
    Grant = apps.get_model('bff', 'AppGrant')
    alias = schema_editor.connection.alias
    device, _ = Installation.objects.using(alias).get_or_create(
        app_id='device-management',
        defaults={'installed_version': '', 'deployment_digest': '', 'state': 'discovered'},
    )
    for grant in Grant.objects.using(alias).filter(app_id='rule-governance').iterator():
        Grant.objects.using(alias).get_or_create(
            app_id=device.pk, user_id=grant.user_id, group_id=grant.group_id,
            defaults={'granted_by_id': grant.granted_by_id},
        )


class Migration(migrations.Migration):
    dependencies = [('bff', '0003_app_package_lifecycle')]
    operations = [migrations.RunPython(preserve_device_grants, migrations.RunPython.noop)]
