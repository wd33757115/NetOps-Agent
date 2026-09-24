"""Two built-in groups; retain legacy App grants when removing old role groups."""
from django.conf import settings
from django.db import migrations


def migrate_groups(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    Grant = apps.get_model('bff', 'AppGrant')
    admin, _ = Group.objects.using(alias).get_or_create(name='admin')
    Group.objects.using(alias).get_or_create(name='netops-config')
    for user in User.objects.using(alias).filter(is_superuser=True):
        user.groups.add(admin)
    User.objects.using(alias).filter(groups=admin).update(is_superuser=True, is_staff=True)
    for group in Group.objects.using(alias).filter(name__in=['operator', 'viewer']):
        members = list(User.objects.using(alias).filter(groups=group).values_list('pk', flat=True))
        for grant in Grant.objects.using(alias).filter(group=group):
            for user_id in members:
                Grant.objects.using(alias).get_or_create(app_id=grant.app_id, user_id=user_id,
                    defaults={'granted_by_id': grant.granted_by_id})
        group.delete(using=alias)


class Migration(migrations.Migration):
    dependencies = [('bff', '0004_device_management_grants')]
    operations = [migrations.RunPython(migrate_groups, migrations.RunPython.noop)]
