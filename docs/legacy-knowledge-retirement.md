# 旧知识库退出边界

核对日期：2026-09-22。当前问答已使用 DSH；原 LangGraph/Supervisor、RAG 知识库接口、索引模型及对应依赖/启动入口已退出，不作为检索备用。

旧数据库索引的退出由 `0102_remove_legacy_knowledge` 迁移处理。历史迁移保留，已删除的数据不能通过 downgrade 自动恢复。当前资料由 materials 管理，Assistant 按需检索，见[问答指南](assistant-guide.md)。

2026-09-27 已将旧 `knowledge_base`、`vectorstore`、知识库专用虚拟环境和旧模型缓存移出工作目录，归档到项目同级的 `netops-agent-archive/20260927-223314/`。旧数据库备份一并移入归档，未物理擦除。当前 materials 原件、修订、历史引用对象及用户桌面的 Word 原件不受影响。

归档范围和单项恢复步骤见 [维护与验证](maintenance.md)。运行状态以当前服务与包校验为准，不能沿用早期“尚未接通 DSH”的结论。
