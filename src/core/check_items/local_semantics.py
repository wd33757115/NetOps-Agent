"""Validate CheckItem-owned semantics without modifying the shared template catalog."""

import json
import re
from typing import Any


def validate_local_definition(raw: Any, observation_id: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{observation_id}: 语义定义必须为对象")
    definition = dict(raw)
    definition['id'] = observation_id
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]*', observation_id):
        raise ValueError('观测语义标识必须以字母开头，仅包含字母、数字、点、下划线或连字符')
    if not str(definition.get('description') or '').strip():
        raise ValueError(f'{observation_id}: 请填写语义说明')
    kind = definition.get('semantic_type')
    if kind not in {'state', 'enum', 'boolean', 'text', 'gauge', 'counter', 'threshold'}:
        raise ValueError(f'{observation_id}: 不支持的语义类型')
    for key in ('value_types', 'assessment_roles', 'dimensions', 'allowed_values'):
        value = definition.get(key, [])
        if not isinstance(value, (list, tuple)) or any(isinstance(v, (dict, list)) for v in value):
            raise ValueError(f'{observation_id}: {key} 必须为标量数组')
        definition[key] = list(value)
        if key != 'allowed_values' and any(not isinstance(v, str) or not v.strip() for v in value):
            raise ValueError(f'{observation_id}: {key} 必须为非空文本数组')
    types = set(definition['value_types'])
    roles = set(definition['assessment_roles'])
    if not types or types - {'text', 'number', 'integer', 'float', 'percent', 'enum', 'boolean', 'interface_name', 'ip_or_unassigned'}:
        raise ValueError(f'{observation_id}: 不支持的值类型')
    if not roles or roles - {'evidence', 'current_state', 'change', 'reference'}:
        raise ValueError(f'{observation_id}: 请选择评估用途')
    if kind in {'gauge', 'counter', 'threshold'} and types - {'number', 'integer', 'float', 'percent'}:
        raise ValueError(f'{observation_id}: 数值语义需要数值类型')
    if kind == 'text' and 'current_state' in roles:
        raise ValueError(f'{observation_id}: 文本不能直接用于健康判断，请选择状态或数值语义')
    groups = definition.get('state_groups') or {}
    if not isinstance(groups, dict) or set(groups) - {'healthy', 'unhealthy', 'unknown', 'transitional'}:
        raise ValueError(f'{observation_id}: 状态分类无效')
    for values in groups.values():
        if not isinstance(values, (list, tuple)) or any(isinstance(v, (dict, list)) for v in values):
            raise ValueError(f'{observation_id}: 状态分类必须为标量数组')
    grouped = [json.dumps(v, sort_keys=True) for values in groups.values() for v in values]
    if len(grouped) != len(set(grouped)):
        raise ValueError(f'{observation_id}: 正常、异常、未知状态不能重复或交叉')
    allowed = {json.dumps(v, sort_keys=True) for v in definition['allowed_values']}
    if allowed and set(grouped) - allowed:
        raise ValueError(f'{observation_id}: 状态分类包含未声明的取值')
    if kind in {'state', 'enum', 'boolean'} and 'current_state' in roles:
        if not groups.get('healthy') or not groups.get('unhealthy'):
            raise ValueError(f'{observation_id}: 健康判断必须定义正常和异常状态')
    definition['state_groups'] = groups
    definition.setdefault('allowed_dimension_values', {})
    if not isinstance(definition['allowed_dimension_values'], dict) or set(definition['allowed_dimension_values']) - set(definition['dimensions']):
        raise ValueError(f'{observation_id}: 维度取值定义无效')
    if any(not isinstance(values, (list, tuple)) or any(not isinstance(v, str) for v in values) for values in definition['allowed_dimension_values'].values()):
        raise ValueError(f'{observation_id}: 维度取值必须为文本数组')
    definition['canonical_unit'] = str(definition.get('canonical_unit') or '').strip().lower() or None
    keys = {'id', 'description', 'semantic_type', 'value_types', 'canonical_unit', 'dimensions', 'allowed_dimension_values', 'allowed_values', 'state_groups', 'assessment_roles'}
    return {key: definition[key] for key in keys}


def validate_local_references(rows: list) -> None:
    by_field = {row.get('canonical_field'): row for row in rows}
    for row in rows:
        if 'semantic_definition' not in row:
            continue
        references = row.get('threshold_references') or {}
        constraints = row.get('policy_constraints') or {}
        if not isinstance(references, dict) or not isinstance(constraints, dict):
            raise ValueError(f"{row['canonical_field']}: 阈值引用和比较约束必须为对象")
        if row['semantic_type'] in {'gauge', 'counter', 'threshold'} and 'current_state' in row.get('assessment_roles', []) and not (references or row.get('health_policy') or constraints.get('required_thresholds')):
            raise ValueError(f"{row['canonical_field']}: 数值健康判断必须配置阈值字段或明确的健康策略，不使用隐含固定阈值")
        for role, reference in references.items():
            target = by_field.get(reference.get('field_name')) if isinstance(reference, dict) else None
            if role not in {'high', 'low', 'critical_high', 'critical_low'} or target is None or target is row:
                raise ValueError(f"{row['canonical_field']}: 阈值必须引用当前检查项的其他数值字段")
            if target.get('value_type') not in {'number', 'integer', 'float', 'percent'}:
                raise ValueError(f"{row['canonical_field']}: 阈值字段必须为数值类型")
            if (row.get('unit') or '').lower() != (target.get('unit') or '').lower():
                raise ValueError(f"{row['canonical_field']}: 阈值字段单位必须一致")
        modes = constraints.get('allowed_compare_modes', [])
        supported = {'state': {'state_transition', 'enum_transition', 'exact'}, 'enum': {'state_transition', 'enum_transition', 'exact'}, 'boolean': {'state_transition', 'enum_transition', 'exact'}, 'text': {'exact', 'hash_diff'}, 'gauge': {'numeric_delta', 'threshold_and_deadband', 'threshold_low_and_deadband', 'dimension_dispatch'}, 'threshold': {'numeric_delta', 'threshold_and_deadband', 'threshold_low_and_deadband'}, 'counter': {'counter_delta'}}
        if not isinstance(modes, list) or set(modes) - supported[row['semantic_type']]:
            raise ValueError(f"{row['canonical_field']}: 比较方式与语义类型不匹配")


def derive_assessment_mode(contract: dict) -> str:
    """The check item judges health if any Value does, else change, else nothing."""
    roles = {role for row in contract.get('observations') or [] for role in row.get('assessment_roles') or []}
    return 'health' if 'current_state' in roles else 'change' if 'change' in roles else 'evidence'


_TREND_KINDS = {
    'utilization_increase': ('percent', '利用率上升', '百分比（percent）'),
    'linear_power_drop': ('dbm', '光功率下降', 'dBm'),
}


def compile_health_policy(row: dict) -> None:
    """A numeric health Value is judged by the limits the device itself reports.

    The form owns the threshold fields and the optional trend; everything the
    runtime reads (``health_policy``) is derived here and never hand-written.
    """
    field = row.get('canonical_field')
    kind, roles = row.get('semantic_type'), row.get('assessment_roles') or []
    submitted = row.pop('health_policy', None)
    if 'current_state' not in roles or kind != 'gauge':
        return
    references = row.get('threshold_references') or {}
    limits = {}
    for side, keys in (('high', ('high', 'critical_high')), ('low', ('low', 'critical_low'))):
        fields = [references[key]['field_name'] for key in keys if key in references]
        if fields:
            limits[side] = list(dict.fromkeys(fields))
    if not limits:
        raise ValueError(f'{field}: 数值健康判断必须选择设备输出的阈值字段')
    policy = {'baseline_mode': 'device_limits', 'device_thresholds': limits}
    window = (row.get('fixed_dimensions') or {}).get('window')
    if window == '5m':
        policy['window'] = window
    trend = submitted.get('trend') if isinstance(submitted, dict) else None
    identity_fields = []
    if trend:
        if not isinstance(trend, dict) or trend.get('kind') not in _TREND_KINDS:
            raise ValueError(f'{field}: 趋势提醒类型无效')
        unit, label, unit_label = _TREND_KINDS[trend['kind']]
        if (row.get('unit') or '').lower() != unit:
            raise ValueError(f'{field}: 趋势提醒“{label}”只适用于单位为 {unit_label} 的字段')
        identity_fields = list(trend.get('identity_fields') or [])
        policy.update(baseline_mode='trend_only', trend=trend)
    from .numeric_health import validate_health_policy
    errors = validate_health_policy(policy)
    if errors:
        raise ValueError(f'{field}: ' + '; '.join(errors))
    row['health_policy'] = policy
    # The runtime reads limits and module identity from the same record.
    needed = [name for fields in limits.values() for name in fields] + identity_fields
    row['context_fields'] = list(dict.fromkeys([*(row.get('context_fields') or []), *needed]))
