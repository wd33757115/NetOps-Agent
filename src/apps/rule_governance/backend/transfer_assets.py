"""Explicit portable asset schema and dependency reader for rule packages."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.infrastructure.db import models as m
from .parser.assets import TemplateAssetStore
from .transfer_format import MAX_ASSETS


@dataclass(frozen=True)
class Spec:
    model: type
    fields: str
    identity: str
    parent: str = ''
    stable: bool = False

    @property
    def columns(self):
        return self.fields.split()


# Only business data crosses the boundary. No status, approval, jobs or timestamps.
SPECS = {
    'check': Spec(m.CheckItemRecord, 'id name category description', 'id', stable=True),
    'check_version': Spec(m.CheckItemVersionRecord, 'check_item_id version result_mode assessment_mode canonical_contract_json report_projection applicability_json freshness_policy_json empty_result_policy_json coverage_policy_json', 'check_item_id', 'check_item_id'),
    'implementation': Spec(m.CollectionImplementationRecord, 'id name description', 'id', stable=True),
    'implementation_version': Spec(m.CollectionImplementationVersionRecord, 'implementation_id version collector match_json commands_json parser_requirement template_scope adapter_spec_json output_quality_policy_json selection_priority', 'implementation_id', 'implementation_id'),
    'parser': Spec(m.ParserTemplate, 'vendor model family profile canonical_command category version template_text validation_json', 'vendor profile canonical_command'),
    'policy': Spec(m.ChangePolicyRecord, 'name observation_type description', 'name'),
    'policy_version': Spec(m.ChangePolicyVersionRecord, 'policy_id check_item_version_id version observation_type semantic_type compare_mode policy_json', 'policy_id', 'policy_id'),
    'event': Spec(m.EventRuleRecord, 'name description', 'name'),
    'event_version': Spec(m.EventRuleVersionRecord, 'rule_id check_item_version_id version event_type severity condition_json priority exclusive event_class evaluation_scope recovery_json correlation_key_json', 'rule_id', 'rule_id'),
    'identity': Spec(m.EntityIdentityPolicyRecord, 'id name', 'id', stable=True),
    'identity_version': Spec(m.EntityIdentityPolicyVersionRecord, 'policy_id version policy_json', 'policy_id', 'policy_id'),
    'comparison': Spec(m.TextComparisonProfileRecord, 'id name', 'id', stable=True),
    'comparison_version': Spec(m.TextComparisonProfileVersionRecord, 'profile_id version mode profile_json', 'profile_id', 'profile_id'),
    'link': Spec(m.CheckItemImplementationLinkRecord, 'check_item_version_id implementation_version_id priority required', 'check_item_version_id implementation_version_id'),
    'parser_binding': Spec(m.CollectionImplementationParserBindingRecord, 'implementation_version_id canonical_command parser_template_id record_set binding_role output_signature', 'implementation_version_id canonical_command record_set binding_role'),
    'assessment': Spec(m.AssessmentPolicyProfileRecord, 'check_item_id observation_type policy_role name description', 'check_item_id observation_type policy_role name'),
    'assessment_version': Spec(m.AssessmentPolicyVersionRecord, 'profile_id check_item_version_id version policy_json', 'profile_id', 'profile_id'),
}
TABLE_KIND = {s.model.__tablename__: k for k, s in SPECS.items()}
VERSION_KINDS = {k for k, s in SPECS.items() if 'version' in s.columns}
PUBLIC_KINDS = ['check_version', 'parser', 'policy_version', 'event_version', 'implementation_version', 'comparison_version', 'identity_version', 'assessment_version']
LABELS = dict(zip(PUBLIC_KINDS, ['检查项', 'TextFSM', 'Change 策略', '状态 / 事件规则', '采集实现', '文本比较', '实体标识', '评估策略']))
PARSER_CONTRACT_FIELDS = frozenset('entity_type entity category primary_keys primary_key_defaults required_fields optional_fields validators allow_empty empty_patterns ignore_fields presence record_sets record_set_relations source_parser_spec parser_spec state_semantics textfsm_dialect'.split())
EMBEDDED = {'template_id': 'parser', 'parser_template_id': 'parser', 'check_item_id': 'check',
            'check_item_version_id': 'check_version', 'implementation_version_id': 'implementation_version',
            'comparison_profile_version_id': 'comparison_version', 'entity_identity_policy_version_id': 'identity_version'}


def fk_fields(kind):
    return {c.name: TABLE_KIND[fk.column.table.name] for c in SPECS[kind].model.__table__.columns
            for fk in c.foreign_keys if c.name in SPECS[kind].columns and fk.column.table.name in TABLE_KIND}


def row_data(kind, row):
    result = {name: getattr(row, name) for name in SPECS[kind].columns}
    if kind == 'parser':
        store = TemplateAssetStore()
        if row.template_text is None and not Path(row.asset_path).resolve().is_relative_to(store.root.resolve()):
            raise ValueError(f'Parser 文件不在模板目录：{row.id}')
        result['template_text'] = store.read(row.asset_path, row.sha256, row.template_text)
        result['validation_json'] = {k: v for k, v in (row.validation_json or {}).items() if k in PARSER_CONTRACT_FIELDS}
    return result


def transform(value, resolve, key=None):
    if isinstance(value, list):
        return [transform(v, resolve, key) for v in value]
    if isinstance(value, dict):
        return {k: transform(v, resolve, k) for k, v in value.items()}
    if key in EMBEDDED and value:
        return resolve(EMBEDDED[key], str(value))
    return value


class Graph:
    def __init__(self, db):
        self.db, self.assets, self.pending = db, {}, []
        self.recipes = {}

    def add(self, kind, ident):
        ref = f'{kind}:{ident}'
        if ref not in self.assets:
            if kind not in SPECS:
                raise ValueError(f'不支持的依赖类型：{kind}')
            row = self.db.get(SPECS[kind].model, ident)
            if row is None:
                raise ValueError(f'缺少依赖：{ref}')
            self.assets[ref] = None
            if len(self.assets) > MAX_ASSETS:
                raise ValueError('资产超过 5000 项，请缩小导出范围')
            self.pending.append((kind, row))
        return {'$ref': ref}

    def children(self, kind, field, ident):
        spec = SPECS[kind]
        for row in self.db.query(spec.model).filter(getattr(spec.model, field) == ident).all():
            # Frozen selected versions may depend on deprecated versions; preserve explicit links.
            self.add(kind, row.id)

    def close(self):
        while self.pending:
            kind, row = self.pending.pop(0)
            data = row_data(kind, row)
            for field, target in fk_fields(kind).items():
                if data[field] is not None:
                    data[field] = self.add(target, data[field])
            for field in ['adapter_spec_json', 'profile_json', 'policy_json', 'condition_json', 'canonical_contract_json']:
                if field in data:
                    data[field] = transform(data[field], self.add)
            self.assets[f'{kind}:{row.id}'] = {'ref': f'{kind}:{row.id}', 'kind': kind, 'data': data,
                                              'source_status': getattr(row, 'status', '')}
            if kind == 'check_version':
                for link in self.db.query(m.CheckItemImplementationLinkRecord).filter_by(check_item_version_id=row.id).all():
                    impl = self.db.get(m.CollectionImplementationVersionRecord, link.implementation_version_id)
                    if impl and (impl.status == 'active' or (row.status != 'active' and impl.status == 'draft')):
                        self.add('link', link.id)
                for child in ['policy_version', 'event_version', 'assessment_version']:
                    for version in self.db.query(SPECS[child].model).filter(
                            SPECS[child].model.check_item_version_id == row.id,
                            SPECS[child].model.status == ('active' if row.status == 'active' else 'draft')).all():
                        self.add(child, version.id)
                self.runtime_recipes(row.id)
            elif kind == 'implementation_version':
                for link in self.db.query(m.CheckItemImplementationLinkRecord).filter_by(implementation_version_id=row.id).all():
                    check = self.db.get(m.CheckItemVersionRecord, link.check_item_version_id)
                    if check and (check.status == 'active' or f'check_version:{check.id}' in self.assets):
                        self.add('link', link.id)
                self.children('parser_binding', 'implementation_version_id', row.id)
        return [self.assets[key] for key in sorted(self.assets)]

    def runtime_recipes(self, check_id):
        kinds = {'parser_template': 'parser',
                 'change_policy': 'policy_version', 'event_rule': 'event_version',
                 'current_state_rule': 'event_version', 'comparison_profile': 'comparison_version',
                 'identity_policy': 'identity_version'}
        for bundle in self.db.query(m.CheckItemRuntimeBundleVersionRecord).filter(
                m.CheckItemRuntimeBundleVersionRecord.check_item_version_id == check_id,
                m.CheckItemRuntimeBundleVersionRecord.status == 'active').all():
            if bundle.id in self.recipes:
                continue
            recipe = {'check': self.add('check_version', check_id),
                      'implementation': self.add('implementation_version', bundle.implementation_version_id),
                      'identity': self.add('identity_version', bundle.entity_identity_policy_version_id), 'assets': []}
            link = self.db.query(m.CheckItemImplementationLinkRecord).filter_by(check_item_version_id=check_id, implementation_version_id=bundle.implementation_version_id).first()
            if link is None:
                raise ValueError('运行包检查项与实现的关联缺失')
            self.add('link', link.id)
            if bundle.comparison_profile_version_id:
                recipe['comparison'] = self.add('comparison_version', bundle.comparison_profile_version_id)
            for asset in self.db.query(m.RuntimeBundleAssetRecord).filter_by(bundle_version_id=bundle.id).all():
                if asset.asset_type == 'command_adapter_projection':
                    continue  # Included in the immutable implementation.
                if asset.asset_type not in kinds:
                    raise ValueError(f'运行包包含不支持的依赖：{asset.asset_type}')
                recipe['assets'].append({'role': asset.asset_type, 'asset': self.add(kinds[asset.asset_type], asset.asset_version_id)})
            self.recipes[bundle.id] = recipe
