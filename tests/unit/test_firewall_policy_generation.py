"""Exercise the real CLI, workbook reader, topology and vendor rendering together."""
import json
from datetime import date, timedelta
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
                                                ('end_time','2026-09-10','start_time 和 end_time 必须同时填写')])
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


# Validity window (start_time/end_time) and long link, rendered for USG6000 and F5000.
TIMED = [*COLUMNS, 'start_time', 'end_time', 'long_link']
START = date.today() + timedelta(days=1)
END = START + timedelta(days=30)


def timed(start=f'{START} 08:00', end=f'{END}', long_link='是', base=VALID):
    return [*base, start, end, long_link]


def h3c(topology):
    topology['firewalls'][0]['type'] = 'H3C'


def test_validity_and_long_link_on_huawei(tmp_path):
    result, payload, output = invoke(tmp_path, [timed()], columns=TIMED, include_policy_plan=True)
    assert result.returncode == 0, payload
    config = (output / 'system/TEST-FW.txt').read_text(encoding='utf-8')
    # The time range is defined before the policy and named after the ticket.
    assert config.index('time-range test123\n') < config.index('security-policy')
    assert f' absolute-range 08:00:00 {START:%Y/%m/%d} to 23:59:59 {END:%Y/%m/%d}' in config
    for command in [' time-range test123', ' long-link enable', ' long-link aging-time 168']:
        assert command + '\n' in config
    rule = payload['policy_plan']['rules'][0]
    assert rule['validity'] == f'{START} 08:00 至 {END} 24:00'
    assert rule['long_link_hours'] == 168
    preview = (output / '策略计划预览.html').read_text(encoding='utf-8')
    assert '有效期' in preview and '168 小时' in preview
    # The editable request keeps the cells as written, so a re-upload reads the same window.
    exported = pd.read_excel(output / '策略申请表.xlsx', dtype=str).fillna('')
    assert exported[['start_time', 'end_time', 'long_link']].values.tolist() == [[f'{START} 08:00', f'{END}', '是']]


def test_validity_and_never_aging_long_link_on_h3c_f5000(tmp_path):
    result, payload, output = invoke(tmp_path, [timed(long_link='0')], columns=TIMED, topology_change=h3c)
    assert result.returncode == 0, payload
    config = (output / 'system/TEST-FW.txt').read_text(encoding='utf-8')
    assert f'time-range test123 from 08:00 {START:%Y/%m/%d} to 24:00 {END:%Y/%m/%d}\n' in config
    assert config.index('time-range test123 from') < config.index('security-policy ip')
    assert 'time-range test123\nsession persistent aging-time 0\naction pass' in config


def test_excel_date_cells_end_with_the_whole_day(tmp_path):
    row = timed(start=pd.Timestamp(START), end=pd.Timestamp(END), long_link='')
    result, payload, output = invoke(tmp_path, [row], columns=TIMED, include_policy_plan=True)
    assert result.returncode == 0, payload
    rule = payload['policy_plan']['rules'][0]
    assert rule['validity'] == f'{START} 00:00 至 {END} 24:00'
    assert rule['long_link_hours'] is None
    assert 'long-link' not in (output / 'system/TEST-FW.txt').read_text(encoding='utf-8')


def test_different_settings_are_never_merged(tmp_path):
    rows = [timed(start='', end='', long_link=''), timed(base=['10.0.0.2', *VALID[1:]])]
    result, payload, output = invoke(tmp_path, rows, columns=TIMED, include_policy_plan=True)
    assert result.returncode == 0, payload
    rules = payload['policy_plan']['rules']
    assert sorted(r['validity'] for r in rules) == ['', f'{START} 08:00 至 {END} 24:00']
    config = (output / 'system/TEST-FW.txt').read_text(encoding='utf-8')
    assert config.count(' time-range test123') == 1 and config.count('long-link enable') == 1


def test_long_link_applies_to_the_tcp_half_only(tmp_path):
    row = timed(base=[*VALID[:3], 'tcp/udp', 'permit'])
    result, payload, output = invoke(tmp_path, [row], columns=TIMED, include_policy_plan=True)
    assert result.returncode == 0, payload
    hours = {r['protocol']: r['long_link_hours'] for r in payload['policy_plan']['rules']}
    assert hours == {'tcp': 168, 'udp': None}
    assert (output / 'system/TEST-FW.txt').read_text(encoding='utf-8').count('long-link enable') == 1


@pytest.mark.parametrize('row,expected', [
    (timed(end=''), 'start_time 和 end_time 必须同时填写'),
    (timed(start=f'{END}', end=f'{START}'), 'end_time 必须晚于 start_time'),
    (timed(start='2020-01-01', end='2020-02-01'), '有效期已经结束'),
    (timed(start='明天', end=f'{END}'), '无法识别的时间'),
    (timed(start=f'{START} 08:00:30'), '精确到分钟'),
    (timed(start='08:00', end='18:00'), '无法识别的时间'),
    (timed(long_link='abc'), '老化小时数'),
    (timed(long_link='24001'), '最大 24000'),
    (timed(base=[*VALID[:3], 'udp', 'permit']), '长连接只适用于 TCP'),
])
def test_invalid_validity_or_long_link_never_exports(tmp_path, row, expected):
    result, payload, output = invoke(tmp_path, [row], columns=TIMED)
    assert result.returncode != 0
    assert expected in payload['error']
    assert not list(output.rglob('*.txt'))


def test_one_ticket_has_one_validity_window(tmp_path):
    rows = [timed(), timed(base=['10.0.0.2', *VALID[1:]], start=f'{START} 09:00')]
    result, payload, output = invoke(tmp_path, rows, columns=TIMED)
    assert result.returncode != 0
    assert '只能有一个有效期' in payload['error'] and '第 2 行' in payload['error'] and '第 3 行' in payload['error']


@pytest.mark.parametrize('ticket', ['工单1', '1REQ', 'all', 'R' * 33])
def test_ticket_must_name_a_device_time_range(tmp_path, ticket):
    result, payload, output = invoke(tmp_path, [timed()], columns=TIMED, ticket_id=ticket)
    assert result.returncode != 0
    assert '工单号需以英文字母开头' in payload['error']


@pytest.mark.parametrize('vendor', ['H3CF1000', '天融信', '山石'])
def test_other_vendors_refuse_validity_and_long_link(tmp_path, vendor):
    def change(topology):
        topology['firewalls'][0]['type'] = vendor
    result, payload, output = invoke(tmp_path, [timed(long_link='')], columns=TIMED, topology_change=change)
    assert result.returncode != 0
    assert '暂不支持有效期和长连接' in payload['error']
    # Without the settings these vendors still generate as before.
    result, payload, output = invoke(tmp_path, [timed(start='', end='', long_link='')], columns=TIMED,
                                     topology_change=change)
    assert result.returncode == 0, payload
