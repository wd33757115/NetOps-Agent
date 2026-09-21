from django.conf import settings
from django.db import models


class DesktopPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    layout = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class AppInstallation(models.Model):
    app_id = models.SlugField(primary_key=True, max_length=100)
    installed_version = models.CharField(max_length=40)
    deployment_digest = models.CharField(max_length=64)
    desired_enabled = models.BooleanField(default=False)
    state = models.CharField(max_length=20, default="installed")
    error = models.TextField(blank=True)
    migration_head = models.CharField(max_length=40, blank=True)
    target_digest = models.CharField(max_length=64, blank=True)
    readiness = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


class AppGrant(models.Model):
    app = models.ForeignKey(AppInstallation, on_delete=models.CASCADE, related_name="grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    group = models.ForeignKey("auth.Group", null=True, blank=True, on_delete=models.CASCADE)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="app_grants_issued")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=(models.Q(user__isnull=False, group__isnull=True) | models.Q(user__isnull=True, group__isnull=False)), name="app_grant_one_principal"),
            models.UniqueConstraint(fields=["app", "user"], name="app_grant_unique_user"),
            models.UniqueConstraint(fields=["app", "group"], name="app_grant_unique_group"),
        ]
