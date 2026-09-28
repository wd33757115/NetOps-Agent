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

## 仍未完成

这是配置数据和模型运行时验证，不是 operational 全量状态、NETCONF/RESTCONF 服务、NACM 授权或 VSR 实机验收。VSR 已采样本缺统计必填值，保持 partial 状态。拓扑虚拟节点→NE→设备树的关联也没有因 NI 挂载通过而自动建立；相关审计见 `../associations.md`。
