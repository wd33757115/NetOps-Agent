import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, RequestFactory
from rest_framework_simplejwt.tokens import AccessToken
from .group_admin import groups, serialize_group, PROTECTED_NAMES
from .views.app_platform import administration, grant_data
from .models import AppGrant, AppInstallation
from .app_access import decision
from .roles import get_user_role
from .user_admin import bff_user_detail


class SystemManagementTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = get_user_model().objects.create_superuser(username='system-test-admin')
        self.user = get_user_model().objects.create_user(username='system-test-user')
        self.app, _ = AppInstallation.objects.get_or_create(app_id='inspection')

    def call(self, view, method='get', body=None, actor=None, **kwargs):
        req = getattr(self.factory, method)('/api/platform/groups/',
            data=json.dumps(body or {}) if method != 'get' else {}, content_type='application/json',
            HTTP_AUTHORIZATION='Bearer ' + str(AccessToken.for_user(actor or self.admin)))
        with patch('bff.decorators.token_is_revoked', return_value=False):
            return view(req, **kwargs)

    def group(self):
        response = self.call(groups, 'post', {'name': 'DC operators', 'member_ids': [self.user.id]})
        self.assertEqual(response.status_code, 201)
        return Group.objects.get(pk=json.loads(response.content)['data']['id'])

    def test_nonadmin_rejected_for_read_and_mutation(self):
        for method in ['get', 'post']:
            self.assertEqual(self.call(groups, method, actor=self.user).status_code, 403)
        self.assertEqual(self.call(administration, actor=self.user).status_code, 403)

    def test_reserved_groups_cannot_be_created_changed_or_deleted(self):
        for name in PROTECTED_NAMES:
            with self.subTest(name=name):
                self.assertEqual(self.call(groups, 'post', {'name': name.upper(), 'member_ids': []}).status_code, 400)
                group, _ = Group.objects.get_or_create(name=name)
                for method in ['patch', 'delete']:
                    self.assertEqual(self.call(groups, method, {'name': 'renamed', 'member_ids': [self.user.pk], 'revision': serialize_group(group)['revision']}, group_id=group.pk).status_code, 403)
        self.assertNotEqual(get_user_role(self.user), 'admin')

    @patch('bff.app_access.deployment_decision', return_value=200)
    def test_group_membership_changes_effective_access_and_preserves_direct_grants(self, _):
        group = self.group()
        AppGrant.objects.create(app=self.app, group=group)
        self.assertEqual(decision(self.user, self.app.pk), 200)
        self.assertEqual(self.call(groups, 'patch', {'revision': serialize_group(group)['revision'], 'member_ids': []}, group_id=group.pk).status_code, 200)
        self.assertEqual(decision(self.user, self.app.pk), 403)
        AppGrant.objects.create(app=self.app, user=self.user)
        self.assertEqual(self.call(groups, 'delete', {'revision': serialize_group(group)['revision']}, group_id=group.pk).status_code, 200)
        self.assertEqual(decision(self.user, self.app.pk), 200)
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_rename_preserves_members_and_rejects_stale_edits(self):
        group = self.group(); revision = serialize_group(group)['revision']
        self.assertEqual(self.call(groups, 'patch', {'name': 'new name', 'revision': revision}, group_id=group.pk).status_code, 200)
        self.assertTrue(group.user_set.filter(pk=self.user.pk).exists())
        self.assertEqual(self.call(groups, 'patch', {'name': 'lost update', 'revision': revision}, group_id=group.pk).status_code, 409)
        self.assertEqual(self.call(groups, 'delete', {'revision': revision}, group_id=group.pk).status_code, 409)

    def test_bad_member_does_not_create_or_partially_rename_group(self):
        for members in [[True], [987654321], 'all']:
            self.assertEqual(self.call(groups, 'post', {'name': 'bad-group', 'member_ids': members}).status_code, 400)
        self.assertFalse(Group.objects.filter(name='bad-group').exists())
        group = self.group()
        result = self.call(groups, 'patch', {'name': 'changed', 'member_ids': [987654321], 'revision': serialize_group(group)['revision']}, group_id=group.pk)
        self.assertEqual(result.status_code, 400)
        group.refresh_from_db(); self.assertEqual(group.name, 'DC operators')

    def test_grant_conflict_keeps_current_authorization(self):
        before = grant_data(self.app.pk)
        body = {'user_ids': [self.user.pk], 'group_ids': [], 'revision': before['revision']}
        self.assertEqual(self.call(administration, 'put', body, app_id=self.app.pk).status_code, 200)
        body['user_ids'] = []
        self.assertEqual(self.call(administration, 'put', body, app_id=self.app.pk).status_code, 409)
        self.assertTrue(AppGrant.objects.filter(app=self.app, user=self.user).exists())

    def test_catalog_preserves_inactive_users_and_exposes_sources(self):
        group = self.group()
        self.user.is_active = False; self.user.save()
        response = self.call(administration)
        data = json.loads(response.content)['data']
        user = next(u for u in data['users'] if u['id'] == self.user.pk)
        self.assertFalse(user['is_active']); self.assertIn(group.pk, user['group_ids'])
        self.assertTrue(all('revision' in app for app in data['items']))

    def test_duplicate_and_protected_renames_are_rejected(self):
        group = self.group()
        self.assertEqual(self.call(groups, 'post', {'name': 'dc OPERATORS', 'member_ids': []}).status_code, 400)
        self.assertEqual(self.call(groups, 'patch', {'name': 'ADMIN', 'revision': serialize_group(group)['revision']}, group_id=group.pk).status_code, 400)

    def test_rejected_self_disable_does_not_partially_change_role(self):
        get_user_model().objects.create_superuser(username='system-backup-admin')
        result = self.call(bff_user_detail, 'patch', {'role': 'operator', 'is_active': False}, user_id=self.admin.pk)
        self.assertEqual(result.status_code, 400)
        self.admin.refresh_from_db()
        self.assertEqual(get_user_role(self.admin), 'admin')
        self.assertTrue(self.admin.is_active)

    def test_last_admin_and_boolean_validation(self):
        self.assertEqual(self.call(bff_user_detail, 'patch', {'role': 'operator'}, user_id=self.admin.pk).status_code, 400)
        self.assertEqual(self.call(bff_user_detail, 'patch', {'is_active': 'false'}, user_id=self.user.pk).status_code, 400)
        self.assertEqual(self.call(bff_user_detail, 'delete', user_id=self.admin.pk).status_code, 400)
        self.user.refresh_from_db(); self.assertTrue(self.user.is_active)

    def test_netops_config_can_grant_revoke_but_cannot_manage_accounts_or_groups(self):
        from .roles import assign_netops_configuration, get_user_permissions
        assign_netops_configuration(self.user, True)
        self.assertEqual(get_user_permissions(self.user), ['apps.manage'])
        self.assertEqual(self.call(administration, actor=self.user).status_code, 200)
        revision = grant_data(self.app.pk)['revision']
        self.assertEqual(self.call(administration, 'put', {'user_ids': [self.user.pk], 'group_ids': [], 'revision': revision}, actor=self.user, app_id=self.app.pk).status_code, 200)
        self.assertEqual(self.call(administration, 'put', {'user_ids': [], 'group_ids': [], 'revision': grant_data(self.app.pk)['revision']}, actor=self.user, app_id=self.app.pk).status_code, 200)
        self.assertFalse(AppGrant.objects.filter(app=self.app).exists())
        self.assertEqual(self.call(bff_user_detail, 'patch', {'role': 'admin'}, actor=self.user, user_id=self.user.pk).status_code, 403)
        self.assertEqual(self.call(groups, 'post', {'name': 'escalation'}, actor=self.user).status_code, 403)
        assign_netops_configuration(self.user, False)
        self.assertEqual(self.call(administration, actor=self.user).status_code, 403)

    def test_system_group_members_can_change_without_renaming_or_deleting(self):
        group, _ = Group.objects.get_or_create(name='netops-config')
        for members in [[self.user.pk], []]:
            response = self.call(groups, 'patch', {'member_ids': members, 'revision': serialize_group(group)['revision']}, group_id=group.pk)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(list(group.user_set.values_list('pk', flat=True)), members)

    def test_admin_membership_promotes_and_removes_superuser_flags(self):
        group, _ = Group.objects.get_or_create(name='admin')
        self.admin.groups.add(group)
        response = self.call(groups, 'patch', {'member_ids': [self.admin.pk, self.user.pk], 'revision': serialize_group(group)['revision']}, group_id=group.pk)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db(); self.assertTrue(self.user.is_superuser)
        response = self.call(groups, 'patch', {'member_ids': [self.admin.pk], 'revision': serialize_group(group)['revision']}, group_id=group.pk)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db(); self.assertFalse(self.user.is_superuser)
        response = self.call(groups, 'patch', {'member_ids': [], 'revision': serialize_group(group)['revision']}, group_id=group.pk)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(group.user_set.filter(pk=self.admin.pk).exists())

    def test_paged_directory_search_selected_and_excluded_members(self):
        from .principals import directory
        User = get_user_model()
        User.objects.bulk_create([User(username=f'bulk-{i:03d}') for i in range(105)])
        response = self.call(directory, 'post', {'kind': 'user', 'search': 'bulk-', 'page': 2, 'page_size': 10})
        data = json.loads(response.content)['data']
        self.assertEqual(data['total'], 105)
        self.assertEqual(len(data['items']), 10)
        self.assertEqual(data['items'][0]['name'], 'bulk-010')
        selected = [row['id'] for row in data['items']]
        data = json.loads(self.call(directory, 'post', {'kind': 'user', 'ids': selected, 'search': 'bulk-012'}).content)['data']
        self.assertEqual(data['total'], 1)
        data = json.loads(self.call(directory, 'post', {'kind': 'user', 'exclude_ids': selected, 'search': 'bulk-'}).content)['data']
        self.assertEqual(data['total'], 95)
        self.assertEqual(self.call(directory, 'post', {'page_size': 10000}).status_code, 400)
        self.assertEqual(self.call(directory, 'post', actor=self.user).status_code, 403)

    def test_authorization_preview_retains_group_access_after_direct_removal(self):
        from .principals import directory
        group = self.group()
        data = json.loads(self.call(directory, 'post', {'kind': 'user', 'access': {'user_ids': [], 'group_ids': [group.pk]}}).content)['data']
        row = next(r for r in data['items'] if r['id'] == self.user.pk)
        self.assertEqual(row['sources'], ['用户组：DC operators'])
        data = json.loads(self.call(directory, 'post', {'kind': 'user', 'access': {'user_ids': [], 'group_ids': []}}).content)['data']
        self.assertNotIn(self.user.pk, [r['id'] for r in data['items']])

    def test_default_group_migration_preserves_existing_application_access(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        legacy, _ = Group.objects.get_or_create(name='operator')
        self.user.groups.add(legacy)
        AppGrant.objects.create(app=self.app, group=legacy)
        custom = self.group()
        migration = importlib.import_module('bff.migrations.0005_system_groups')
        from types import SimpleNamespace
        migration.migrate_groups(apps, SimpleNamespace(connection=connection))
        self.assertFalse(Group.objects.filter(name__in=['operator', 'viewer']).exists())
        self.assertTrue(Group.objects.filter(pk=custom.pk).exists())
        self.assertTrue(AppGrant.objects.filter(app=self.app, user=self.user).exists())
        self.assertTrue(self.admin.groups.filter(name='admin').exists())
        from .roles import assign_user_role
        assign_user_role(self.user, 'operator')
        self.assertFalse(Group.objects.filter(name__in=['operator', 'viewer']).exists())
