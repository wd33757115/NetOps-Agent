from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from .app_access import decision
from .models import AppGrant, AppInstallation


class DeviceAppAdmissionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='device-app-test')
        self.rules, _ = AppInstallation.objects.get_or_create(app_id='rule-governance')
        self.devices, _ = AppInstallation.objects.get_or_create(app_id='device-management')

    @patch('bff.app_access.deployment_decision', return_value=200)
    def test_device_grant_is_independent_of_rules_and_inspection(self, _):
        AppGrant.objects.create(app=self.devices, user=self.user)
        self.assertEqual(decision(self.user, 'device-management'), 200)
        self.assertEqual(decision(self.user, 'rule-governance'), 403)
        self.assertEqual(decision(self.user, 'inspection'), 403)
        AppGrant.objects.filter(app=self.devices).delete()
        self.assertEqual(decision(self.user, 'device-management'), 403)

    def test_migration_preserves_direct_and_group_grants_once_without_enabling(self):
        group = Group.objects.create(name='asset-maintainers-test')
        AppGrant.objects.create(app=self.rules, user=self.user)
        AppGrant.objects.create(app=self.rules, group=group)
        migration = import_module('bff.migrations.0004_device_management_grants')
        editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        migration.preserve_device_grants(apps, editor)
        migration.preserve_device_grants(apps, editor)
        self.assertEqual(AppGrant.objects.filter(app=self.devices).count(), 2)
        self.devices.refresh_from_db()
        self.assertFalse(self.devices.desired_enabled)

    @patch('bff.app_access.deployment_decision', return_value=404)
    def test_disabled_app_rejects_preexisting_grant(self, _):
        AppGrant.objects.create(app=self.devices, user=self.user)
        self.assertEqual(decision(self.user, 'device-management'), 404)
