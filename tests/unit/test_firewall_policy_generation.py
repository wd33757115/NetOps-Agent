"""Exercise the real CLI, workbook reader, topology and vendor rendering together."""
import json
import io
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pandas as pd
import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "src/skills/firewall-policy-generator/scripts"
COLUMNS = ["src_ip", "dst_ip", "port", "proto", "action"]
VALID = ["10.0.0.1", "10.1.0.1", "443", "tcp", "permit"]


def invoke(tmp_path, rows, columns=COLUMNS, offset=0, topology_change=None, **params):
    workbook = tmp_path / "policy.xlsx"
    pd.DataFrame(rows, columns=columns).to_excel(workbook, index=False, startrow=offset)
    topology = {"firewalls": [{"name": "TEST-FW", "type": "华为", "security_domains": [
        {"name": "Trust", "ip_ranges": ["10.0.0.0/24"]},
        {"name": "Untrust", "ip_ranges": ["10.1.0.0/24"]},
    ]}]}
    if topology_change:
        topology_change(topology)
    topo_path = tmp_path / "topology.json"
    topo_path.write_text(json.dumps(topology), encoding="utf-8")
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps({"ticket_id": "test123", "policy_file_url": str(workbook),
                                      "topology_file_url": str(topo_path), **params}), encoding="utf-8")
    output = tmp_path / "output"
    result = subprocess.run([sys.executable, str(SCRIPTS / "run.py"), "--params", str(params_path),
                             "--output-dir", str(output)], capture_output=True, encoding="utf-8",
                            env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"), timeout=30)
    payload = json.loads((result.stdout if result.returncode == 0 else result.stderr).splitlines()[-1])
    return result, payload, output


@pytest.mark.parametrize("offset", [0, 9, 14])
def test_valid_workbook_generates_real_configuration(tmp_path, offset):
    result, payload, output = invoke(tmp_path, [VALID], offset=offset)
    assert result.returncode == 0, result.stderr
    assert payload["manifest"]["device_count"] == 1
    config = (output / "system/TEST-FW.txt").read_text(encoding="utf-8")
    for command in ["source-address 10.0.0.1 32", "destination-address 10.1.0.1 32",
                    "destination-port 443", "action permit"]:
        assert command in config
    assert (output / "manifest.json").exists()
    assert "_local_zip" not in payload  # directory-output executor owns the archive lifetime
    assert "policy_plan" not in payload  # chat stays compact


def test_app_can_request_the_verified_structured_plan(tmp_path):
    result, payload, output = invoke(tmp_path, [VALID], include_policy_plan=True)
    assert result.returncode == 0, result.stderr
    plan = json.loads((output / "policy_plan.json").read_text(encoding="utf-8"))
    assert payload["policy_plan"] == plan
    assert plan["rules"][0]["evidence_ids"]
    assert plan["commands"]["TEST-FW"] == payload["manifest"]["scripts"][0]["commands"]


@pytest.mark.parametrize('enabled', [False, True])
def test_business_paths_are_optional_and_keep_names_through_export(tmp_path, enabled):
    rows = [[*VALID, '订单系统<script>', '支付系统'],
            ['10.0.0.2', *VALID[1:], '库存系统', '支付系统']]
    result, payload, output = invoke(tmp_path, rows,
        columns=[*COLUMNS, '源业务系统', '目的业务系统'], offset=8,
        include_business_paths=enabled, include_policy_plan=True)
    assert result.returncode == 0, payload
    plan = payload['policy_plan']
    assert plan['include_business_paths'] is enabled
    assert [r['source_system'] for r in plan['requests']] == ['订单系统<script>', '库存系统']
    assert plan['optimization']['after'] == 1
    assert {e['row'] for e in plan['evidence']} == {10, 11}
    assert set(plan['rules'][0]['evidence_ids']) == {e['id'] for e in plan['evidence']}
    preview = (output / '策略计划预览.html').read_text(encoding='utf-8')
    assert ('<h2>业务系统与拓扑路径</h2>' in preview) is enabled
    assert ('订单系统&lt;script&gt;' in preview) is enabled
    assert '订单系统<script>' not in preview
    assert plan['plan_id'] not in preview
    assert all(rule['id'] not in preview for rule in plan['rules'])
    exported = pd.read_excel(output / '策略申请表.xlsx').fillna('')
    assert exported['源业务系统'].tolist() == ['订单系统<script>', '库存系统']
    assert exported['目的业务系统'].tolist() == ['支付系统', '支付系统']
    # Names are annotations, never injected into vendor commands.
    assert '系统' not in plan['commands']['TEST-FW']


def test_business_paths_with_no_system_names_and_invalid_option(tmp_path):
    result, payload, output = invoke(tmp_path, [VALID], include_business_paths=True)
    assert result.returncode == 0, payload
    assert '未填写' in (output / '策略计划预览.html').read_text(encoding='utf-8')
    result, payload, _ = invoke(tmp_path, [VALID], include_business_paths='false')
    assert result.returncode != 0
    assert 'include_business_paths 必须为 true 或 false' in payload['error']


def test_chinese_headers_and_reordered_columns(tmp_path):
    result, payload, _ = invoke(tmp_path, [["tcp", "permit", "443", "10.1.0.1", "10.0.0.1"]],
                                columns=["协议", "动作", "目的端口", "目的地址", "源地址"])
    assert result.returncode == 0, payload


def test_device_inventory_is_rejected_without_empty_artifact(tmp_path):
    result, payload, output = invoke(tmp_path, [["test", "192.168.1.1"]], columns=["device_name", "IP"])
    assert result.returncode != 0
    assert "设备清单不能用于生成策略" in payload["error"]
    assert not list(output.rglob("*.txt"))
    assert not (output / "manifest.json").exists()


@pytest.mark.parametrize("rows,expected", [
    ([], "没有有效策略行"),
    ([["10.0.0.1", "", "443", "tcp", "permit"]], "dst_ip 不能为空"),
    ([["10.0.0.1", "10.1.0.1", "70000", "tcp", "permit"]], "端口范围"),
    ([["10.0.0.1", "10.1.0.1", "", "tcp", "permit"]], "必须明确填写端口"),
    ([["10.0.0.999", "10.1.0.1", "443", "tcp", "permit"]], "第 2 行无效"),
    ([VALID, ["192.0.2.1", "10.1.0.1", "443", "tcp", "permit"]], "无法匹配拓扑"),
])
def test_invalid_or_partial_input_never_exports(tmp_path, rows, expected):
    result, payload, output = invoke(tmp_path, rows)
    assert result.returncode != 0
    assert expected in payload["error"]
    assert not list(output.rglob("*.txt"))
    assert not (output / "manifest.json").exists()


@pytest.mark.parametrize("key", ["policy_file_url", "topology_file_url"])
def test_explicit_missing_file_does_not_fall_back_to_sample(tmp_path, key):
    result, payload, output = invoke(tmp_path, [VALID], **{key: str(tmp_path / "missing")})
    assert result.returncode != 0
    assert "不存在" in payload["error"]
    assert not list(output.rglob("*.txt"))


def test_unsupported_vendor_cannot_claim_success(tmp_path):
    result, payload, _ = invoke(tmp_path, [VALID], topology_change=lambda t: t["firewalls"][0].update(type="unknown"))
    assert result.returncode != 0
    assert "不支持的防火墙类型" in payload["error"]


def test_acl_failure_cannot_export_partial_configuration(tmp_path):
    result, payload, output = invoke(tmp_path, [VALID], topology_change=lambda t: t.update(global_acl=[{
        "source_fw": "TEST-FW", "source_zone": "Trust", "destination_fw": "TEST-FW",
        "destination_zone": "Untrust", "action": "deny",
    }]))
    assert result.returncode != 0
    assert "全局 ACL 禁止" in payload["error"]
    assert not list(output.rglob("*.txt"))


def test_shared_executor_archives_real_commands(monkeypatch, tmp_path):
    from src.core.skills import executor

    invoke(tmp_path, [VALID])
    uploaded = {}

    class Storage:
        def is_ready(self):
            return True

        def upload_file(self, key, stream, **kwargs):
            uploaded[key] = stream.read()
            return True

        def get_presigned_url(self, key, **kwargs):
            return "http://storage.test/" + key

    monkeypatch.setattr(executor, "get_minio_storage", Storage)
    params = json.loads((tmp_path / "params.json").read_text())
    result = executor._run_subprocess_skill("firewall-policy-generator", params)
    assert result["success"]
    with ZipFile(io.BytesIO(uploaded[result["config_file_key"]])) as archive:
        assert "source-address 10.0.0.1" in archive.read("system/TEST-FW.txt").decode()
        assert json.loads(archive.read("manifest.json"))["device_count"] == 1
        plan = json.loads(archive.read("policy_plan.json"))
        assert plan['plan_id'] == result['policy_plan_id']
        assert result['policy_plan_id'] in result['config_file_key']
        assert uploaded[result['artifacts']['policy_preview']['file_key']] == archive.read('策略计划预览.html')
        assert uploaded[result['artifacts']['policy_request']['file_key']] == archive.read('策略申请表.xlsx')


def test_shared_executor_preserves_validation_error_without_upload(monkeypatch, tmp_path):
    from src.core.skills import executor

    invoke(tmp_path, [["test", "192.0.2.1"]], columns=["device_name", "IP"])
    monkeypatch.setattr(executor, "get_minio_storage", lambda: None)
    params = json.loads((tmp_path / "params.json").read_text())
    with pytest.raises(executor.SkillExecutionError) as error:
        executor._run_subprocess_skill("firewall-policy-generator", params)
    assert error.value.payload["success"] is False
    assert "设备清单不能用于生成策略" in error.value.payload["error"]


def test_no_default_sample_when_file_not_supplied(tmp_path):
    result, payload, _ = invoke(tmp_path, [VALID], policy_file_url=None)
    assert result.returncode != 0
    assert '请上传策略申请表' in payload['error']


def test_action_optional_and_disable_merge(tmp_path):
    result, payload, output = invoke(tmp_path, [VALID[:4], VALID[:4]], columns=COLUMNS[:4], merge_policies=False)
    assert result.returncode == 0, payload
    plan = json.loads((output / 'policy_plan.json').read_text(encoding='utf-8'))
    assert plan['optimization']['status'] == 'disabled'
    assert len(plan['rules']) == 2


@pytest.mark.parametrize('field,value,expected', [('action','deny','只生成 permit'),('proto','notaprotocol','协议暂只支持'),
                                                ('end_time','2026-09-10','暂不支持 end_time')])
def test_unsupported_request_is_not_silently_changed(tmp_path, field, value, expected):
    columns = COLUMNS if field in COLUMNS else COLUMNS + [field]
    row = dict(zip(COLUMNS, VALID))
    row[field] = value
    result, payload, output = invoke(tmp_path, [[row[c] for c in columns]], columns=columns)
    assert result.returncode != 0
    assert expected in payload['error']
    assert not list(output.rglob('*.txt'))


def test_generated_preview_escapes_input_name(tmp_path):
    result, payload, output = invoke(tmp_path, [VALID], policy_input_name='<img src=x onerror=alert(1)>')
    assert result.returncode == 0, payload
    html = (output / '策略计划预览.html').read_text(encoding='utf-8')
    assert '<img src=x' not in html
    assert '&lt;img' in html
