"""Resolve reviewed command semantics before producing a ParserSpec or TextFSM."""

from contextlib import nullcontext

from src.apps.rule_governance.backend.check_items.catalog import CheckItemCatalogRepository
from src.infrastructure.db.models import OfflinePatrolJobRecord, RuleAssetCommandUnitRecord


class CheckItemBindingError(ValueError):
    pass


def require_parser_check_item(db, job_id: str, *, requested_id: str = "") -> dict:
    # Import lazily: the command projection itself uses the Parser repository.
    from src.apps.rule_governance.backend.rule_control.command_asset_units import (
        RuleAssetCommandUnitService,
    )

    rows = (
        db.query(RuleAssetCommandUnitRecord, OfflinePatrolJobRecord)
        .join(OfflinePatrolJobRecord, OfflinePatrolJobRecord.id == RuleAssetCommandUnitRecord.offline_job_id)
        .filter(RuleAssetCommandUnitRecord.parser_job_id == job_id)
        .all()
    )
    if not rows:
        raise CheckItemBindingError("请先在待归类检查项中关联检查项，再制作 TextFSM")
    catalog = CheckItemCatalogRepository(session_scope=lambda: nullcontext(db)).load_active()
    items = {item.id: item for item in catalog.check_items}
    bindings = []
    for unit, source in rows:
        if source.purpose != "rule_asset_source" or source.status != "completed":
            raise CheckItemBindingError("请先完成 CLI 样本导入与处理，再制作 TextFSM")
        association = RuleAssetCommandUnitService._preproduction_association_for_row(unit, source) or {}
        item_id = association.get("check_item_id") or unit.check_item_id
        item = items.get(item_id)
        if item is None:
            raise CheckItemBindingError("请先在待归类检查项中关联有效检查项，再制作 TextFSM")
        if association and str(association.get("check_item_version_id") or "") != str(item.version_id):
            raise CheckItemBindingError("检查项语义版本已变化，请在待归类检查项中重新确认关联")
        if item.result_mode != "structured":
            raise CheckItemBindingError("文本检查项不需要 TextFSM，请创建文本命令适配器")
        bindings.append({"check_item_id": item.id, "version_id": item.version_id, "digest": item.digest})
    binding = bindings[0]
    if any(value != binding for value in bindings[1:]):
        raise CheckItemBindingError("样本关联了不同检查项，请先统一关联再制作 TextFSM")
    if requested_id and requested_id != binding["check_item_id"]:
        raise CheckItemBindingError("所选检查项与样本关联不一致，请先在待归类检查项中修改关联")
    return binding
