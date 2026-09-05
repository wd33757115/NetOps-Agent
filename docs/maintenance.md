# 维护与验证

核对日期：2026-09-06。用于维护当前代码、文档与兼容边界，不记录可随环境变化的生产资产数量。

## 文档入口与更新

当前使用方式从 [文档中心](README.md) 查找。新增或修改功能时同步更新对应指南、接口契约与可执行测试，不另造一份重复的“最终方案”。已替代方案、已实施计划和一次性验收记录从工作目录移除，历史由 Git 保存。

`document-governance.yaml` 登记职责、状态和核对时间。只在实际核对后更新 `last_verified`；文档的 active 状态不是生产可用性的证明。

```powershell
venv\Scripts\python.exe scripts/docs/audit_docs.py --all-links --strict-unregistered
venv\Scripts\python.exe scripts/deprecations/audit_deprecations.py
venv\Scripts\python.exe scripts/check_utf8.py docs scripts/docs --mojibake
```

## 回归范围

从项目根目录执行。完整后端测试和部署冒烟需要独立测试数据库、队列和对象存储；部分测试直接访问数据库，不能把真实业务库作为默认测试目标。

```powershell
# 文档、依赖角色、当前 Workflow 上下文及正式报告渲染
venv\Scripts\python.exe -m pytest tests/unit/test_docs_governance.py tests/unit/test_dependency_profiles.py tests/unit/test_chat_workflow_context.py tests/unit/test_check_item_reporting.py -q

# PowerShell 语法检查，不启动服务
.\scripts\validate_ps1.ps1

# 前端：编码、lint、单元测试和构建
npm --prefix web/react_frontend run check
```

浏览器回归使用前端包中的 `test:e2e` 与 `test:visual`。视觉基线与 CI 使用 Windows Chrome。前端构建只证明资源可生成，不能代替业务回归；Mock 通过也不能代替真实设备样本与模型质量验收。

目标环境巡检就绪检查、产品门禁与真实样本回放脚本位于 [scripts/test](../scripts/test/)。执行前阅读脚本参数和数据副作用。当前生产判断必须重新取证，不再引用旧月份验收矩阵中的数量或结论。

## 删除代码的判断

1. 从实际入口检查导入、路由、组件挂载、插件注册、Skill 入口、任务名和配置引用。
2. 零文本引用只用于找候选。检查动态加载、CLI、持久化历史记录及外部兼容接口，确认替代路径正在使用。
3. 删除无调用实现及其专属样式和依赖；保留替代路径的行为测试。
4. 历史路径涉及数据库或持久任务时，按 [退出登记](implementation/deprecations.yaml)核对调用观测、数据写入、任务注册、替代覆盖及回滚证据。记录保留原因，不伪造“零使用”证据。

## 本次清理记录

- 移除 Streamlit 辅助 UI、独立依赖组、旧启动目标，以及未使用的 `app/config/settings.py` 重复配置。
- 移除失效的 Supervisor v1 启动选项和 `USE_SUPERVISOR_V2` 配置；实际图入口一直固定为 v2。
- 移除旧侧栏、旧会话面板、未挂载的规则候选组件及专属导航样式。现有页面使用统一 Shell 与当前规则工作区。
- 移除无人调用的 ITSM 上下文构造副本、旧 TextFSM 资产导入桥接和简版巡检 Excel 渲染器。当前流程分别使用插件上下文、Parser 资产库和完整审计报告渲染器。
- 继续移除无调用的 14 个兼容函数和内部辅助函数，包括旧图别名、设备采集包装、旧 Webhook 包装和全局 Value 别名合并器；仍被使用的按范围映射保留。
- 相对当前 HEAD，移除 49 份已跟踪过期文档及旧规则工作台静态稿，新增 8 份当前指南；同步合并重复设计、更新索引和引用。当前文档登记为 39 份，覆盖业务、架构、运维和 ADR。
- 修复 Makefile 对工作目录、Compose v2 与 core profile 的使用。该入口可用 `make -C deployment` 或 `make -f deployment/Makefile` 调用。
- 前端容器与安装脚本统一使用 lockfile；清理无效的旧构建参数。生产 Compose 端口列表改为显式替换，并通过实际配置解析测试校验仅绑定 loopback。

- 移除未接入执行图的旧 Script Executor、设备话术编排器、Python Skill 扫描器，以及不用的数据库包装、路径适配和全局缓存入口。
- 移除 TextFSM 旧 prompt/repair 包装、设备身份转接模块和公文按文本猜排版的旧导出函数；实际模型会话、标准 Word 渲染和 Skill 入口保留。
- 将 Parser IR 中仍有调用的 evidence-line 编译辅助独立为 `evidence_line.py`，移除已无消费者的 IR 类型和编译函数。
- 移除不再使用的 Streamlit 端口和 Qdrant collection 配置；Qdrant 连接诊断仍有调用，保留 host/port。
- 修正 Skill 校验脚本的 JSON 失败退出码、合法 false 配置、显式空触发词及文件末尾误报，并增加 CLI 回归。
- 核对 26 份 Skill 随附说明和 6 份 Workflow README，补齐章节与缺省声明；更新历史巡检、规则生成、审批和 ITSM 的实际流程。
- 移除最后 10 个未使用类型，包括旧回调响应模型及其专属子模型、旧参数模型和无消费者的异常类型。
- 修正一个在 HEAD 上同样失败的陈旧 Parser 测试：分别验证“空正样本结果被阻断”和“有解析结果但语义复核拒绝”，没有放宽运行门禁。

### 本轮验证记录

- 生成、复核、标准规则、Adapter 和报告投影的专项回归：93 项通过。
- 最新代码的 TextFSM、Skill 加载/安全/缓存、公文、设备巡检及观测回归：142 项通过；其中包含校验脚本与两个 Parser 失败场景。
- 此前移除辅助函数后的执行图、Workflow、编译器和仓库边界回归：72 项通过。
- 前端完整检查先前通过；本轮确认 TextFSM 详情组件 19 项测试及生产构建通过，本轮后续只改后端、测试与文档。
- 文档登记、全链接、UTF-8、修改文件语法与差异空白检查通过。兼容退出审计仍有一项到期提示，实际退出条件未满足，未伪造完成证据。

以上测试组有重叠，不相加作为独立测试总数。日志保存于本地 `.runtime/test/cleanup-*.log`，不提交运行日志或设备数据。

### 收尾核对与保留依据

本轮清理覆盖过期方案与重复文档、当前使用与运维指南、Skill/Workflow 说明，以及前后端无调用实现、旧配置和部署入口。已核对的完成证据：

| 范围 | 证据 |
| --- | --- |
| 过期文档移除与当前文档入口 | Git 删除清单、39 份登记、全链接审计通过；8 份新指南可从文档中心进入 |
| Skill / Workflow 说明 | 26 份 Skill 必填项全部通过，6 份 README 对照 YAML 和调用服务更新；本地链接检查通过 |
| 删除代码的影响 | 875 份 Python 文件语法与导入扫描通过，未发现指向本轮删除模块的导入；前端 lint、168 项测试和构建通过 |
| 当前修改的后端链路 | 收尾 96 项回归通过，覆盖 Workflow、ITSM、资产提交/审批、事件消费、离线导入、Skill 元数据及文档/退出治理；另有 15 项执行器、设备备份/巡检及同步公文回归通过 |
| 前期删除的业务辅助路径 | 已完成上文 93、142、72 项专项回归；测试组重叠，不重复计数 |

26 份 Skill 的配置元数据（不含说明正文）与修改前一致；已有 YAML 配置值未改动。23 份使用可解析脚本入口，设备备份、设备巡检和同步公文使用现有任务或 handler 映射，不应因没有 `scripts/run.py` 而删除。内部 Skill 显式声明 `triggers: []`，未增加聊天触发词。

`patrol-attested-offline-replay`、`patrol-diff`、`state-normalizer` 原先缺少版本声明，现在显式使用元数据加载器原有的默认版本 `1.0.0`。执行记录此前经另一处缺省逻辑写为 `0.0.0`，此后会写为 `1.0.0`；历史记录不回填。校验器仍对 20 份 Skill 提供可选项或风格建议，这些不是必填项失败，未为消除提示随意更改业务分类。

以下内容经过引用核对，保留为仍有职责的实现：

| 保留内容 | 当前用途与后续退出条件 |
| --- | --- |
| 旧 Parser 请求的拒绝/历史路径 | 生成入口仍识别旧请求并返回只读/升级提示；到期审计有提示，退出登记中的实际调用观测和回滚证据仍未满足 |
| 旧 command-profile 与命令资产接口 | 禁用旧激活写入的明确响应、现有配置读取与按命令下钻仍有调用，不能整段删除 |
| 历史巡检批次读取 | Workflow API 合并没有新 manifest 的旧批次；删除将使历史记录不可见 |
| 旧巡检报告投影 | 已注册的报告任务对缺少新 runtime manifest 的批次仍走该分支；新批次使用 CheckItemReportMaterializer |
| 数据库模型与迁移 | ORM 表登记、迁移和历史数据结构不能以 Python 名称零引用作为删表依据；未执行数据库清理 |
| 按范围的 TextFSM Value 映射 | 仍用于字段兼容；只删除无消费者的全局合并包装 |
| Skill 示例、知识库 SOP 和测试样本 | 分别是作者模板、检索内容和回归输入，不是过期实现方案 |

这些保留项不标记为“已退出”，具体门禁见[退出登记](implementation/deprecations.yaml)。本次验证不等于真实设备、模型或生产部署验收；未重启服务、修改生产数据或执行外部工单回调。
