# 平台数据库 bootstrap 与迁移

核对日期：2026-09-27。数据库为平台公共基础设施；平台管理执行、备份恢复及数据库生命周期；App 声明并维护其业务 Schema。

## 唯一管理入口

Windows 在仓库根目录执行：

```powershell
# 安装依赖并启动中间件后，首次安装或升级时执行
.\scripts\windows\bootstrap.ps1 -Action apply
# 只读检查，不建表、不迁移、不填充数据
.\scripts\windows\bootstrap.ps1 -Action check
# 日常启动，只校验数据库就绪
.\scripts\windows\start.ps1 -SkipLangfuse
```

两个 PowerShell 操作都调用同一个平台实现：

```text
python -m src.platform.database.bootstrap apply
python -m src.platform.database.bootstrap check
```

`scripts/init_db.py`、`scripts/migrate_db.py` 仅保留旧命令兼容转发，不再有独立迁移逻辑。统一入口只升级到当前完整发行版；不支持旧入口的部分 `--revision` 升级。

## 执行顺序与责任

| 阶段 | 负责人 | 内容 |
| --- | --- | --- |
| 连接与串行锁 | 平台 | 验证 PostgreSQL，使用会话 advisory lock 防止重复部署并发执行 |
| 公共历史迁移链 | 平台 | Alembic 平台修订，保留既有历史及数据 |
| 认证与授权 Schema | 平台 | 创建 `django` namespace，执行 Django migrations |
| App Schema | App 定义、平台执行 | 按依赖顺序执行清单声明的迁移，独立 `alembic_app_*` 版本表 |
| 初始数据 | 所属组件定义、平台执行 | 幂等初始化；记录成功版本，失败不记完成 |
| App 登记 | 平台 | prepare 登记已就绪包，不自动启用、不新增使用权 |

Docker 的数据库容器只管理 PostgreSQL 实例、初始数据库和持久卷，不挂载业务初始化 SQL。Windows 应用继续运行在本机；Linux Compose 合并为一个 `migrate` 作业，调用同一 bootstrap，业务容器等待其成功。`deployment/requirements/bootstrap.txt` 是该作业的依赖组合，不增加常驻服务。

Gateway、Worker、普通启动不执行 Schema 迁移。Gateway 原来的检查项、实体身份策略、文本比较配置初始化已移至规则治理 App 的 bootstrap。Skill 启动只加载文件/内存；初始目录写入由平台 bootstrap 执行，用户主动更新 Skill 的业务操作仍可更新目录。初始化不调用向量模型，也不导入本地临时 Parser 模板。

## App Schema 所有权

继续以 `src/apps/<package>/app.yaml` 的 `database.tables` 为所有权声明，重复所有权会拒绝部署。新增 App 使用 `database.mode: package`，迁移文件留在 App 目录，平台只调度执行，不复制业务 DDL 到启动脚本。

当前 inspection、rule-governance、official-documents 的历史表仍由已有平台历史修订创建，清单的 `legacy` 表示历史链来源，**不表示未来可以随意改写这些历史修订**。原有表不换库、不改表名。后续需要新 DDL 时，通过已有 `handoff` 契约切换到 App 独立迁移链，声明继承修订、表及列类型/可空性，再追加包内修订。

平台 Alembic 自动生成排除 App 声明拥有的表，避免把业务表修改或删除错误生成到平台修订。共享会话、设备清单、证据等平台服务的数据仍按平台契约维护；数据被多个 App 使用不意味着多个 App 同时拥有该表。

规则治理的初始数据声明示例：

```yaml
database:
  mode: legacy
  bootstrap: src.apps.rule_governance.backend.bootstrap:initialize
  bootstrap_version: 1
```

hook 必须位于所属 App 中；版本为正整数。`netops_bootstrap_steps` 由平台维护，以组件和初始化版本记录完成。普通重启和重复 apply 不重新导入同版本目录；用户删除、停用、编辑的业务数据不会因此被重新初始化。新增默认数据需要显式升级初始化版本，hook 必须可重试，不能依赖每次启动覆盖数据。

## 升级、失败与恢复

1. 备份数据库；修改已启用 App 前，先用旧代码完成停用/排空。
2. 替换文件并安装依赖，执行 `bootstrap apply`。仍在启用且摘要已变化的 App 会拒绝迁移。
3. 执行 `bootstrap check`，启动服务并通过现有部署校验后显式启用所升级 App。
4. 任一迁移或初始化失败即退出，不启动服务、不自动 stamp、不降级、不重置数据库。修复原因后重跑 apply；已完成阶段由迁移版本和初始化记录跳过。

平台迁移、Django 迁移、App 迁移各自保持事务边界，**不是整库单一原子事务**。版本表是恢复依据。并发索引迁移需要 autocommit，因此平台串行锁采用 session lock，进程退出连接关闭后锁自动释放。

已建表但从未接入 Alembic 的旧库，必须先核实备份和基线，才显式使用 `-AdoptExisting`；日常启动永远不自动接管。正常升级不需要该参数。创建管理员仍使用 `manage.py createsuperuser`，不会创建默认密码或恢复已删除用户。

## 验证记录

- 当前数据库升级成功；只读检查在 PostgreSQL `default_transaction_read_only=on` 下通过。
- 独立临时空库完整执行：apply → check → apply → check，均成功；临时库随后删除。
- 初始化失败不写完成记录，可重试；同版本重复调用不执行 hook；hook 不可指向其他 App。
- Windows 启动和 Docker 单一迁移入口、技能启动不写目录均有回归覆盖。
- 35 项单元测试、25 项 Django 测试通过；19 个 PowerShell 脚本语法检查通过，生产 Shell 入口通过 Bash 语法检查。
- Windows 完整启动验证通过，Gateway、Django、前端、Worker 与 Beat 就绪，7 个已启用 App 的部署检查通过。
- Docker 配置与依赖入口做静态验证；本次未完成整套 Linux 镜像构建和 Linux 服务部署验收。
