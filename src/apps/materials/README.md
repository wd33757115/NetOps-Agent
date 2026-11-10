# 资料管理

完整业务包：资料维护 API、本地 Word 解析、CHM 产品文档集导入与阅读、原文件保存、不可变修订、
前端页面及包内迁移。工作台通过 app.yaml 自动注册，应用 ID 为 materials，入口 /apps/materials。
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
- POST /docsets/uploads、POST /docsets/imports：申请直传地址；上传完成后开始解析。
- GET /docsets/imports、/docsets/imports/{job}：作业列表与核对预览；/pages、/titles、/figures 抽看解析结果。
- POST /docsets/imports/{job}/publish、DELETE /docsets/imports/{job}：发布（新文档集或新修订）、取消。
- GET /docsets/{id}/toc、/toc/locate、/pages、/figures/{sha}.{ext}、/search：按修订浏览目录、页面、图片与全文搜索。
- POST /docsets/{id}/revisions/{revision}/source-link：原 CHM 的 5 分钟下载链接。

原文件以 SHA-256 作为对象键。对象存储与数据库之间没有分布式事务；
存储成功但数据库失败时可能留下未被引用的同摘要对象，不会发布不完整修订。
相同原文件 SHA-256（文本用正文 SHA-256）在未删除资料中拒绝重复导入，停用资料也参与去重。
唯一索引防止并发创建重复资料；更新当前资料允许复用原文件，删除后允许重新导入。
网络结果不明时刷新列表核对，重试不会重复创建同一文件。
停用/删除只影响后续问答的资料快照；已开始的问答保留当时的快照，已有引用可按修订查阅。

## 产品文档集（CHM）

厂商 CHM 产品文档（如 1 GB、2 万页的交换机文档）整份导入为一个「产品文档集」，不当作一份 Word：
每个 HTML 页面一页，保留目录树，另生成命令、告警、日志索引。问答读取方式见 ../assistant/README.md 0.11.0。

上传 → 后台解析 → 核对预览 → 发布，作业状态为 uploading → queued → running → ready →
publishing → published，出错为 failed；待核对 7 天未发布为 expired，取消或停用应用为 cancelled。

- 浏览器经 `/object-storage/` 预签名直传 MinIO（默认上限 4 GiB），请求里不解析文件。
- 解析在 `app.materials` 队列执行：7-Zip 先列清单并核对限额，再只解出 HTML、目录和图片；
  HTML 用标准库当文本解析，不执行脚本、不加载外部资源、不调用模型。
- Linux 镜像使用 `7zz`；Windows 安装 7-Zip（默认路径可自动发现），或用 `MATERIALS_SEVENZIP` 指定。
- 限额可用 `MATERIALS_DOCSET_<名称>` 覆盖，如 `MATERIALS_DOCSET_UPLOAD_BYTES`；超限时作业失败并写明哪一项。
- 页面转为 Markdown：标题层级、编号步骤、表格行列、命令代码块、说明/注意框保留；图片原样保存，图中文字不提取。
- 每页开头两行固定：第 1 行为目录位置，第 2 行为类型、特性和原页面路径。内容完全相同的页面只保留一份。
- 发布与 Word 一致：核对缺失提示后整份发布；同一文件、同一厂商+产品+版本不重复发布，
  同一版本的新文档在原文档集上更新修订。改标题和元数据会生成新修订，页面不变。

问答缓存：发布或改元数据后，`app.materials.materialize_docset` 把该修订写到 `.runtime/materials-docsets/<修订>/`
（`MATERIALS_DOCSET_CACHE` 可改位置，默认上限 20 GiB，按最近使用淘汰，缺失时首次使用再生成）。
读法技能 `skills/product-docs/SKILL.md` 随本包发布，由问答复制进每轮技能目录，不登记为可执行技能。

型号建议：发布预览和「编辑资料信息」在「适用型号与别名」下列出建议，点选后才写入，不产生新修订。来源一是文档集
自己标题含「版本配套」页面里「设备名称 / 发布版本中的设备信息」两列的型号（样本 30 个），二是设备台账里
同厂商、没有任何文档集写过的型号。接口：GET /docsets/{id}/model-suggestions、GET /docsets/imports/{job}/model-suggestions?vendor=。

修订对比：阅读页「修订对比」选基准修订，按目录顺序列出新增、删除、正文变化的页面（按路径对齐、比正文 SHA-256，
不含重复页和 README/TOC/索引）；点开看单页逐行差异（GET /docsets/{id}/compare/page?path=&before=&after=，最多 2000 行）。
汇总沿用 GET /documents/{id}/compare 的返回结构。

插图识读：问答需要时（不在导入时），`figure_reading` 把一张插图缩到长边 1600 px 的 PNG，交给视觉模型
（默认 `deepseek-v4-flash-vision-exp`，`MATERIALS_FIGURE_MODEL` 可改）转写文字、连线和标注位置；
先用低强度推理（45 秒），没有结果再关推理重试一次（20 秒）。结果按图片 SHA-256、模型和提示版本
存在 app_materials_figure_readings，同一张图只识读一次。识读不是手册原文，引用时连同原图一起展示。

存储：正文按 SHA-256 存在 app_materials_texts，修订的文件清单在 app_materials_docset_files，
图片在 MinIO `apps/materials/figures/<sha>.<ext>`，原 CHM 在 `apps/materials/sources/<sha>.chm`。
原 CHM 体积可达数 GB，不经接口转发：下载时发放 5 分钟有效的预签名链接，同样经 `/object-storage/`。

## 迁移和部署

materials_0004 增加插图识读缓存表 app_materials_figure_readings。
materials_0003 增加资料类型 kind（默认 document）和文档集的正文、文件、图片、导入作业四张表，只新增不改旧数据。
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
