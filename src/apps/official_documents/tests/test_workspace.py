import copy
from contextlib import contextmanager
from io import BytesIO

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from docx import Document as Word

from src.infrastructure.db.models import Base, Conversation, Message
from src.apps.official_documents.backend.models import OfficialDocument as Document, OfficialDocumentVersion as Version, OfficialDocumentOperation as Operation
from src.apps.official_documents.backend import service, operations
from src.apps.official_documents.backend.schemas import CreateRequest, SaveRequest, RevisionRequest, AssistantRequest, Content, digest
from src.apps.official_documents.backend.render import render_workspace_document_bytes


@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Document.__table__, Version.__table__, Operation.__table__, Conversation.__table__, Message.__table__])
    @contextmanager
    def sessions():
        with Session(engine) as s:
            with s.begin():
                yield s
    monkeypatch.setattr(operations, 'db_session', sessions)
    monkeypatch.setattr(operations, 'dispatch', lambda _: None)
    yield sessions
    engine.dispose()


def create(s):
    return service.create(s, 'alice', CreateRequest(request_id='create-1', title='关于培训的通知'))


def complete_content(value):
    content = copy.deepcopy(value)
    texts = {'title': '关于培训的通知', 'main_recipient': '各部门：', 'opening': '现将培训事项通知如下。', 'body': '培训于15:00开始。', 'signature_org': '综合办公室', 'signature_date': '2026年9月5日'}
    for block in content['blocks']:
        block['runs'] = [{'text': texts[block['role']], 'marks': ['bold', 'underline'] if block['role'] == 'body' else []}]
    return content


@pytest.mark.parametrize('status,lease_delta,denial,expected', [
    ('queued', None, 403, 'failed'),
    ('queued', None, 503, 'queued'),
    ('running', -60, 403, 'failed'),
    ('running', 600, 403, 'running'),
])
def test_worker_rechecks_authorization_without_stomping_live_lease(db, monkeypatch, status, lease_delta, denial, expected):
    from datetime import timedelta
    from src.apps.official_documents.backend import tasks
    from src.apps.official_documents.backend.models import now

    with db() as s:
        first = create(s)
        doc = service.owned(s, first['id'], 'alice', lock=True)
        op = service.submit(s, doc, 'alice', AssistantRequest(base_revision=0, request_id='auth-check', action='generate', instruction='写通知'))
        op.status = status
        if lease_delta is not None:
            op.lease_expires_at = now() + timedelta(seconds=lease_delta)
        operation_id = op.id

    def denied(*args):
        raise HTTPException(denial, 'denied')

    monkeypatch.setattr(tasks, 'authorize_worker', denied)
    tasks.execute_document_operation.run(operation_id)
    with db() as s:
        assert s.get(Operation, operation_id).status == expected


def test_save_versions_idempotency_and_conflict(db):
    with db() as s:
        first = create(s)
        assert create(s)['id'] == first['id']
        doc = service.owned(s, first['id'], 'alice', lock=True)
        request = SaveRequest(request_id='save-1', base_revision=0, content=complete_content(first['content']))
        result = service.save(s, doc, 'alice', request)
        assert result['revision'] == 1 and result['current_version'] == 2
        assert service.save(s, doc, 'alice', request)['revision'] == 1
        with pytest.raises(HTTPException) as error:
            service.save(s, doc, 'alice', request.model_copy(update={'request_id': 'stale'}))
        assert error.value.status_code == 409
        assert service.version(s, doc, 1).content_json == first['content']


def test_full_resource_ownership_and_soft_delete(db):
    with db() as s:
        first = create(s)
        with pytest.raises(HTTPException) as error:
            service.owned(s, first['id'], 'bob')
        assert error.value.status_code == 404
        doc = service.owned(s, first['id'], 'alice', lock=True)
        op = service.submit(s, doc, 'alice', AssistantRequest(base_revision=0, request_id='ai', action='generate', instruction='写通知'))
        service.mutate(s, doc, 'alice', RevisionRequest(base_revision=0, request_id='delete'), 'delete')
        assert op.status == 'cancelled'
        with pytest.raises(HTTPException):
            service.owned(s, doc.id, 'alice')
        service.mutate(s, doc, 'alice', RevisionRequest(base_revision=1, request_id='undelete'), 'undelete')
        assert service.owned(s, doc.id, 'alice').deleted_at is None


def test_ai_patch_applies_only_to_frozen_block_and_is_idempotent(db):
    with db() as s:
        first = create(s); doc = service.owned(s, first['id'], 'alice', lock=True)
        original = first['content']; block = original['blocks'][3]
        op = service.submit(s, doc, 'alice', AssistantRequest(base_revision=0, request_id='ai', action='rewrite', instruction='改时间', block_ids=[block['id']]))
        replacement = {**block, 'runs': [{'text': '会议15:00开始。', 'marks': []}]}
        op.result_json = {'message': '修改时间', 'patches': [{'op': 'replace_block', 'block_id': block['id'], 'value': replacement, 'expected_hash': digest(block)}]}
        op.status = 'proposed'
        req = RevisionRequest(base_revision=0, request_id='accept')
        result = service.accept(s, doc, 'alice', op, req)
        assert result['content']['blocks'][0] == original['blocks'][0]
        assert result['content']['blocks'][3] == replacement
        assert service.accept(s, doc, 'alice', op, req)['current_version'] == 2


def test_ai_result_cannot_overwrite_manual_edit(db):
    with db() as s:
        first = create(s); doc = service.owned(s, first['id'], 'alice', lock=True)
        op = service.submit(s, doc, 'alice', AssistantRequest(base_revision=0, request_id='ai', action='generate', instruction='写通知'))
        op.status = 'proposed'; op.result_json = {'content': complete_content(first['content'])}
        service.save(s, doc, 'alice', SaveRequest(base_revision=0, request_id='manual', content=complete_content(first['content'])))
        with pytest.raises(HTTPException) as error:
            service.accept(s, doc, 'alice', op, RevisionRequest(base_revision=1, request_id='accept'))
        assert error.value.status_code == 409


def test_finalize_checks_and_restore_new_snapshot(db):
    with db() as s:
        first = create(s); doc = service.owned(s, first['id'], 'alice', lock=True)
        with pytest.raises(HTTPException):
            service.mutate(s, doc, 'alice', RevisionRequest(base_revision=0, request_id='finalize'), 'finalize')
        service.save(s, doc, 'alice', SaveRequest(base_revision=0, request_id='save', content=complete_content(first['content'])))
        service.mutate(s, doc, 'alice', RevisionRequest(base_revision=1, request_id='finalize'), 'finalize')
        with pytest.raises(HTTPException):
            service.save(s, doc, 'alice', SaveRequest(base_revision=2, request_id='restore', content=first['content']), source='restore', force=True)
        service.mutate(s, doc, 'alice', RevisionRequest(base_revision=2, request_id='reopen'), 'reopen')
        service.save(s, doc, 'alice', SaveRequest(base_revision=3, request_id='restore', content=first['content']), source='restore', force=True)
        assert doc.current_version == 3
        assert service.version(s, doc, 2).content_json != service.version(s, doc, 3).content_json


def test_docx_preserves_text_and_supported_marks(db):
    with db() as s:
        content = complete_content(create(s)['content'])
    word = Word(BytesIO(render_workspace_document_bytes(content, draft=True)))
    assert word.sections[0].page_width.cm == pytest.approx(21, abs=.01)
    body = next(p for p in word.paragraphs if p.text == '培训于15:00开始。')
    assert body.runs[0].bold and body.runs[0].underline
    assert '草稿' in word.sections[0].header.paragraphs[0].text
    assert word.paragraphs[0].runs[0].font.size.pt == 22


def test_export_preserves_unconfirmed_facts_without_draft_mode(db):
    with db() as s:
        first = create(s)
        assert first['validation_issues']
        doc = service.owned(s, first['id'], 'alice', lock=True)
        content = complete_content(first['content'])
        content['unresolved_facts'] = ['会议地点']
        content['blocks'][3]['runs'][0]['text'] = '会议地点：【待确认：会议室】。'
        saved = service.save(s, doc, 'alice', SaveRequest(base_revision=0, request_id='save', content=content))
        assert [issue['id'] for issue in saved['validation_issues']] == ['facts']
        request = RevisionRequest(base_revision=1, request_id='export')
        with pytest.raises(HTTPException):
            service.mutate(s, doc, 'alice', request, 'finalize')
        op = service.submit(s, doc, 'alice', request, kind='export', export_version=2)
        op.result_json = {'object_key': 'private-key'}
        described = service.operation_data(op)
        assert described['draft'] is False and 'object_key' not in described['result']
        word = Word(BytesIO(render_workspace_document_bytes(op.input_json['content'])))
        assert not word.sections[0].header.paragraphs[0].text
        assert any('【待确认：会议室】' in p.text for p in word.paragraphs)
        assert service.version(s, doc).content_json == content
        updated = service.save(s, doc, 'alice', SaveRequest(base_revision=1, request_id='verified', content=complete_content(first['content'])))
        assert updated['validation_issues'] == []
        formal = service.submit(s, doc, 'alice', RevisionRequest(base_revision=2, request_id='formal'), kind='export', export_version=3)
        assert service.operation_data(formal)['draft'] is False


def test_content_rejects_duplicate_identity_and_active_markup(db):
    with db() as s:
        content = create(s)['content']
    content['blocks'][1]['id'] = content['blocks'][0]['id']
    with pytest.raises(ValueError): Content.model_validate(content)
    content['blocks'][1]['id'] = 'safe-id'
    content['blocks'][1]['runs'] = [{'text': 'text', 'marks': ['script']}]
    with pytest.raises(ValueError): Content.model_validate(content)


def test_worker_duplicate_delivery_and_failure_are_durable(db, monkeypatch):
    with db() as s:
        first = create(s); doc = service.owned(s, first['id'], 'alice', lock=True)
        op = service.submit(s, doc, 'alice', AssistantRequest(base_revision=0, request_id='ai', action='generate', instruction='写通知'))
        op_id = op.id
    calls = []
    async def generate(payload):
        calls.append(payload)
        return {'kind': 'clarification', 'message': '请补充', 'questions': ['会议在哪里？']}
    monkeypatch.setattr(operations, 'generate_result', generate)
    operations.execute(op_id); operations.execute(op_id)
    assert len(calls) == 1
    with db() as s:
        assert s.get(Operation, op_id).status == 'needs_input'
        parent = s.get(Operation, op_id)
        doc = s.get(Document, parent.document_id)
        retry = service.submit(s, doc, 'alice', AssistantRequest(base_revision=0, request_id='reply', action='generate', instruction='三楼会议室', parent_operation_id=op_id))
        assert retry.input_json['reference_time'] == parent.input_json['reference_time']
        assert parent.status == 'completed'
        assert retry.input_json['conversation_history'][0]['content'] == '写通知'
        retry_id = retry.id
    async def fail(_): raise RuntimeError('model unavailable')
    monkeypatch.setattr(operations, 'generate_result', fail)
    operations.execute(retry_id)
    with db() as s:
        assert s.get(Operation, retry_id).status == 'failed'


def test_cancel_while_worker_runs_discards_late_result(db, monkeypatch):
    with db() as s:
        first = create(s); doc = service.owned(s, first['id'], 'alice', lock=True)
        op_id = service.submit(s, doc, 'alice', AssistantRequest(base_revision=0, request_id='ai', action='generate', instruction='写通知')).id
    async def generate(_):
        with db() as s:
            s.get(Operation, op_id).status = 'cancelled'
        return {'kind': 'proposal', 'content': complete_content(first['content']), 'message': '初稿'}
    monkeypatch.setattr(operations, 'generate_result', generate)
    operations.execute(op_id)
    with db() as s:
        assert s.get(Operation, op_id).result_json is None
        assert s.get(Document, first['id']).current_version == 1


def test_api_auth_and_nested_resources(db):
    from src.gateway.official_documents_api import router, session
    from src.apps.official_documents.backend.api import get_current_user
    from src.auth.models import CurrentUser
    app = FastAPI(); app.include_router(router)
    def sessions():
        with db() as s: yield s
    app.dependency_overrides[session] = sessions
    client = TestClient(app)
    assert client.get('/api/v1/official-documents').status_code == 401
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id='alice', username='alice')
    response = client.post('/api/v1/official-documents', json={'title': '测试通知', 'request_id': 'new'})
    assert response.status_code == 201, response.text
    identifier = response.json()['id']
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id='bob', username='bob')
    for suffix in ('', '/versions', '/messages', '/operations', '/versions/1', '/exports/fake/download', '/operations/fake/events'):
        assert client.get(f'/api/v1/official-documents/{identifier}{suffix}').status_code == 404
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id='alice', username='alice', role='viewer')
    # App admission grants the complete business capability, regardless of legacy role.
    assert client.post('/api/v1/official-documents', json={'request_id': 'granted-viewer'}).status_code == 201


def test_export_uses_frozen_version_and_reports_storage_failure(db, monkeypatch):
    with db() as s:
        first = create(s); doc = service.owned(s, first['id'], 'alice', lock=True)
        original = complete_content(first['content'])
        service.save(s, doc, 'alice', SaveRequest(base_revision=0, request_id='save', content=original))
        op_id = service.submit(s, doc, 'alice', RevisionRequest(base_revision=1, request_id='export'), kind='export', export_version=2).id
        changed = copy.deepcopy(original); changed['blocks'][3]['runs'][0]['text'] = '改为16:00'
        service.save(s, doc, 'alice', SaveRequest(base_revision=1, request_id='later', content=changed))
    def export(payload, *_):
        assert payload['content'] == original
        raise RuntimeError('storage unavailable')
    monkeypatch.setattr(operations, 'export_result', export)
    operations.execute(op_id)
    with db() as s:
        assert s.get(Operation, op_id).status == 'failed'
        assert s.get(Operation, op_id).result_json is None


def test_legacy_review_does_not_claim_success_after_model_failure(monkeypatch):
    import asyncio
    from src.apps.official_documents.backend.writing import service as legacy
    class BrokenLLM:
        async def ainvoke(self, _):
            raise RuntimeError('unavailable')
    monkeypatch.setattr(legacy, 'get_document_llm', lambda: BrokenLLM())
    result = asyncio.run(legacy.review_document('通知', '请参训', '检查清单', '格式规范'))
    assert result['success'] is False


def test_model_cannot_modify_unselected_blocks(db, monkeypatch):
    import asyncio
    from src.apps.official_documents.backend.writing import service as legacy
    with db() as s:
        content = complete_content(create(s)['content'])
    class WrongScopeLLM:
        def with_structured_output(self, *_args, **_kwargs): return self
        async def ainvoke(self, _):
            return {'kind': 'proposal', 'message': '修改', 'patches': [{'op': 'delete_block', 'block_id': content['blocks'][0]['id']}]}
    monkeypatch.setattr(legacy, 'get_document_llm', lambda: WrongScopeLLM())
    payload = {'kind': 'rewrite', 'content': content, 'block_ids': [content['blocks'][3]['id']]}
    with pytest.raises(ValueError, match='选定段落'):
        asyncio.run(operations.generate_result(payload))


def test_structural_checks_follow_document_type_and_validate_date(db):
    from src.apps.official_documents.backend.schemas import blocking_issues
    with db() as s:
        content = complete_content(create(s)['content'])
    content['doc_type'] = '会议纪要'
    content['blocks'] = [b for b in content['blocks'] if b['role'] != 'main_recipient']
    assert not blocking_issues(content)
    next(b for b in content['blocks'] if b['role'] == 'signature_date')['runs'][0]['text'] = '2026年2月30日'
    assert any(i['id'] == 'signature_date_format' for i in blocking_issues(content))
    next(b for b in content['blocks'] if b['role'] == 'body')['runs'] = []
    assert any(i['id'] == 'body' for i in blocking_issues(content))


def test_model_answer_with_valid_patch_is_presented_as_unapplied_proposal(db, monkeypatch):
    import asyncio
    from src.apps.official_documents.backend.writing import service as legacy
    with db() as s:
        content = complete_content(create(s)['content'])
    block = copy.deepcopy(content['blocks'][3])
    block['runs'][0]['text'] = '培训时间调整为16:00。'
    class MislabeledLLM:
        def with_structured_output(self, *_args, **_kwargs): return self
        async def ainvoke(self, _):
            return {'kind': 'answer', 'message': '修改时间', 'patches': [{'op': 'replace_block', 'block_id': block['id'], 'value': block}]}
    monkeypatch.setattr(legacy, 'get_document_llm', lambda: MislabeledLLM())
    result = asyncio.run(operations.generate_result({'kind': 'rewrite', 'content': content, 'block_ids': [block['id']]}))
    assert result['kind'] == 'proposal'
    assert result['patches'][0]['expected_hash'] == digest(content['blocks'][3])


def test_whole_draft_uses_skill_structure_and_splits_sections(db, monkeypatch):
    import asyncio
    from src.apps.official_documents.backend.writing import service as legacy
    with db() as s:
        content = create(s)['content']
    class FullDocumentLLM:
        def with_structured_output(self, schema, **_):
            assert schema.__name__ == 'DraftResult'
            return self
        async def ainvoke(self, prompt):
            assert '"block_ids": []' in prompt
            return {'kind': 'proposal', 'message': 'body block', 'document': {
                'doc_type': '通知', 'title': '关于参加集团年会的通知', 'main_recipient': '集团各部门',
                'main_body': {'opening': '为总结工作，召开集团年会。', 'sections': [{'heading': '一、会议安排', 'content': '会议时间：2026年12月5日。\n请各部门负责人参加。'}, {'heading': '二、有关要求', 'content': '请准时参会。'}], 'closing': '特此通知。'},
                'signature': {'org': '集团办公室', 'date': '2026年9月5日'}}}
    monkeypatch.setattr(legacy, 'get_document_llm', lambda: FullDocumentLLM())
    result = asyncio.run(operations.generate_result({'kind': 'generate', 'content': content, 'block_ids': [content['blocks'][3]['id']]}))
    blocks = result['content']['blocks']
    assert len([b for b in blocks if b['role'] == 'section_heading']) == 2
    assert len([b for b in blocks if b['role'] == 'body']) == 3
    assert blocks[0]['id'] == content['blocks'][0]['id']
    assert blocks[0]['runs'][0]['text'] != '未命名公文'
    assert 'body' not in result['message']
    word = Word(BytesIO(render_workspace_document_bytes(result['content'])))
    assert word.sections[0].top_margin.mm == pytest.approx(37, abs=.01)
    assert word.paragraphs[0].runs[0].font.size.pt == 22
    assert word.paragraphs[0].runs[0].font.name == '方正小标宋简体'
    body = next(p for p in word.paragraphs if p.text == '会议时间：2026年12月5日。')
    assert body.paragraph_format.line_spacing.pt == 28
    assert body.paragraph_format.first_line_indent.pt == 32
    assert body.runs[0].font.size.pt == 16


def test_template_extension_changes_catalog_without_frontend_changes(tmp_path, monkeypatch):
    import json
    from src.apps.official_documents.backend import templates
    path = tmp_path / 'templates.json'
    data = json.loads(templates.REGISTRY_PATH.read_text(encoding='utf-8'))
    data['templates'].append({'id': 'invitation', 'doc_type': '会议邀请', 'name': '会议邀请', 'aliases': [], 'sections': ['会议安排'], 'closing': '敬请出席。', 'recipient_required': True, 'format': {'title_size_pt': 24}})
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(templates, 'REGISTRY_PATH', path)
    assert templates.catalog()['templates'][-1]['doc_type'] == '会议邀请'
    assert templates.resolve_template('会议邀请')['format']['title_size_pt'] == 24
    assert templates.resolve_template('会议邀请')['format']['body_size_pt'] == 16


def test_application_draft_uses_requested_type_without_report_headings(db, monkeypatch):
    import asyncio
    from src.apps.official_documents.backend.writing import service as legacy
    with db() as s:
        first = service.create(s, 'alice', CreateRequest(request_id='application', doc_type='报告'))
        previous = first['content']
    class ApplicationLLM:
        def with_structured_output(self, *args, **kwargs): return self
        async def ainvoke(self, prompt):
            return {'kind': 'proposal', 'message': '初稿', 'unresolved_facts': ['申请人姓名'], 'document': {
                'doc_type': '入党申请书', 'title': '入党申请书', 'main_recipient': '敬爱的党组织',
                'main_body': {'opening': '我志愿加入中国共产党。', 'sections': [{'heading': '', 'content': '我将认真学习理论知识。\n【待确认：结合本人实际经历补充表现】'}], 'closing': '请党组织在实践中考验我。'},
                'signature': {'org': '申请人：【待确认：姓名】', 'date': '2026年9月5日'}}}
    monkeypatch.setattr(legacy, 'get_document_llm', lambda: ApplicationLLM())
    payload = {'kind': 'generate', 'content': previous, 'instruction': '你随便写', 'clarification_context': {'original_instruction': '写一份入党申请书'}}
    result = asyncio.run(operations.generate_result(payload))
    assert result['kind'] == 'proposal'
    assert result['content']['doc_type'] == '入党申请书'
    assert not any(b['role'] == 'section_heading' for b in result['content']['blocks'])
    assert len([b for b in result['content']['blocks'] if b['role'] == 'body']) == 2
    strict = asyncio.run(operations.generate_result({**payload, 'allow_placeholders': False}))
    assert strict['kind'] == 'clarification'
    with db() as s:
        doc = service.owned(s, first['id'], 'alice', lock=True)
        op = service.submit(s, doc, 'alice', AssistantRequest(request_id='generate', base_revision=0, instruction='写入党申请书', action='generate'))
        op.result_json = result
        op.status = 'proposed'
        assert doc.document_type == '报告'
        accepted = service.accept(s, doc, 'alice', op, RevisionRequest(base_revision=0, request_id='accept'))
        assert accepted['document_type'] == '入党申请书'
        word = Word(BytesIO(render_workspace_document_bytes(accepted['content'])))
        assert word.paragraphs[0].text == '入党申请书'
        assert any('待确认' in p.text for p in word.paragraphs)


def test_repeated_clarifications_keep_original_writing_request(db):
    with db() as s:
        first = create(s)
        doc = service.owned(s, first['id'], 'alice', lock=True)
        op = service.submit(s, doc, 'alice', AssistantRequest(request_id='initial', base_revision=0, instruction='写入党申请书', action='generate'))
        for index, instruction in enumerate(['你随便写', '你随便编']):
            op.status = 'needs_input'
            op.result_json = {'questions': ['请补充姓名']}
            op = service.submit(s, doc, 'alice', AssistantRequest(request_id=f'reply-{index}', base_revision=0, instruction=instruction, action='generate', parent_operation_id=op.id))
            assert op.input_json['clarification_context']['original_instruction'] == '写入党申请书'
            assert op.input_json['allow_placeholders'] is True
