# 助手资料与 DSH 验证工具

## 当前回归与评测（2026-09-21）

正式版本为 Assistant 0.4.1 / Inspection 1.1.0，执行器仍为 DSH 0.1.5rc1。
下文 P6-B/C 属历史记录；Linux 回收、正式 App MCP 和只读业务证据现已有测试，
基础交付见 [验收报告](../../docs/assistant-foundation-validation.md)，最新修正见
[稳定性记录](../../docs/assistant-runtime-stability.md)。
0.4.1已将原30题全部作为看过的回归题，不再把其中原8道保留题作为新的独立验收集。
后续质量验收需另建未用于调优的保留集。报告新增 implementation_sha256，覆盖运行器、边界、资料适配及评测代码，
避免动态引用提示变化后仅用静态系统提示摘要标识实验。

```powershell
# 只在本地从已经提取的手册选择五个小片段，不访问模型。
.\venv\Scripts\python.exe scripts/assistant/prepare_evaluation.py --sample .runtime/assistant-eval/9cd743515ec7547b/sample.md --output .runtime/assistant-evaluation/dataset.json
# 默认只校验题集；题目、参考答案、引用要点与保留集标记均可审阅。
.\venv\Scripts\python.exe scripts/assistant/evaluate.py --dataset .runtime/assistant-evaluation/dataset.json
# 显式调用真实模型；参考答案和评分规则不会进入模型上下文。
.\venv\Scripts\python.exe scripts/assistant/evaluate.py --dataset .runtime/assistant-evaluation/dataset.json --live --split dev --output .runtime/assistant-evaluation/new-dev
# 固定提示和配置后再运行保留集；失败会返回非零，不能删除失败样本来提高通过率。
.\venv\Scripts\python.exe scripts/assistant/evaluate.py --dataset .runtime/assistant-evaluation/dataset.json --live --split holdout --output .runtime/assistant-evaluation/new-holdout
```

重新制备前保留原数据集及其摘要；不要覆盖已经用于验收的报告。自动评测检查答案要点、实际读取的证据、
禁止结论与终态，同时记录真实 token、模型轮数、工具次数、耗时、数据/提示摘要。单题最多12000字符资料，
本次五个真实片段合计2028字符，不读取完整 Word 发送到模型。版本、前提和语义正确性仍需领域审阅。

Linux SDK / MCP / 取消与进程回收可在无网络容器内复现（构建镜像需要下载依赖）：

```powershell
docker build -f scripts/assistant/Dockerfile.verify -t netops-assistant-verify:local scripts/assistant
$taskRoot = (Resolve-Path .).Path
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 1g --mount "type=bind,source=$taskRoot/src,target=/work/src,readonly" --mount "type=bind,source=$taskRoot/tests,target=/work/tests,readonly" netops-assistant-verify:local
```

镜像不包含项目 .env、用户资料或凭据；源码只读挂载，模型为本地替身，真实SDK和MCP照常运行。
这验证 Linux 进程和协议行为，不代替目标服务器或真实设备的交付验收。

`prepare_docx_sample.py` 只做本地结构提取和确定性校验，不调用模型。
其默认 manifest 标记 `not_authorized` 代表提取脚本没有授予任何发送权限。
本项目用户后来明确允许华为云 Stack 样本的少量片段验证，仍禁止整份发送；
该授权与抽样结果在 P6 实施记录及本地 fragment/report 中单独记录，不修改原文件。

## 当前正式执行器验证（P6-C）

正常 venv 已使用 DSH SDK/runtime-bin 0.1.5rc1、MCP 1.26.0、sse-starlette 2.4.1。
锁定依赖见 `src/apps/assistant/requirements.lock`。下方 MCP 2.2.0 原型仅为历史实验。

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/test_dsh_runtime.py tests/unit/test_assistant_package.py tests/observability/test_chat_stream.py -q
.\venv\Scripts\python.exe scripts/assistant/dsh_runtime_probe.py --live --fragment .runtime/assistant-dsh-lab/fragment.json
```

第二条会访问真实模型，只允许提前选定的至多 1200 字符片段（本项目样本为 320 字符）；
默认禁止真实调用，必须显式传 `--live`。该脚本运行正式适配器及原生文件工具，禁用真实业务查询。
每轮最多 5 轮模型、6 次工具、60 秒，沿用正式单次请求/输出上限；它不会读取完整 Word 或 sample.md。
结果在 `.runtime/assistant-dsh-probe.json`，工作区/SDK home 在运行结束清理。
这是接入复现，不等于 P6 题集质量验收。

## SDK 依赖安装

早期隔离试验的 MCP 2.x 锁文件已删除。正式环境统一执行
`scripts/windows/install.ps1`，由 Assistant App 的 `requirements.lock` 约束 SDK、runtime 和 MCP。
执行器回归使用上面的 `dsh_runtime_probe.py`，不再向项目环境安装原型依赖。

## 可选片段验证

显式 `--live --fragment <JSON路径>` 才访问外部模型，使用已配置的
`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`LLM_MODEL`。不要将密钥写进命令或版本库。
JSON 需包含 `text`，可包含 `question` 和 `citation`；文件不超过 12000 字节，正文不超过 1200 字符。
每轮最多 3 次外部请求，每个请求 JSON 最多 16000 字符，单次回答上限 300 token。
不要将完整 sample.md 作为输入。报告只在 Git 忽略的 `.runtime/assistant-dsh-lab/` 中保存。

本次实际只选取 320 字符 BGP EVPN/RR 段落，调用外部模型 2 次，回答返回该片段引用，runtime 已回收。
这是单题链路验证，不是答案质量验收，也没有达到 P6 的 30–50 道真实题与独立保留题要求。

## 已发现的限制

rc1 的 `request_timeout_seconds=0.3` 没有终止整轮 `Session.run` 通知等待：
延迟替身仍在约 7 秒后正常完成。脚本因此另设整轮截止时间，关闭 runtime，并验证进程退出。
正式适配器必须继承这个外层时限和回收逻辑，不能只依赖 SDK 请求超时。
目前取消、超时及最小工具暴露仅在 Windows 原型中实测；正式 App MCP、Linux、异常进程树和并发资源限额仍待实施。

优先复用 [官方 Python SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk/README.md)
和 [MCP 客户端](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md)。
[原生文件工具](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/fs/tool-fs/README.md)
的读取不能依赖 [fs-sandbox](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/fs/fs-sandbox/README.md)
限制到 NetOps 授权资料库，因此正式业务仍由受控 MCP 接入已有 App 门禁与分段阅读能力。
