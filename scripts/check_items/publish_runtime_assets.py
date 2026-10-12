"""Give the active version of a check item its rule assets and runtime bundles.

A catalog revision published by script gets a new version identity. Its change
policies, event rules and runtime bundles stay bound to the previous version
until they are produced for the new one; this runs the same steps as the
administrator-authorized automatic release.

Default is read-only: it prints the compiled rules and the adapters involved.
--apply creates and approves the rule bundle, then assembles and activates one
runtime bundle per adapter. It stops at the first blocker.
"""

import argparse
import json

from src.apps.rule_governance.backend.check_items.canonical_rule_context import (
    CanonicalRuleContextService,
)
from src.apps.rule_governance.backend.check_items.catalog import CheckItemCatalogRepository
from src.apps.rule_governance.backend.check_items.runtime_bundle import (
    CheckItemRuntimeBundleService,
)
from src.apps.rule_governance.backend.check_items.runtime_bundle_assembly import (
    event_rule_observation_types,
)
from src.apps.rule_governance.backend.rule_control.approval import RuleAssetApprovalService
from src.core.check_items.command_adapter import direct_projection_manifest
from src.infrastructure.db.models import (
    ChangePolicyVersionRecord,
    EventRuleVersionRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


def say(**values):
    print(json.dumps(values, ensure_ascii=False, default=str))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("check_item_id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor", default="catalog-revision")
    args = parser.parse_args()
    reason = f"为 {args.check_item_id} 当前版本补齐规则资产与运行包"

    catalog = CheckItemCatalogRepository().load_active()
    item = catalog.check_item(args.check_item_id)
    if item is None:
        raise SystemExit(f"active check item not found: {args.check_item_id}")
    adapters = [
        implementation
        for implementation in catalog.implementations
        if any(
            output.get("check_item_id") == item.id
            for output in (implementation.adapter_spec or {}).get("outputs", [])
        )
    ]
    rules = CanonicalRuleContextService()
    candidates = rules.generate_candidates(item.version_id)
    say(check_item=item.id, version=item.version, version_id=item.version_id,
        change_policies=[policy["observation_type"] for policy in candidates["change_policies"]],
        event_rules=[f"{rule['event_type']} ({rule['severity']}, {rule['evaluation_scope']})"
                     for rule in candidates["event_rules"]],
        blockers=candidates["blockers"], adapters=[adapter.id for adapter in adapters])
    if not candidates["success"]:
        raise SystemExit("rule candidates are blocked")
    if not args.apply:
        return

    drafts = rules.create_drafts(item.version_id, actor=args.actor)
    bundle_id = drafts.get("bundle_id")
    if bundle_id:
        with get_db_session() as db:
            active = db.get(RuleGenerationJobRecord, bundle_id).status == "active"
        if not active:
            RuleAssetApprovalService().approve(bundle_id, actor=args.actor, reason=reason, allow_same_actor=True)
    policy_ids = [asset["version_id"] for asset in drafts.get("change_policy_assets", [])]
    rule_ids = list(drafts.get("event_rule_version_ids", []))
    say(rule_bundle=bundle_id, change_policy_versions=policy_ids, event_rule_versions=rule_ids)

    runtime = CheckItemRuntimeBundleService()
    contract = {"version_id": item.version_id, "canonical_contract": item.canonical_contract}
    for adapter in adapters:
        produced = set(direct_projection_manifest(adapter.adapter_spec, check_item_id=item.id,
                                                  check_item=contract)["observation_types"])
        with get_db_session() as db:
            # A bundle carries only the rules for Values this adapter actually produces.
            selection = {
                "check_item_version_id": item.version_id,
                "implementation_version_id": adapter.version_id,
                "parser_template_ids": [adapter.adapter_spec["parser"]["template_id"]],
                "change_policy_version_ids": [
                    version_id for version_id in policy_ids
                    if db.get(ChangePolicyVersionRecord, version_id).observation_type in produced
                ],
                "event_rule_version_ids": [
                    version_id for version_id in rule_ids
                    if set(event_rule_observation_types(db.get(EventRuleVersionRecord, version_id))) & produced
                ],
            }
        preview = runtime.preview_assembly(**selection)
        if preview.get("blockers"):
            say(adapter=adapter.id, blockers=preview["blockers"])
            raise SystemExit("runtime bundle assembly is blocked")
        draft = runtime.assemble(**selection, preview_digest=preview["preview_digest"], actor=args.actor)
        active = runtime.activate(draft["version_id"], actor=args.actor, reason=reason, allow_same_actor=True)
        say(adapter=adapter.id, runtime_bundle=draft["version_id"],
            status=active.get("version_status", active.get("status")))


if __name__ == "__main__":
    main()
