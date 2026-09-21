# 维护与验证

核对日期：2026-09-22。当前使用说明从[文档中心](README.md)进入。

## 文档维护

新增功能同步更新对应指南、接口合同和测试。`document-governance.yaml` 登记职责与状态；历史交付报告标注 archived，不能作为当前运行说明。被完整替代的重复方案可以删除，先保留未提交文件备份，再清理本地链接和登记。

```powershell
venv\Scripts\python.exe scripts/docs/audit_docs.py --all-links --strict-unregistered
venv\Scripts\python.exe scripts/check_utf8.py docs scripts/docs --mojibake
```

本轮合并旧版实施方案与重复进度汇总，移除 Supervisor 执行指南、v2 过渡计划、批次进度总汇及已完成的资料工作区改进建议。当前架构、问答、资料、权限、工作台及部署入口已更新。原文备份在 `.runtime/workbench-personalization/docs-before/`，它不属于产品资料库。

## 回归与部署

```powershell
# 仅文档审计测试
venv\Scripts\python.exe -m pytest tests/unit/test_docs_governance.py -q
# 本地前端
npm --prefix web/react_frontend run test:unit
npm --prefix web/react_frontend run build
# 启动预检，不重启
.\scripts\test\start.ps1 -CheckOnly
```

后端涉及数据库、Broker 或对象存储的测试必须用隔离环境。前端构建不代替浏览器操作，模拟 API 不代替真实权限、设备或模型验收。工作台回归需覆盖不同账号、布局冲突、刷新、壁纸上传与恢复、宽屏右侧拖放、窄屏投影和历史会话打开。

业务 App 升级需按[能力包生命周期](capability-packages-guide.md)先停用排空，再更新并验证启用。不要在已启用包内只修改 README 后忽略内容摘要变化。

## 旧代码与数据

删除实现之前检查真实入口、动态注册、持久任务名、历史读取和兼容链接。历史迁移、证据原文件、旧报告和用户数据不能因零文本引用就删除。旧 Workflow 历史读取与取消保留，不表示新巡检/规则任务仍使用 Workflow。

退出登记见 [deprecations.yaml](implementation/deprecations.yaml)。旧知识库数据残留的处理范围见[退役边界](legacy-knowledge-retirement.md)，本次文档清理不执行磁盘资料或数据库数据删除。
