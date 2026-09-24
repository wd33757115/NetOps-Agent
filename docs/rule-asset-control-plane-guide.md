# 规则资产治理

当前入口是规则治理 App。样本导入、本地分割脱敏、待归类、确认检查项关联之后，才进入 TextFSM / V2 命令适配器制作。检查项拥有 Value、单位、必填约束和判断语义；变化、状态和事件规则由检查项语义生成。

## 当前执行路径

- TextFSM 提取设备输出；检查项版本约束字段与业务语义。
- CommandAdapter V2 将解析记录转换为标准数据，验证来源字段、实体身份和覆盖范围。
- 固定自动制作服务保存进度、执行验证并按已有授权发布；不依赖 Workflow。
- 运行包装配仅接受 V2 结构化适配器。文本检查项继续采用显式比较 Profile。
- 已发布目录提供 Parser、变化策略和事件规则的单项、批量停用与删除。操作同步停用依赖的活动运行包，历史执行事实保留。

## 已删除的旧机制

规则治理 1.4.0 删除独立 `netops_observation_contracts` 表及 ORM、Repository、生成器和前端页签；删除 Parser 发布后自动生成契约的种子链、旧样本规则生成入口、旧候选自动协调器和契约编辑修订链。Agent 工具使用相同的检查项规则服务。

旧 `/parser/observation-contracts`、`/parser/runtime-assets/resolve`、`/parser/runtime-assets/health`、`/generation-runs`、种子 `/start-generation`、`/generation-context` 和旧工程修订任务接口不再提供。请使用检查项 `/rule-context`、规则草稿及运行包装配接口。

旧候选的 `/rule-generation-jobs/{id}/revise`、`/execute-revision`、`/submit` 及其修订执行器、提交服务、提交 Skill 和 Workflow 也已删除；检查项规则草稿仍由当前业务服务创建、验证与审批。

迁移 `0103_retire_data_contracts` 拒绝删除仍被活动运行包引用的契约表，不使用 CASCADE。旧契约候选退出任务队列；历史报告、执行事实、审计和冻结资产摘要保留，不能重新通过旧契约运行。回退需恢复升级前备份和相应代码。

检查项的 `canonical_contract`、Parser 字段格式校验、API 输入输出结构不属于独立数据契约资产，继续使用。

## 代码与验证

- [规则上下文](../src/apps/rule_governance/backend/check_items/canonical_rule_context.py)
- [自动制作](../src/apps/rule_governance/backend/rule_control/command_autopilot.py)
- [运行包装配](../src/apps/rule_governance/backend/check_items/runtime_bundle.py)
- [退役回归](../tests/unit/test_retire_observation_contracts.py)

制作未完成时，先查看检查项关联、输入是否过期、Parser 验证和适配器覆盖缺口；不要重复创建任务。正式 API 以当前 OpenAPI 为准。

升级时先排空相关应用并备份数据库，再执行 `venv/Scripts/python.exe -m alembic upgrade head`，之后升级应用包、构建前端并通过部署校验启用。应用声明最低迁移版本 0103，防止遗漏删表迁移。

## 2026-09-23 本地执行记录

- 已执行 `0103_retire_data_contracts`，独立契约表已物理删除。27 条旧契约及 43 条相关生成任务状态已备份至 `.runtime/retire-observation-contracts/`；历史事实、报告与审计未删除。
- 回归：129 项后端、18 项前端通过；补充旧提交入口删除后的 28 项回归通过。两项命令资产测试在修改前即失败（目录样例缺失、未关联检查项时未创建 Parser 任务），本次未将其记为通过。2144 项单元测试可正常收集；前端生产构建与差异空白检查通过。
- 19 个活动运行包的只读装配结果与升级前完全一致：14 个就绪，5 个已有阻塞。第一轮在线检查中现行目录/制作/检查项语义接口正常，旧契约接口返回 404。
- 规则治理 1.4.0 已完成最终启用，当前状态 `enabled`。此前失效 PID 11352 导致 Beat 无法启动；在用户再次授权处理后，确认无存活进程并归档旧文件，恢复 Worker、Beat 并通过部署检查。现行目录/制作/检查项语义接口再次验证返回 200，旧契约接口返回 404。Windows 启停脚本已统一增加 PID 状态检查，完整启动和单服务启动都能安全清理失效文件，见[维护与验证](maintenance.md)。
- 采集中 Parser 草稿已恢复到原状态，样本数量未变，也未触发新的生成任务。
