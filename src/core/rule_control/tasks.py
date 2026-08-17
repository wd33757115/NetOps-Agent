"""Celery entry points for unattended rule-asset production."""

from __future__ import annotations

from src.core.celery_tasks.celery_app import celery
from src.core.rule_control.automation import RuleAssetAutomationService


@celery.task(name="reconcile_rule_asset_automation")
def reconcile_rule_asset_automation_task() -> dict:
    return RuleAssetAutomationService().run_once()

