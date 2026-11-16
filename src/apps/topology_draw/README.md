# 拓扑绘图（topology-draw）

展示型应用：读取拓扑发现应用的当前快照（公开 HTTP API，前端直读），用华为产品图标
风格渲染拓扑图。展示用，不参与发现和计算。

- 数据：活跃网络的当前快照（`/apps/topology/...` 公开接口），设备图标按身份的
  `model` 字段映射（USG→防火墙、AR→路由器、CE→交换机），终端按 PC、其余按通用节点。
- 布局：按类型分层自动排布；人工拖拽的位置存浏览器 localStorage（按网络 id），
  刷新与重开不丢。
- 不读拓扑发现的内部代码：应用边界是它的公开 API。
- 后端是薄路由层：`/ai-edit` 走平台助手 DSH 的 drawio 模式（进程内调用），
  无独立数据库（`database.mode: none`）；画布 XML 的权威状态在前端，
  模型输出经 XML 真解析校验（id 唯一/形状白名单/端点与 parent 存在性）后规范化。

## 设备图标：导入与替换

图标源文件在 `assets/icons/`（随仓库版本管理），映射文件 `assets/icons.map.json`
（中文文件名必须映射：`{"汇聚交换机": {"kind": "aggregate", "label": "汇聚交换机"}}`；
kind 只允许小写字母/数字/下划线）。无位图的种类（server/generic）走内嵌 SVG 兜底。

导入/替换流程（PNG 放进 `assets/icons/` 后）：

```bash
# Windows
venv\Scripts\python.exe tools/import_icons.py
# Linux
venv/bin/python tools/import_icons.py

# 再从别的目录拿新图标（会先拷贝进 assets/icons/ 留存）：
venv/bin/python tools/import_icons.py /path/to/new/pngs --map src/apps/topology_draw/assets/icons.map.json
```

脚本自动重写 `frontend/iconPngs.ts` 并同步前端 PALETTE_LABELS、Kind 类型、
后端 KINDS 白名单；已有 kind 直接替换图标，新文件名即新种类。收尾两件事：

1. 新 kind 在 `src/platform/agent_runtime/dsh.py` 的 drawio 提示词 kind 清单里补一句；
2. 重新构建部署：`cd web/react_frontend && npm run build`，再跑 `start.ps1 -UpgradeApps @('topology-draw')`。

## 已知边界

设备重锚定后的数据面故障（如 trunk 撤放行）在状态表里不可见，
详见 `docs/network-twin-plan.md` 第 5/8 节 F1 的定论。
