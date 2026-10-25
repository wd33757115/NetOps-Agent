---
name: official-document-writing
description: 使用公文应用创建、生成、读取与导出当前用户的公文。
---

# 公文写作

只使用平台根据 App 授权提供的工具。本文不授予权限，不改变个人文档归属。

1. 确认文种、用途和已有事实；未知姓名、时间、单位、文号使用待确认标记。
2. 使用 `official_documents__create_draft` 创建草稿，保留 document_id。同一次请求重试复用 request_id。
3. 使用 `official_documents__submit_assistant` 提交生成、改写、审查或答疑；先读取最新 revision 作为 base_revision。
4. 使用 `official_documents__get_operation` 查询任务。返回任务 ID 不代表完成。生成/改写建议由用户在公文页面核对接受，不声称尚未接受的建议已写入正文。
5. 使用 `official_documents__read_document` 核对当前版本，再用 `official_documents__export_document` 提交指定版本的 Word 导出。下载入口在公文页面，受 App 授权和所有者校验。

版本冲突时重新读取，不覆盖并发编辑。停止回答不意味着后台任务已取消，以业务 API 的任务状态为准。

`references/`、`checklists/`、`assets/` 是本包参考与排版资源。
`scripts/generate_official_document.py` 是本地结构化渲染兼容工具，不是 Agent 绕过工具门禁执行命令的入口。

旧 test-run 与聊天同步 Skill 已退役，请使用 `/apps/official-documents` 或上述受控工具。
