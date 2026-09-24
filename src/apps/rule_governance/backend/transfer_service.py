"""Rule exchange: dependency closure, deterministic preview and atomic draft import."""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import Boolean, Integer, String, text

from src.infrastructure.db import models as m
from src.infrastructure.db.postgres import get_db_session
from .transfer_assets import Graph, SPECS, PUBLIC_KINDS, VERSION_KINDS, LABELS, EMBEDDED, PARSER_CONTRACT_FIELDS, row_data, fk_fields
from .transfer_format import canonical, digest, pack, unpack


class TransferConflict(ValueError):
    pass


def refs(value):
    if isinstance(value, dict):
        if '$ref' in value:
            if set(value) != {'$ref'} or not isinstance(value['$ref'], str):
                raise ValueError('包内引用格式无效')
            return {value['$ref']}
        return set().union(*(refs(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(refs(v) for v in value)) if value else set()
    return set()


def resolve(value, mapping):
    if isinstance(value, dict):
        if '$ref' in value:
            return mapping[value['$ref']]
        return {k: resolve(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, mapping) for v in value]
    return value


def ordered(package):
    assets = package['assets']
    by_ref = {a['ref']: a for a in assets}
    dependencies = {}
    def embedded(value):
        if isinstance(value, list):
            for v in value:
                embedded(v)
        elif isinstance(value, dict):
            for key, val in value.items():
                if key in EMBEDDED and val:
                    if not isinstance(val, dict) or set(val) != {'$ref'} or by_ref.get(val['$ref'], {}).get('kind') != EMBEDDED[key]:
                        raise ValueError(f'{key} 必须使用包内引用，不能引用源环境或目标环境的裸 ID')
                else:
                    embedded(val)
    for asset in assets:
        kind, data = asset['kind'], asset['data']
        if kind not in SPECS or set(data) != set(SPECS[kind].columns):
            raise ValueError(f'不支持的资产类型或字段：{kind}')
        if kind == 'parser' and (not isinstance(data['validation_json'], dict) or set(data['validation_json']) - PARSER_CONTRACT_FIELDS):
            raise ValueError('Parser 只允许携带字段契约，不能携带审批或生效证明')
        for field, target in fk_fields(kind).items():
            val = data[field]
            if val is not None and (not isinstance(val, dict) or set(val) != {'$ref'}
                                    or by_ref.get(val['$ref'], {}).get('kind') != target):
                raise ValueError(f'{asset["ref"]}.{field} 必须引用包内 {target}')
        needed = refs(data)
        if needed - set(by_ref):
            raise ValueError(f'缺少依赖：{sorted(needed - set(by_ref))}')
        dependencies[asset['ref']] = needed
        for field in ['adapter_spec_json', 'profile_json', 'policy_json', 'condition_json', 'canonical_contract_json']:
            if field in data:
                embedded(data[field])
    for field in ['recipes', 'samples']:
        if not isinstance(package[field], list) or refs(package[field]) - set(by_ref):
            raise ValueError(f'{field} 依赖缺失或格式错误')
    def require_ref(value, kind):
        if not isinstance(value, dict) or set(value) != {'$ref'} or by_ref.get(value['$ref'], {}).get('kind') != kind:
            raise ValueError(f'必须引用包内 {kind} 资产')
    recipe_roles = {'parser_template': 'parser', 'change_policy': 'policy_version',
                    'event_rule': 'event_version', 'current_state_rule': 'event_version', 'comparison_profile': 'comparison_version', 'identity_policy': 'identity_version'}
    for recipe in package['recipes']:
        if not isinstance(recipe, dict) or set(recipe) - {'check', 'implementation', 'identity', 'assets', 'comparison'} or not isinstance(recipe.get('assets'), list):
            raise ValueError('运行包装配关系格式无效')
        for key, kind in [('check', 'check_version'), ('implementation', 'implementation_version'), ('identity', 'identity_version')]:
            require_ref(recipe.get(key), kind)
        if 'comparison' in recipe:
            require_ref(recipe['comparison'], 'comparison_version')
        for asset in recipe['assets']:
            if not isinstance(asset, dict) or set(asset) != {'role', 'asset'} or asset['role'] not in recipe_roles:
                raise ValueError('运行包依赖类型无效')
            require_ref(asset['asset'], recipe_roles[asset['role']])
    for sample in package['samples']:
        if not isinstance(sample, dict) or set(sample) != {'parser', 'text', 'expected'}:
            raise ValueError('样本结构无效')
        require_ref(sample['parser'], 'parser')
    result, seen = [], set()
    while len(result) < len(assets):
        ready = [a for a in assets if a['ref'] not in seen and dependencies[a['ref']] <= seen]
        if not ready:
            raise ValueError('资产包含循环依赖')
        for asset in ready:
            result.append(asset)
            seen.add(asset['ref'])
    return result


def validate_columns(kind, data):
    model = SPECS[kind].model
    for name, val in data.items():
        col = model.__table__.columns[name]
        if val is None:
            if not col.nullable:
                raise ValueError(f'{kind}.{name} 不能为空')
        elif isinstance(col.type, Boolean):
            if not isinstance(val, bool):
                raise ValueError(f'{kind}.{name} 必须是布尔值')
        elif isinstance(col.type, Integer):
            if type(val) is not int or (name == 'version' and val < 1):
                raise ValueError(f'{kind}.{name} 必须是有效整数')
        elif isinstance(col.type, String):
            if not isinstance(val, str) or (col.type.length and len(val) > col.type.length):
                raise ValueError(f'{kind}.{name} 类型或长度无效')


def parse_sample(template, sample):
    import io
    import textfsm
    from .parser.parser_bundle import parse_parser_bundle
    bundle = parse_parser_bundle(template)
    templates = {r.name: r.template_text for r in bundle.record_sets} if bundle else {'default': template}
    return {name: textfsm.TextFSM(io.StringIO(content)).ParseTextToDicts(sample) for name, content in templates.items()}


def validate_content(package):
    """Called in a bounded subprocess; regex compilation/replay cannot block Gateway."""
    from .parser.field_contract import textfsm_field_sets, build_parser_field_contract
    from src.core.patrol.change_policy import policy_from_dict, validate_change_policy
    from src.core.patrol.event_rules import govern_evaluation_scope
    from src.core.patrol.rule_validation import validate_event_rule_condition
    ordered(package)
    reports = []
    for asset in package['assets']:
        kind, data = asset['kind'], asset['data']
        if kind == 'parser':
            if not isinstance(data['template_text'], str) or len(data['template_text']) > 512000:
                raise ValueError('单个 Parser 内容不能超过 512000 字符')
            try:
                fields = textfsm_field_sets(data['template_text'])
            except Exception as exc:
                raise ValueError(f'{asset["ref"]}: TextFSM 编译失败：{exc}') from exc
            contract = build_parser_field_contract(validation=data['validation_json'], template_text=data['template_text'])
            if contract['status'] in {'schema_drift', 'template_invalid'}:
                raise ValueError(f'{asset["ref"]}: Parser 字段契约与 TextFSM 不一致')
            reports.append({'ref': asset['ref'], 'check': 'TextFSM 编译', 'status': 'passed', 'fields': fields})
        elif kind == 'policy_version':
            report = validate_change_policy(policy_from_dict(data['policy_json']))
            if report.get('errors'):
                raise ValueError(f'{asset["ref"]}: {report["errors"]}')
            reports.append({'ref': asset['ref'], 'check': 'Change 规则结构', 'status': 'passed'})
        elif kind == 'event_version':
            govern_evaluation_scope(data['evaluation_scope'], event_class=data['event_class'])
            if not isinstance(data['condition_json'], dict) or not data['condition_json']:
                raise ValueError(f'{asset["ref"]}: 事件条件不能为空')
            errors = validate_event_rule_condition(data['condition_json'])
            if errors:
                raise ValueError(f'{asset["ref"]}: {errors}')
            reports.append({'ref': asset['ref'], 'check': '事件规则 DSL', 'status': 'passed'})
    by_ref = {a['ref']: a for a in package['assets']}
    if len(package['samples']) > 200:
        raise ValueError('单包最多携带 200 份验证样本')
    for sample in package['samples']:
        if not isinstance(sample, dict) or set(sample) != {'parser', 'text', 'expected'}:
            raise ValueError('样本结构无效')
        parser = by_ref.get(sample.get('parser', {}).get('$ref'), {})
        if parser.get('kind') != 'parser' or not isinstance(sample['text'], str) or len(sample['text']) > 256000:
            raise ValueError('样本 Parser 或文本无效')
        actual = parse_sample(parser['data']['template_text'], sample['text'])
        if actual != sample['expected']:
            raise ValueError(f'{parser["ref"]}: 样本回放与包内预期不一致')
        reports.append({'ref': parser['ref'], 'check': '样本回放', 'status': 'passed', 'records': sum(len(v) for v in actual.values())})
    return reports


def bounded_validation(package):
    import subprocess
    import sys
    try:
        run = subprocess.run([sys.executable, '-m', 'src.apps.rule_governance.backend.transfer_validator'],
                             input=canonical(package), capture_output=True, timeout=25,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as exc:
        raise ValueError('规则验证超过 25 秒，请缩小包或检查模板表达式') from exc
    if run.returncode:
        raise ValueError('规则验证进程失败，请检查规则包')
    result = json.loads(run.stdout)
    if result.get('error'):
        raise ValueError(result['error'])
    return result['reports']


class TransferService:
    def __init__(self, session_scope=get_db_session, validator=bounded_validation):
        self.scope, self.validator = session_scope, validator

    def catalog(self, kind='check_version', status='active', search='', offset=0, limit=50):
        if kind not in PUBLIC_KINDS or status not in {'active', 'draft', 'all'}:
            raise ValueError('资产类型或状态无效')
        spec = SPECS[kind]
        with self.scope() as db:
            query = db.query(spec.model)
            if status != 'all':
                query = query.filter(spec.model.status == status)
            # Bounded UI catalog; SQL search keeps a large repository responsive.
            if search:
                from sqlalchemy import String, cast, or_
                expressions = [cast(getattr(spec.model, f), String).ilike(f'%{search}%') for f in spec.identity.split()]
                if spec.parent:
                    root_model = SPECS[fk_fields(kind)[spec.parent]].model
                    query = query.join(root_model, root_model.id == getattr(spec.model, spec.parent))
                    expressions.append(root_model.name.ilike(f'%{search}%'))
                query = query.filter(or_(*expressions))
            total = query.count()
            rows = query.order_by(spec.model.id).offset(offset).limit(limit).all()
            items = []
            for row in rows:
                name = getattr(row, 'canonical_command', None) or getattr(row, 'schema_id', None)
                if spec.parent:
                    target = fk_fields(kind)[spec.parent]
                    root = db.get(SPECS[target].model, getattr(row, spec.parent))
                    name = root.name if root else None
                items.append({'ref': f'{kind}:{row.id}', 'kind': kind, 'name': name or row.id,
                              'version': row.version, 'status': row.status,
                              'scope': ' / '.join(str(getattr(row, f, '') or '') for f in ('vendor', 'profile', 'observation_type')) .strip(' /')})
            return {'items': items, 'total': total, 'kinds': LABELS}

    def export(self, selections, *, all_active=False, include_drafts=False, include_samples=False, actor=''):
        with self.scope() as db:
            if all_active:
                selections = [f'{kind}:{r.id}' for kind in PUBLIC_KINDS for r in db.query(SPECS[kind].model).filter(SPECS[kind].model.status == 'active').all()]
            if not selections:
                raise ValueError('请选择需要导出的资产')
            graph = Graph(db)
            for ref in dict.fromkeys(selections):
                kind, ident = ref.split(':', 1)
                if kind not in PUBLIC_KINDS:
                    raise ValueError('请选择检查项或规则资产版本')
                row = db.get(SPECS[kind].model, ident)
                if row is None or (row.status != 'active' and not include_drafts):
                    raise ValueError(f'{ref} 不是生效版本，请勾选包含未发布版本')
                graph.add(kind, ident)
            assets = graph.close()
            samples = []
            if include_samples:
                for asset in assets:
                    if asset['kind'] != 'parser':
                        continue
                    parser_id = asset['ref'].split(':', 1)[1]
                    rows = db.query(m.ParserJobSample).join(m.ParserCandidateSampleEvidence, m.ParserCandidateSampleEvidence.sample_id == m.ParserJobSample.id).filter(
                        m.ParserCandidateSampleEvidence.template_id == parser_id,
                        m.ParserJobSample.lifecycle_status == 'current').all()
                    for sample in rows:
                        if sample.sanitized_text and sample.sanitized_hash == hashlib.sha256(sample.sanitized_text.encode()).hexdigest():
                            samples.append({'parser': {'$ref': asset['ref']}, 'text': sample.sanitized_text, 'expected': {}})
                if len(samples) > 200:
                    raise ValueError('所选资产超过 200 份样本，请缩小导出范围')
            # Source expectations are recomputed by the same bounded parser runner.
            if samples:
                from .transfer_validator import baseline_samples
                samples = baseline_samples(assets, samples)
            package = {'assets': assets, 'roots': list(dict.fromkeys(selections)), 'recipes': list(graph.recipes.values()), 'samples': samples}
            ordered(package)
            raw = pack(**package)
            sha = hashlib.sha256(raw).hexdigest()
            self.audit(db, actor, 'rule_package_export', sha, {'asset_count': len(assets), 'sample_count': len(samples), 'roots': package['roots']})
            return {'filename': f'rules-{sha[:12]}.netops-rules.zip', 'content_base64': base64.b64encode(raw).decode(),
                    'asset_count': len(assets), 'sample_count': len(samples), 'sha256': sha}

    @staticmethod
    def audit(db, actor, action, resource, detail):
        record = m.AuditLogRecord(id=str(uuid.uuid4()), user_id=actor, action=action,
                                  resource_type='rule_package', resource_id=resource, status='success', detail=detail)
        db.add(record)
        return record

    def plan(self, db, package):
        mapping, actions, seen, reserved = {}, [], {}, {}
        for asset in ordered(package):
            ref, kind = asset['ref'], asset['kind']
            spec = SPECS[kind]
            data = resolve(asset['data'], mapping)
            validate_columns(kind, data)
            query = db.query(spec.model).filter_by(**{f: data[f] for f in spec.identity.split()})
            candidates = query.order_by(spec.model.id).all()
            candidates.sort(key=lambda r: (getattr(r, 'version', None) != data.get('version'), getattr(r, 'status', '') != 'active', str(r.id)))
            comparable = {k: v for k, v in data.items() if k != 'version'}
            equivalent = next((r for r in candidates if {k: v for k, v in row_data(kind, r).items() if k != 'version'} == comparable), None)
            is_root = kind not in VERSION_KINDS and kind not in {'link', 'parser_binding'}
            local = equivalent or (candidates[0] if is_root and candidates else None)
            before = row_data(kind, local or candidates[-1]) if local or candidates else None
            conflict = bool(candidates and local is None)
            same_version = conflict and any(getattr(r, 'version', None) == data.get('version') for r in candidates)
            family_key = (kind, tuple(str(data[f]) for f in spec.identity.split()))
            if local:
                ident, action = str(local.id), 'reuse'
            else:
                ident = data.get('id') if spec.stable else str(uuid.uuid5(uuid.NAMESPACE_URL, 'netops/rules/' + digest([kind, data])))
                action = 'new_version' if conflict and kind in VERSION_KINDS else 'create'
                if conflict and kind not in VERSION_KINDS:
                    raise ValueError(f'{ref} 关联内容发生冲突，请先创建新的检查项或实现版本')
                if conflict:
                    data['version'] = (str(data['version'])[:40] + '-import-' + digest(data)[:12]) if kind == 'parser' else max([r.version for r in candidates] + reserved.get(family_key, [])) + 1
                collision = db.get(spec.model, ident)
                if collision is not None:
                    raise TransferConflict(f'{ref} 本地标识冲突，请重新预检')
            mapping[ref] = ident
            signature = (kind, tuple(str(data[f]) for f in spec.identity.split()), data.get('version'))
            if signature in seen and seen[signature] != ref:
                raise ValueError(f'包中含重复业务身份和版本：{ref}')
            seen[signature] = ref
            if kind in VERSION_KINDS:
                reserved.setdefault(family_key, []).append(data['version'])
            name = data.get('name') or data.get('canonical_command') or data.get('schema_id')
            if not name and spec.parent:
                name = next((a['after'].get('name') for a in actions if a['local_id'] == data[spec.parent]), None)
            actions.append({'ref': ref, 'kind': kind, 'action': action, 'local_id': ident, 'version': data.get('version'),
                            'name': name or ref,
                            'same_version_conflict': same_version, 'before': before, 'after': data,
                            'metadata_preserved': bool(local and is_root and before != data),
                            'local_status': getattr(local, 'status', None),
                            'snapshot': [{**row_data(kind, r), '_id': r.id, '_status': getattr(r, 'status', None),
                                          '_digest': getattr(r, 'digest', None)} for r in candidates]})
        self.validate_catalog(actions)
        token = digest(actions)
        return actions, mapping, token

    @staticmethod
    def validate_catalog(actions):
        from .check_items.catalog import build_check_item_definition, validate_check_item_definition, build_collection_implementation
        rows = {(a['kind'], a['local_id']): a['after'] for a in actions}
        checks = {a['after']['id'] for a in actions if a['kind'] == 'check'}
        for item in actions:
            kind, data = item['kind'], item['after']
            if kind not in {'check_version', 'implementation_version'}:
                continue
            parent_kind = 'check' if kind == 'check_version' else 'implementation'
            parent_field = SPECS[kind].parent
            root = rows[(parent_kind, data[parent_field])]
            payload = {**root, **{k.removesuffix('_json'): v for k, v in data.items() if k != parent_field}, 'status': 'draft'}
            if kind == 'check_version':
                candidate = build_check_item_definition(payload)
                validate_check_item_definition(candidate)
            else:
                links = [a['after'] for a in actions if a['kind'] == 'link' and a['after']['implementation_version_id'] == item['local_id']]
                payload['check_item_ids'] = sorted({rows[('check_version', link['check_item_version_id'])]['check_item_id'] for link in links})
                candidate = build_collection_implementation(payload, known_check_item_ids=checks)
                for link in links:
                    if rows[('check_version', link['check_item_version_id'])]['result_mode'] != candidate.parser_requirement:
                        raise ValueError('采集实现与关联检查项的结果模式不一致')
            item['catalog_digest'] = candidate.digest
            item['catalog_payload'] = {k: v for k, v in payload.items() if k not in {'id', 'version', 'status'}}

    def preview(self, raw):
        package = unpack(raw)
        reports = self.validator(package)
        with self.scope() as db:
            actions, mapping, token = self.plan(db, package)
        return {'sha256': hashlib.sha256(raw).hexdigest(), 'revision': token,
                'items': [{k: v for k, v in a.items() if k not in {'snapshot', 'catalog_payload', 'catalog_digest'}} for a in actions],
                'counts': dict(Counter(a['action'] for a in actions)), 'reports': reports,
                'sample_count': len(package['samples']), 'samples': package['samples'],
                'recipe_count': len(package['recipes']),
                'requires_confirmation': any(a['action'] == 'new_version' for a in actions)}

    def execute(self, raw, revision, *, confirm_conflicts=False, actor):
        package = unpack(raw)
        ordered(package)
        reports = self.validator(package)
        sha = hashlib.sha256(raw).hexdigest()
        with self.scope() as db:
            # Conflicting imports and other writers cannot invalidate a confirmed plan during commit.
            if db.bind.dialect.name == 'postgresql':
                db.execute(text("SET LOCAL lock_timeout = '5s'"))
                names = sorted({s.model.__tablename__ for s in SPECS.values()} | {m.CheckItemCatalogChangeRecord.__tablename__, m.AuditLogRecord.__tablename__})
                db.execute(text('LOCK TABLE ' + ','.join('"' + n + '"' for n in names) + ' IN SHARE ROW EXCLUSIVE MODE'))
            prior = db.query(m.AuditLogRecord).filter_by(action='rule_package_import', resource_id=sha).first()
            if prior:
                return {**prior.detail, 'record_id': prior.id, 'replayed': True}
            actions, mapping, token = self.plan(db, package)
            if token != revision:
                raise TransferConflict('资产已发生变化，请重新预检')
            if any(a['action'] == 'new_version' for a in actions) and not confirm_conflicts:
                raise TransferConflict('存在内容冲突，请确认创建新版本')
            result_items = []
            for item in actions:
                if item['action'] != 'reuse':
                    self.insert(db, item, actor, reports, sha)
                    db.flush()
                result_items.append({k: item[k] for k in ('ref', 'kind', 'action', 'local_id', 'version', 'name')})
            changes = self.catalog_changes(db, actions, actor, sha)
            detail = {'sha256': sha, 'items': result_items, 'counts': dict(Counter(a['action'] for a in actions)),
                      'changes': changes, 'reports': reports, 'samples': resolve(package['samples'], mapping),
                      'recipes': resolve(package['recipes'], mapping), 'state': 'draft',
                      'notice': '新增资产为待验证版本；来源样本回放通过不代表已通过本地发布门禁。'}
            audit = self.audit(db, actor, 'rule_package_import', sha, detail)
            db.flush()
            return {**detail, 'record_id': audit.id, 'replayed': False}

    def insert(self, db, item, actor, reports, sha):
        kind, data = item['kind'], dict(item['after'])
        model = SPECS[kind].model
        values = {**data, 'id': item['local_id']}
        columns = model.__table__.columns
        if 'status' in columns:
            values['status'] = 'draft'
        if 'current_version' in columns:
            values['current_version'] = 0
        if 'created_by' in columns:
            values['created_by'] = actor
        if 'digest' in columns:
            values['digest'] = item.get('catalog_digest') or digest(data)
        if 'validation_report_json' in columns:
            values['validation_report_json'] = {'status': 'pending', 'source': 'rule_package', 'package_sha256': sha}
        if kind == 'parser':
            from .parser.repository import contract_digest
            values.update(asset_path=f'database://parser/{item["local_id"]}', sha256=hashlib.sha256(data['template_text'].encode()).hexdigest(),
                          contract_sha256=contract_digest(data['validation_json']),
                          validation_json={**data['validation_json'], 'status': 'pending', 'import_package': sha, 'semantic_review': {'status': 'pending'}})
        if kind == 'policy_version':
            from src.core.patrol.change_policy_repository import change_policy_digest
            values['digest'] = change_policy_digest(data['policy_json'])
        if kind == 'event_version':
            from src.core.patrol.event_rules import event_rule_digest
            root = db.get(m.EventRuleRecord, data['rule_id'])
            values['digest'] = event_rule_digest(name=root.name, event_type=data['event_type'], severity=data['severity'],
                condition=data['condition_json'], governance={key: data[field] for key, field in {
                    'priority': 'priority', 'exclusive': 'exclusive', 'event_class': 'event_class', 'evaluation_scope': 'evaluation_scope',
                    'recovery': 'recovery_json', 'correlation_keys': 'correlation_key_json'}.items()})
        if kind == 'assessment_version':
            values.update(evidence_refs_json=[], provenance_json={'source': 'rule_package', 'sha256': sha},
                          idempotency_key='rules:' + item['local_id'], request_digest=digest(data))
        db.add(model(**values))

    def catalog_changes(self, db, actions, actor, sha):
        changes = []
        for item in actions:
            kind = item['kind']
            if kind not in {'check_version', 'implementation_version'} or item['action'] == 'reuse':
                continue
            spec = SPECS[kind]
            row = db.get(spec.model, item['local_id'])
            root = db.get(SPECS[fk_fields(kind)[spec.parent]].model, getattr(row, spec.parent))
            payload = item['catalog_payload']
            baseline = db.query(spec.model).filter(getattr(spec.model, spec.parent) == root.id, spec.model.status == 'active').first()
            record = m.CheckItemCatalogChangeRecord(id=str(uuid.uuid4()), asset_kind='check_item' if kind == 'check_version' else 'collection_implementation',
                asset_id=root.id, version_id=row.id, version=row.version, baseline_version_id=baseline.id if baseline else None,
                status='draft', payload_digest=row.digest, candidate_payload_json=payload, created_by=actor,
                creation_reason='规则包导入 ' + sha[:12], creation_idempotency_key='rules:' + row.id, request_digest=digest(payload))
            db.add(record)
            changes.append({'change_id': record.id, 'asset_id': root.id, 'kind': kind})
        return changes

    def history(self, offset=0, limit=20):
        with self.scope() as db:
            query = db.query(m.AuditLogRecord).filter(m.AuditLogRecord.action.in_(['rule_package_import', 'rule_package_export']))
            return {'total': query.count(), 'items': [{'id': r.id, 'action': r.action, 'actor': r.user_id,
                    'created_at': r.created_at.isoformat() if r.created_at else None, 'sha256': r.resource_id,
                    'counts': (r.detail or {}).get('counts'), 'asset_count': (r.detail or {}).get('asset_count')}
                    for r in query.order_by(m.AuditLogRecord.created_at.desc()).offset(offset).limit(limit).all()]}

    def detail(self, ident):
        with self.scope() as db:
            row = db.get(m.AuditLogRecord, ident)
            if not row or row.action != 'rule_package_import':
                raise ValueError('导入记录不存在')
            return {'record_id': row.id, **row.detail}

    def parser_samples(self, template):
        sha = (template.validation_json or {}).get('import_package')
        if not sha:
            return []
        with self.scope() as db:
            record = db.query(m.AuditLogRecord).filter_by(action='rule_package_import', resource_id=sha).first()
            if not record:
                raise ValueError('规则包来源记录缺失，不能作为 Parser 复核证据')
            samples = [s for s in record.detail.get('samples', []) if s['parser'] == template.id]
            return [{'sample_id': f'import:{record.id}:{index}', 'snapshot_id': None, 'device_id': None,
                     'sanitized_text': sample['text'], 'sanitized_hash': hashlib.sha256(sample['text'].encode()).hexdigest(),
                     'sample_role': 'package_replay', 'expectation': {'source': 'rule_package', 'expected_records': sample['expected']},
                     'source_context': {'canonical_command': template.canonical_command, 'package_sha256': sha}}
                    for index, sample in enumerate(samples)]

    def validate_import(self, ident, actor):
        """Read current target readiness using the existing assembly planner, never activate."""
        from .check_items.runtime_bundle_assembly import RuntimeBundleAssemblyPlanner
        detail = self.detail(ident)
        checks = []
        with self.scope() as db:
            for item in detail['items']:
                kind = item['kind']
                if kind not in PUBLIC_KINDS:
                    continue
                row = db.get(SPECS[kind].model, item['local_id'])
                blockers = []
                if row is None:
                    blockers.append('本地资产已删除，需要重新导入')
                elif row.status != 'active':
                    blockers.append('本地版本尚未发布；请完成对应目录或规则资产的验证与发布')
                checks.append({'name': LABELS[kind] + ' · ' + item['local_id'], 'status': 'blocked' if blockers else 'passed', 'blockers': blockers})
            for index, recipe in enumerate(detail.get('recipes', [])):
                assets = recipe['assets']
                ids = lambda role: [a['asset'] for a in assets if a['role'] == role]
                try:
                    plan = RuntimeBundleAssemblyPlanner(db).build(
                        check_item_version_id=recipe['check'], implementation_version_id=recipe['implementation'],
                        parser_template_ids=ids('parser_template'),
                        change_policy_version_ids=ids('change_policy'),
                        event_rule_version_ids=sorted(set(ids('event_rule') + ids('current_state_rule'))),
                        comparison_profile_version_id=recipe.get('comparison'))
                    blockers = [str(b.get('message') or b.get('code')) for b in plan.get('blockers', [])]
                except ValueError as exc:
                    blockers = [str(exc)]
                checks.append({'name': f'运行包装配 {index + 1}', 'status': 'blocked' if blockers else 'passed', 'blockers': blockers})
            if not detail.get('samples'):
                checks.append({'name': '本地验证样本', 'status': 'blocked', 'blockers': ['本包未携带 Parser 回放样本；请通过样本导入补齐本地证据。']})
            self.audit(db, actor, 'rule_package_validation', ident, {'checks': checks})
        return {'checks': checks}
