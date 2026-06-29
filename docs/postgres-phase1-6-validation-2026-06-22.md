# PostgreSQL Phase 1-6 验收状态

更新时间：2026-06-22

## 当前结论

Phase 1-6 的两季度 PostgreSQL 在线验收已重新跑通。

当前 Docker 状态：

- `netops-postgres`：healthy，映射 `5432:5432`。
- `netops-redis`：healthy。
- `netops-rabbitmq`：healthy。
- Alembic 当前版本：`0015_device_dynamic_groups (head)`。

最新验收命令：

```powershell
venv\Scripts\python.exe scripts\test\phase1_6_e2e_validation.py
```

最新验收输出：

```json
{
  "success": true,
  "previous_run_id": "phase1-6-validation-previous-20260622_124201",
  "current_run_id": "phase1-6-validation-current-20260622_124201",
  "previous_observations": 11969,
  "current_observations": 11991,
  "change_count": 2196,
  "event_count": 0,
  "summary_json_path": "C:\\Users\\wangd\\PycharmProjects\\PythonProject\\netops-agent\\.runtime\\test-reports\\phase1_6_e2e_summary_20260622_124201.json",
  "summary_markdown_path": "C:\\Users\\wangd\\PycharmProjects\\PythonProject\\netops-agent\\.runtime\\test-reports\\phase1_6_e2e_summary_20260622_124201.md"
}
```

## 恢复步骤

先启动 Docker Desktop，确认 Docker engine 可用：

```powershell
docker ps
```

启动项目中间件 PostgreSQL：

```powershell
docker compose -f deployment/docker-compose.middleware.yml up -d postgres
```

执行数据库迁移：

```powershell
venv\Scripts\python.exe -m alembic upgrade head
```

执行 Phase 1-6 两季度验收：

```powershell
venv\Scripts\python.exe scripts\test\phase1_6_e2e_validation.py
```

如果 Windows venv launcher 对直接执行脚本报 `Unable to create process`，可以先用 pytest 调用脚本入口验证前置条件：

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_phase1_6_e2e_validation.py -q
```

## 已补强

`scripts/test/phase1_6_e2e_validation.py` 现在在 PostgreSQL 或其他前置条件失败时：

- 返回非 0 退出码。
- 输出结构化 JSON。
- 明确包含 PostgreSQL host、port、database、user。
- 明确给出 Docker compose、Alembic 和重跑命令。
- 不降级、不跳过 PostgreSQL、不写假报告。

对应测试：

```text
tests/unit/test_phase1_6_e2e_validation.py
```

## 历史成功报告摘要

最近可读历史报告：

```text
.runtime/test-reports/phase1_6_e2e_summary_20260620_182112.md
.runtime/test-reports/phase1_6_e2e_summary_20260620_182112.json
```

关键结果：

- 上一季度文件数：27
- 当前季度文件数：27
- Previous Observations：11969
- Current Observations：11991
- Observation Changes：2196
- Persisted Changes：2184
- Events：0
- Persisted Events：0

工程含义：

- `CommandSnapshot -> StateObservation -> NetworkChange -> report-generator` 已能基于两季度真实目录跑通。
- `event-builder` 链路可执行，但当前真实变化未命中事件规则，事件规则覆盖仍需继续提升。
- Parser 模板覆盖仍有缺口，历史报告中仍存在 `template_missing` 和少量 `parse_failed`。

## 最新报告摘要

最新报告：

```text
.runtime/test-reports/phase1_6_e2e_summary_20260622_135152.md
.runtime/test-reports/phase1_6_e2e_summary_20260622_135152.json
.runtime/test-reports/phase1_6_patrol_report_20260622_135152.md
```

快照解析状态：

- Previous：`parsed=99`，`parse_empty=4`，`template_missing=141`，`parse_failed=5`，`ignored=212`。
- Current：`parsed=98`，`parse_empty=4`，`template_missing=143`，`parse_failed=5`，`ignored=272`。

状态、变化和事件：

- Previous Observations：`11969`
- Current Observations：`11991`
- Previous Observations after default state normalization：`11944`
- Current Observations after default state normalization：`11966`
- Observation Changes：`148`
- Persisted Changes：`148`
- Events：`0`
- Persisted Events：`0`
- No-Event Changes：`148`

No-Event 诊断：

- Top fields：`cpu_1m=64`，`cpu_5s=39`，`cpu_5m=27`，`ip_address=7`，`speed=4`，`description=4`。
- Top entity types：`cpu=130`，`interface_l3=11`，`interface_l2=4`，`memory=2`，`environment_sensor=1`。
- Top commands：`show processes cpu=121`，`display ip interface brief=10`，`display cpu=9`，`show interface status=4`。
- Change types：`modified=148`。

本次修正：

- `change-detector` 的 Observation 模式不再把“当前 run 未采集对应命令”的历史 observation 判定为 deleted。
- deleted 只在当前 run 已采集同一设备、同一命令、同一实体类型、同一字段集合时才成立。
- added 同样受采集集合边界约束，上一季度未采集同一集合时作为 baseline，不算网络新增。
- Phase 1-6 验收脚本在 `use_state_observations=true` 时显式传入 previous/current run，避免旧验证 run 污染本次基线。
- `StateNormalizer` 增加默认状态语义治理：`uptime` 不进入状态事实，`description` 的 `--`/空占位值规范化为空字符串。
- 这避免跨季度命令集合/Profile 差异制造大量假变化；本次验收总变化从最初 `2196` 收敛到 `148`，且全部为 `modified`。

当前剩余差距：

- 事件规则未命中这批真实变化，后续需要扩展事件规则覆盖和事件测试样本。
- Parser 模板覆盖仍需继续补齐，当前覆盖率约 `41%`。Top 缺口为：
  - `show inventory=18`
  - `show environment power / show env power=11`
  - `show environment fan / show env fan=11`
  - `show system resources=11`
  - `display device manuinfo=9`
  - `display environment=9`
  - `display irf=9`
  - `display memory=8`
  - `display fan=8`
  - `display power=8`
- 剩余变化主要是 CPU 小幅波动、少量接口 IP/speed/description 变化、内存 free/used 和温度 reading 变化；当前事件规则正确地没有把这些低价值变化升级为事件。
- 默认事件规则已补齐计划中的 `MemoryHigh`，并兼容旧数据库中只 bootstrap 了 8 条默认规则的场景。
