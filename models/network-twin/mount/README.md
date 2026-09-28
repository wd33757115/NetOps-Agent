# 原生 Schema Mount 验证

本目录验证 **RFC 8529 设备内 NI → RFC 8528 挂载路由树 → 父接口引用**。全部 Schema 使用已锁定模型包，没有新增 YANG。blue/red、eth0/eth1 和 192.0.2.0/24 是合成配置例子，不代表 VSR 实机状态或新建了 VRF。验证运行时不接入应用，不访问设备。

## 复现

先按上级 README 构建 41 文件模型包。使用已有 Docker Desktop，在项目根目录运行：

```powershell
New-Item -ItemType Directory -Force .runtime/ietf-study/mount-tool | Out-Null
$mountToolSource = Get-Content models/network-twin/mount/libyang-source.lock.json -Raw | ConvertFrom-Json
Invoke-WebRequest -Uri $mountToolSource.url -OutFile .runtime/ietf-study/mount-tool/libyang.tar.gz
docker build -t netops-ietf-mount-verify:5.8.6 -f models/network-twin/mount/Dockerfile.source .runtime/ietf-study/mount-tool
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_mount_cases.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_mount.py
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/audit_ietf_mount_associations.py --fetch
```

源码固定 CESNET/libyang v5.8.6 提交及归档 SHA-256，Dockerfile 在解包前验证摘要。基础镜像固定 digest；编译依赖来自 Debian 软件源，版本保存在验证报告中，**未承诺镜像字节级可复现**。下载只发生在准备/构建阶段。每次验证按已解析 image ID 创建临时容器，禁用网络、只读文件系统、非特权用户，只读挂入模型和测试实例，用完删除容器。保留本地验证镜像供重放；不启动新应用服务。

## 验证结果

`reports/mount-validation.json`（位于上一级目录）记录 8 项结果、具体命令、镜像 ID、工具/依赖版本和输入摘要。正例同时检查输出仍保留两棵挂载路由树；负例必须有预期诊断，崩溃不算有效拒绝。

| 例子 | 预期结果 |
|---|---|
| shared-schema，两个 VRF 各引用自己的父接口 | 通过，挂载路由均保留 |
| blue 路由引用绑定 red 的 eth1 | leafref 拒绝 |
| 引用不存在的接口 | leafref 拒绝 |
| shared-schema 未提供 parent-reference | leafref 拒绝 |
| inline 引用父层接口 | leafref 拒绝 |
| inline 在自己挂载树内提供接口 | 通过 |
| 挂载路由中加入自定义字段 | 拒绝 |
| 导出包含子 Schema 未实现 augment 的整个父接口 | 明确的跨 context Schema 错误 |

依据 [RFC 8528 §3.3、§4](https://www.rfc-editor.org/rfc/rfc8528.html#section-3.3)，shared-schema 可通过 parent-reference 扩展可访问数据树，inline 则自包含。实例使用 [RFC 8529 §6](https://www.rfc-editor.org/rfc/rfc8529.html#section-6) 模块中的真实 `bind-ni-name`，不照搬正文示例中的旧名称 `bind-network-instance-name`。按当前 NI 筛选后只导出路由引用所需的接口 name/type 及其祖先；不修改 leafref 或给它设置 require-instance false。

`parent-library.json` 同时提供 RFC 8525 与兼容的 RFC 7895 数据。`extension.xml` 是 yanglint 回调输入；5.8.6 的 CLI 回调仍按 XML 解析。shared-schema 的库位于对应挂载实例下；inline 回调输入在根部提供子模型库。后者是工具回调约定，不是把父 datastore 的 YANG Library 替换成子模型库。实际服务应按每个 inline 实例提供库，当前 CLI 例子只验证相同子 Schema。

Debian libyang 3.12.2 对本组共享挂载实例发生 SIGSEGV（返回 -11），没有据此修改标准。`Dockerfile` 保留该版本的复现环境；失败报告另存 `../reports/mount-3.12.2-validation.json`。采用源码构建的 5.8.6 完成验证，并保留准确错误诊断。参考 [上游挂载说明](https://github.com/CESNET/libyang/blob/47351e59e2965e350f9d4098f15ecbe6b3850f6f/src/tree_data.h)。

## 运行状态与部分观测（IETF-1.5）

```powershell
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_operational_cases.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_mount.py --cases models/network-twin/mount/operational --output models/network-twin/reports/operational-validation.json
```

`operational/` 的 16 个生成文件与 `../reports/operational-validation.json` 覆盖 21 项检查。完整合成例子包含父接口状态/计数器、父及子 YANG Library、两个 NI 的挂载配置及 RIB 状态。parent-reference 额外提供选中接口的 oper-status/statistics，使子树的可访问接口满足原生约束；没有导出整个接口或改掉 NI 隔离。`2026-01-01T00:00:00Z` 和计数器 42 都是合成测试值，绝不填入 VSR 实例。

验证明确区分三种模式：

| 模式 | 固定工具调用 | 验收含义 |
|---|---|---|
| `strict` 完整约束检查 | 5.8.6 不传 `-t`，传 `--merge` | 检查完整树约束；缺 discontinuity-time 拒绝，完整合成例子无诊断通过 |
| `data` 运行状态模式 | `-t data --merge` | 启用 LYD_VALIDATE_OPERATIONAL，mandatory 缺失可能警告并返回 0；警告必须保留，不能称完整性已证明；跨 VRF leafref 仍拒绝 |
| `get` 部分数据解析 | `-t get` | LYD_PARSE_ONLY；VSR 两棵部分接口树逐字段往返不变，不插入默认值；不执行完整语义校验，不证明引用解析成功 |

这些开关语义核对了[固定版本 yl_opt.c](https://github.com/CESNET/libyang/blob/47351e59e2965e350f9d4098f15ecbe6b3850f6f/tools/lint/yl_opt.c) 和 [validation.c](https://github.com/CESNET/libyang/blob/47351e59e2965e350f9d4098f15ecbe6b3850f6f/src/validation.c)。该版本帮助中“不传 -t 等同 data”的说法不足以反映 flags 差别，因此不只依赖帮助文字；换工具版本需重新跑全部用例。`--merge` 先解析所有兄弟模块再统一验证，避免直接解析挂载状态树时提前检查 YANG Library 产生 module-set-id 缺失诊断；没有关闭约束或删库。报告保存实际参数。

反例验证 `get` 模式仍拒绝非法 oper-status、identity、日期、计数器、IP、前缀长度、错误层级和自定义字段。另一个反例特意证明跨 VRF 引用在 parse-only 下能保留、在完整校验下失败，所以 parse-only 绝不能充当路由关联验收。未使用会跳过未知字段的 `-n`。正例检查输入保留；get 要求整个 JSON 数据树相等，完整检查只允许增加 Schema 默认节点且不允许原有列表条目丢失。验证输出只作报告，不能拿工具补的默认值覆盖采集证据。

**方案纠偏**：[RFC 8342 §5.3](https://www.rfc-editor.org/rfc/rfc8342.html#section-5.3) 允许 operational 在特定情况下违反所列语义约束，以返回实际在用状态；层级、标识符和类型约束不能违反。CLI 部分采集既不等同完整 operational datastore，也不能据此宣称设备存在合法例外或标准 deviation。VSR 文件仍保持 partial，严格诊断保存，但其中类型合法的观测可保留用于后续关联核验，无需伪造统计时间。

## 仍未完成

这完成合成状态挂载及部分观测的离线验证，不是设备完整状态、NETCONF/RESTCONF 服务、NACM 授权或 VSR 实机验收。VSR 已采样本缺统计必填值，保持 partial 状态。拓扑虚拟节点→NE→设备树的关联也没有因 NI 挂载通过而自动建立；相关审计见 `../associations.md`。
