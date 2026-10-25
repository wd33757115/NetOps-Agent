"""Celery entry points for unattended rule-asset production."""

from __future__ import annotations

from src.apps.rule_governance.backend.access import maintenance

from src.core.celery_tasks.celery_app import celery




@celery.task(name="advance_command_autopilot")
def advance_command_autopilot(key: str) -> dict:
    from src.apps.rule_governance.backend.rule_control.command_autopilot import (
        CommandAutopilotService,
    )
    return CommandAutopilotService().advance(key)


@celery.task(name="recover_command_autopilots")
@maintenance
def recover_command_autopilots() -> dict:
    from src.apps.rule_governance.backend.rule_control.command_autopilot import (
        CommandAutopilotService,
    )
    return {"queued": CommandAutopilotService().recover()}
