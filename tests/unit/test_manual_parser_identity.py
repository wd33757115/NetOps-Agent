from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from src.apps.rule_governance.backend.parser import manual_candidate as module
from src.core.parser_generation import ParserSample, SampleRole, SampleSet


@pytest.mark.parametrize('status', ['failed', 'collecting'])
def test_manual_revision_claims_authenticated_owner_before_saving(monkeypatch, status):
    job={'id':'job','pipeline_version':'simple_v2','status':status,'generation_readiness':{'ready':True},
         'parser_spec_json':{'entity':'port','primary_keys':['interface'],
             'fields':{'interface':{'required':True,'type':'text'}}}}
    jobs=Mock();jobs.get_job.return_value=job
    templates=Mock();templates.list_templates.return_value=[{'id':'candidate','source_job_id':'job','version':'simple-attempt'}]
    monkeypatch.setattr(module, '_load_sample_set', lambda *a: SampleSet((ParserSample(
        sample_id='sample',raw_text='Ethernet1/1\n',role=SampleRole.GENERATION),)))
    def persist(**kwargs):
        jobs.mark_job_dispatched.assert_called_once()
        args=jobs.mark_job_dispatched.call_args
        assert args.kwargs=={'actor':'engineer','actor_user_id':'current-user'}
        execution_id=args.args[1]
        assert execution_id.startswith('manual:')
        jobs.transition_job_status.assert_called_once_with('job','running',
            expected_status='pending',expected_celery_task_id=execution_id)
        return SimpleNamespace(template_generated=True,best_attempt_id='attempt')
    monkeypatch.setattr(module, 'persist_generation', persist)
    result=module.ManualParserCandidateSubmissionService(job_repository=jobs,template_repository=templates,
        patrol_repository=Mock()).submit(job_id='job',actor='engineer',actor_user_id='current-user',
            template_text='Value interface (\\S+)\n\nStart\n  ^${interface}$$ -> Record\n')
    assert result.template_id=='candidate'
    if status=='failed':
        assert jobs.retry_job.call_args.kwargs['actor_user_id']=='current-user'
    else:
        jobs.retry_job.assert_not_called()
