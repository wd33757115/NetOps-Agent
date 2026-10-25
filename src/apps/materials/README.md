# 资料管理

完整业务包：资料维护 API、本地 Word 解析、原文件保存、不可变修订、前端页面及包内迁移。
工作台通过 app.yaml 自动注册，应用 ID 为 materials，入口 /apps/materials。
本包不依赖 Assistant、DSH、Workflow 或模型服务。

## 使用边界

- 只有 materials App 使用权才能上传、发布和更新共享资料；应用内部不增加角色或逐文件权限。
- 已发布资料供 assistant App 用户按需检索、阅读与引用。问答应用保留只读资料 API，
  不再提供资料写入 API。两个应用的使用权分别授予。
- 原始 Word 完整保存至现有 MinIO，授权用户可下载；发布整个文件的可提取正文。
  整份发布不等于把全文送入模型。图片和拓扑图仍以原文件供核对。
- Agent 继续由 DSH 使用原生 glob/grep/read 读取已发布文本；不恢复向量库、嵌入服务或固定检索流程。

## 导入与来源

上传 DOCX → 本地解析预览 → 核对缺失提示 → 整份发布。
预览无存储写入、无模型调用；发布重新解析同一文件并核对文件及解析内容 SHA-256。
原文件上传后回读校验成功才创建修订。原文件内容寻址，修订保存文件摘要、章节和段落/表行位置。
并发更新使用 expected_revision，过期请求返回 409，旧修订和旧引用保持不变。

文件最多 16 MiB；解压内容最多 128 MiB，XML 单项最多 32 MiB、文件项最多 5000。
正文最多 2 MiB、最多 2000 章节；已去掉旧的 50 万字符限制。
浏览器按章节分页预览，搜索不改变发布范围；这些限额不改变问答的按需读取预算。
批量导入最多 10 个 Word 文件、合计 64 MiB；逐个解析、确认后发布，仅重试未成功的文件。
解析拒绝 DTD、外部实体、宏及异常压缩路径，不跟随 Word 外部关系、不执行嵌入内容。

文字段落与标题、表格行列及合并标记可提取；图片/拓扑图不做 OCR，
页码/版式、自动编号、脚注/尾注等不承诺还原。修订文字按接受插入、排除删除处理。
缺失提示出现在导入预览、Agent 目录元数据与引用回查中；不把这种提取称为完整保真转换。

## API

均挂载 /api/apps/materials 并要求 materials App 使用权：

- GET /documents；POST /documents；PUT /documents/{id}：列表与 Markdown/纯文本发布。
- POST /documents/preview：multipart 的 file 字段，解析 Word 并返回章节及提示。
- POST /documents/import：multipart 的 file、options 字段，发布整个文件或新修订（不传 section_ids；旧客户端只接受完整章节集合）。
- GET /documents/{id}/revisions：分页历史修订。
- PATCH /documents/{id}/metadata：只更新标题与元数据，生成新修订；保留正文和来源。
- PATCH /documents/{id}/status：停用/恢复，携带 expected_revision 与 expected_status。
- DELETE /documents/{id}：同样携带上述前提，移出资料库及新问答，保留历史修订和原件供引用回查；不提供物理销毁。
- GET /documents/{id}/compare?before=…&after=…：分页返回章节增删改和元数据差异；正文用现有阅读 API 按需对照。
- GET /documents/{id}/sections、/sections/{section_id}：指定 revision 的分页目录/正文。
- GET /documents/{id}/revisions/{revision}/source：验证摘要后返回原 Word 附件，无公开对象存储链接。

原文件以 SHA-256 作为对象键。对象存储与数据库之间没有分布式事务；
存储成功但数据库失败时可能留下未被引用的同摘要对象，不会发布不完整修订。
相同原文件 SHA-256（文本用正文 SHA-256）在未删除资料中拒绝重复导入，停用资料也参与去重。
唯一索引防止并发创建重复资料；更新当前资料允许复用原文件，删除后允许重新导入。
网络结果不明时刷新列表核对，重试不会重复创建同一文件。
停用/删除只影响后续问答的资料快照；已开始的问答保留当时的快照，已有引用可按修订查阅。

## 迁移和部署

materials_0002 增加状态和当前文件去重键，旧资料默认 active；已有重复记录保留 ID，不自动删除。
materials_0001 创建 app_materials_documents / app_materials_revisions。
若已有 Assistant 资料，逐字段复制全部旧修订，保留文档 ID、revision、原文字节及章节坐标。
旧 app_assistant_documents / revisions 表保留且不再作为运行时数据源；
仍由历史 Assistant 迁移链声明，不做删表、改历史迁移或双写。
新安装时旧表可以不存在，本包可独立安装。

**必须先用旧版本完成 Assistant disable/drain，再使用标准 app_packages prepare 安装新版本。**
标准 prepare 会拒绝尚在运行或排空中的旧包。禁止绕过该流程在旧 Assistant 仍可写时复制资料。
按依赖顺序先安装/启用 materials，再升级/启用 Assistant 0.6.0；依赖只表示包就绪，不自动授予用户权限。
启动脚本提示已更新为 -EnableApps materials,assistant，既有用户授权不变。
不可仅恢复旧 Assistant 二进制回滚，新写入资料不在旧表；回滚须恢复整套已核验备份。
