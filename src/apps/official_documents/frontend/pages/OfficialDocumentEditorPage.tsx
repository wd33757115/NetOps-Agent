import React, { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Dropdown,
  Input,
  Modal,
  Segmented,
  Space,
  Spin,
  Tag,
  Tooltip,
} from "antd";
import {
  ArrowLeftOutlined,
  ArrowUpOutlined,
  DownloadOutlined,
  HistoryOutlined,
  SaveOutlined,
  CloseOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import { useQuery } from "react-query";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "@netops/context/AuthContext";
import { useDocumentSession } from "../hooks/useDocumentSession";
import DocumentEditor from "../components/documents/DocumentEditor";
import {
  contentText,
  documentApi,
  requestId,
  type DocumentContent,
  type DocumentOperation,
  type DocumentPatch,
  type OfficialDocument,
} from "../services/officialDocumentApi";
import { appRoutes } from "@netops/apps/appRoutes";
import "../components/documents/documents.css";

function PatchPreview({
  patches,
  content,
}: {
  patches: DocumentPatch[];
  content: DocumentContent;
}) {
  return (
    <div className="document-patches">
      {patches.map((p, index) => {
        const original = content.blocks.find((b) => b.id === p.block_id);
        return (
          <div key={`${p.block_id}-${index}`}>
            <span className="document-patch-label">
              {p.op === "insert_after" ? "插入位置" : "原文"}
            </span>
            <p className="document-patch-before">
              {original ? contentText(original) : "原段落已不存在"}
            </p>
            <span className="document-patch-label">建议修改</span>
            <p className="document-patch-after">
              {p.op === "delete_block"
                ? "删除该段落"
                : p.value
                  ? contentText(p.value)
                  : ""}
            </p>
          </div>
        );
      })}
    </div>
  );
}

function EditorSession({
  id,
  userId,
}: {
  id: string;
  userId: number | undefined;
}) {
  const navigate = useNavigate(),
    location = useLocation();
  const session = useDocumentSession(id, userId);
  const { doc, content, status, error, change, flush, replace, reload } =
    session;
  const [tab, setTab] = useState("公文助手"),
    [mobileTab, setMobileTab] = useState("文档"),
    [assistantVisible, setAssistantVisible] = useState(true);
  const [instruction, setInstruction] = useState(
    (location.state as { instruction?: string } | null)?.instruction || "",
  );
  const [allowPlaceholders, setAllowPlaceholders] = useState(true),
    [selection, setSelection] = useState<string[]>([]),
    [focusBlock, setFocusBlock] = useState("");
  const [busy, setBusy] = useState(false),
    [actionError, setActionError] = useState(
      (location.state as { submissionFailed?: boolean } | null)
        ?.submissionFailed
        ? "初稿提交失败，已保留写作需求，请重试。"
        : "",
    );
  const [historyOpen, setHistoryOpen] = useState(false),
    [preview, setPreview] = useState<{
      content: DocumentContent;
      version: number;
    } | null>(null);
  const [validation, setValidation] = useState<OfficialDocument | null>(null);
  const [requestedExport, setRequestedExport] = useState<{
    operation: DocumentOperation;
    title: string;
  } | null>(null);
  const downloadStarted = useRef<string | null>(null);
  const [downloadError, setDownloadError] = useState("");
  const operations = useQuery(
    ["document-operations", userId, id],
    () => documentApi.operations(id),
    {
      refetchInterval: (data) =>
        data?.some((op) => ["queued", "running"].includes(op.status)) ||
        (requestedExport &&
          !data?.some((op) => op.id === requestedExport.operation.id))
          ? 2500
          : false,
      enabled: !!doc,
    },
  );
  const exportOperation = requestedExport
    ? operations.data?.find((op) => op.id === requestedExport.operation.id) ||
      requestedExport.operation
    : undefined;
  const exportPending =
    !!exportOperation && ["queued", "running"].includes(exportOperation.status);
  const exportFilename = `${requestedExport?.title || doc?.title}-v${exportOperation?.base_version}.docx`;
  useEffect(() => {
    if (
      !requestedExport ||
      !exportOperation ||
      !["completed", "failed"].includes(exportOperation.status) ||
      downloadStarted.current === exportOperation.id
    )
      return;
    downloadStarted.current = exportOperation.id;
    if (exportOperation.status === "failed") {
      setActionError(exportOperation.error || "Word 导出失败，请重新导出");
      return;
    }
    setDownloadError("");
    void documentApi
      .download(id, exportOperation.id, exportFilename)
      .catch((e) => {
        setDownloadError(
          e instanceof Error ? e.message : "下载失败，请重试下载",
        );
      });
  }, [id, requestedExport, exportOperation, exportFilename]);
  const versions = useQuery(
    ["document-versions", userId, id, doc?.current_version],
    () => documentApi.versions(id),
    { enabled: historyOpen },
  );
  const working = operations.data?.some((o) =>
    ["queued", "running"].includes(o.status),
  );
  const canEdit = true; // App admission grants all document operations.
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setActionError("");
    try {
      await fn();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };
  const leave = () => {
    if (session.dirty.current)
      Modal.confirm({
        title: "还有未保存的修改",
        content: "保存成功后返回公文列表。",
        okText: "保存并返回",
        cancelText: "继续编辑",
        onOk: async () => {
          await flush();
          navigate(appRoutes.documents);
        },
      });
    else navigate(appRoutes.documents);
  };
  const send = (
    action: string,
    text = instruction,
    parent?: DocumentOperation,
  ) =>
    run(async () => {
      if (!text.trim()) throw new Error("请先输入写作或修改需求");
      if (action === "rewrite" && !selection.length)
        throw new Error("请先选中要修改的文字");
      const saved = await flush();
      await documentApi.assistant(id, {
        action,
        instruction: text,
        block_ids: action === "rewrite" ? selection : [],
        allow_placeholders: allowPlaceholders,
        parent_operation_id: parent?.id,
        base_revision: saved.revision,
        request_id: requestId(),
      });
      setInstruction("");
      setTab(action === "review" ? "审查" : "公文助手");
      await operations.refetch();
    });
  const accept = (op: DocumentOperation, issue?: string) =>
    run(async () => {
      const saved = await flush();
      replace(await documentApi.apply(id, op.id, saved.revision, issue));
      await operations.refetch();
    });
  const doExport = () =>
    run(async () => {
      const saved = await flush();
      const operation = await documentApi.export(saved);
      setRequestedExport({ operation, title: saved.title });
      setDownloadError("");
      await operations.refetch();
    });
  const finalize = () =>
    run(async () => {
      const flushed = await flush();
      const saved = flushed.validation_issues
        ? flushed
        : await documentApi.get(id);
      if (saved.validation_issues?.length) {
        setValidation(saved);
        return;
      }
      replace(await documentApi.mutate(id, "finalize", saved.revision));
    });
  if (!doc || !content)
    return (
      <div className="document-app document-loading">
        {error ? (
          <Alert
            type="error"
            message={error}
            action={<Button onClick={() => reload()}>重试</Button>}
          />
        ) : (
          <Spin tip="正在加载公文" />
        )}
      </div>
    );
  const visibleOps = [...(operations.data || [])]
    .reverse()
    .filter((o) =>
      tab === "审查" ? o.kind === "review" : o.kind !== "review",
    );
  const pendingQuestion = [...(operations.data || [])].find(
    (o) => o.status === "needs_input",
  );
  return (
    <div
      className={`document-app document-workspace mobile-${mobileTab === "文档" ? "editor" : "assistant"}`}
    >
      <header className="document-app-header document-edit-header">
        <button className="document-brand" onClick={leave}>
          <b>N</b>
          <span>NetOps</span>
        </button>
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={leave}>
          公文列表
        </Button>
        <span className="document-breadcrumb">/</span>
        <div className="document-header-title">
          <span>{doc.title}</span>
          <span className={`document-save-state is-${status}`} role="status">
            {
              (
                {
                  loading: "加载中",
                  saved: "已保存",
                  saving: "正在保存…",
                  pending: "等待保存…",
                  conflict: "版本冲突",
                  error: "未同步",
                } as Record<string, string>
              )[status]
            }
          </span>
        </div>
        <div className="document-header-actions">
          <Tooltip title="历史版本">
            <Button
              type="text"
              aria-label="历史版本"
              icon={<HistoryOutlined />}
              onClick={() => setHistoryOpen(true)}
            >
              <span>历史版本</span>
            </Button>
          </Tooltip>
          <Button
            type="text"
            aria-label="保存"
            icon={<SaveOutlined />}
            loading={busy}
            onClick={() => run(flush)}
            disabled={!canEdit}
          >
            <span>保存</span>
          </Button>
          <Button
            type="primary"
            aria-label="导出 Word"
            icon={<DownloadOutlined />}
            loading={busy || exportPending}
            disabled={busy || exportPending}
            onClick={doExport}
          >
            导出 Word
          </Button>
        </div>
      </header>
      <Modal
        open={!!validation}
        title="定稿前还需补齐以下内容"
        onCancel={() => setValidation(null)}
        footer={<Button onClick={() => setValidation(null)}>返回编辑</Button>}
      >
        <p>定稿需要补齐必填内容，并核实待确认信息。</p>
        <ul>
          {validation?.validation_issues?.map((issue) => (
            <li key={issue.id}>{issue.message}</li>
          ))}
        </ul>
        <ul>
          {[
            ...new Set([
              ...(validation?.content.unresolved_facts || []),
              ...(validation?.content.blocks.flatMap(
                (block) =>
                  contentText(block).match(
                    /【[^】]*(?:待确认|待补充)[^】]*】/g,
                  ) || [],
              ) || []),
            ]),
          ].map((fact) => (
            <li key={fact}>{fact}</li>
          ))}
        </ul>
      </Modal>
      {(error || actionError || downloadError || operations.error) && (
        <Alert
          className="document-error"
          type={status === "conflict" ? "warning" : "error"}
          message={downloadError || actionError || error || "助手任务加载失败"}
          action={
            status === "conflict" ? (
              <Space>
                <Button
                  size="small"
                  onClick={() =>
                    Modal.confirm({
                      title: "加载最新版本？",
                      content: "请先另存需要保留的本地稿。",
                      okText: "加载",
                      onOk: () => reload(true),
                    })
                  }
                >
                  加载最新版本
                </Button>
                <Button
                  size="small"
                  onClick={() =>
                    run(async () => {
                      const copy = await documentApi.create(
                        `${doc.title}（本地副本）`,
                        content.doc_type,
                        requestId(),
                      );
                      await documentApi.save(
                        copy.id,
                        content,
                        copy.revision,
                        requestId(),
                      );
                      navigate(`${appRoutes.documents}/${copy.id}`);
                    })
                  }
                >
                  另存本地稿
                </Button>
              </Space>
            ) : (
              <Button
                size="small"
                onClick={() =>
                  run(async () => {
                    if (downloadError && exportOperation) {
                      await documentApi.download(
                        id,
                        exportOperation.id,
                        exportFilename,
                      );
                      setDownloadError("");
                      return;
                    }
                    if (
                      requestedExport &&
                      exportOperation?.status === "failed"
                    ) {
                      await doExport();
                      return;
                    }
                    if (status === "error") await flush();
                    await operations.refetch();
                  })
                }
              >
                重试
              </Button>
            )
          }
        />
      )}
      <div className="document-mobile-switch">
        <Segmented
          options={["文档", "助手"]}
          value={mobileTab}
          onChange={(v) => setMobileTab(String(v))}
        />
      </div>
      <div
        className={`document-workspace-body${assistantVisible ? "" : " assistant-hidden"}`}
      >
        <DocumentEditor
          content={content}
          onChange={change}
          onSelection={setSelection}
          readOnly={busy || !canEdit || doc.status === "finalized"}
          focusBlock={focusBlock}
        />
        {assistantVisible && (
          <aside className="document-assistant">
            <header className="document-assistant-header">
              <button
                className={tab === "公文助手" ? "active" : ""}
                onClick={() => setTab("公文助手")}
              >
                公文助手
              </button>
              <button
                className={tab === "审查" ? "active" : ""}
                onClick={() => setTab("审查")}
              >
                审查
              </button>
              <Button
                type="text"
                aria-label="收起助手"
                icon={<CloseOutlined />}
                onClick={() => setAssistantVisible(false)}
              />
            </header>
            <div className="document-assistant-context">
              <FileTextOutlined /> 当前文档 · v{doc.current_version}
              <Tag>{doc.status === "finalized" ? "已定稿" : "草稿"}</Tag>
            </div>
            <div className="document-assistant-messages" aria-live="polite">
              {!!content.unresolved_facts.length && (
                <div className="document-review-results">
                  <strong>待确认信息</strong>
                  {content.unresolved_facts.map((fact, index) => (
                    <div
                      className="document-review-issue"
                      key={`${index}-${fact}`}
                    >
                      <p>{fact}</p>
                      <Button
                        size="small"
                        disabled={
                          busy || !canEdit || doc.status === "finalized"
                        }
                        onClick={() =>
                          Modal.confirm({
                            title: "已核实并补入正文？",
                            content: fact,
                            okText: "确认已补齐",
                            onOk: () =>
                              change({
                                ...content,
                                unresolved_facts:
                                  content.unresolved_facts.filter(
                                    (_, i) => i !== index,
                                  ),
                              }),
                          })
                        }
                      >
                        标记已核实
                      </Button>
                    </div>
                  ))}
                </div>
              )}
              {!visibleOps.length && (
                <div className="document-assistant-empty">
                  <span>✦</span>
                  <h3>
                    {tab === "审查" ? "检查当前公文" : "一起完成这篇公文"}
                  </h3>
                  <p>
                    {tab === "审查"
                      ? "检查文档结构、待确认事实和语言表达，逐项查看修改建议。"
                      : "描述写作需求，或选中段落让我帮你调整。修改建议会先展示，由你决定是否接受。"}
                  </p>
                </div>
              )}
              {visibleOps.map((op) => (
                <article className="document-operation" key={op.id}>
                  <p className="document-user-message">{op.instruction}</p>
                  {["queued", "running"].includes(op.status) ? (
                    <div className="document-task-running">
                      <Spin size="small" />
                      <span>
                        {op.status === "queued" ? "等待处理…" : "正在处理…"}
                      </span>
                      <Button
                        type="link"
                        size="small"
                        onClick={() =>
                          run(async () => {
                            await documentApi.cancel(id, op.id);
                            await operations.refetch();
                          })
                        }
                      >
                        取消
                      </Button>
                    </div>
                  ) : op.status === "failed" ? (
                    <Alert
                      type="error"
                      message={op.error || "处理失败"}
                      action={
                        <Button
                          size="small"
                          onClick={() =>
                            op.kind === "export"
                              ? doExport()
                              : send(op.kind, op.instruction)
                          }
                        >
                          {op.kind === "export" ? "重新导出当前版本" : "重试"}
                        </Button>
                      }
                    />
                  ) : op.status === "cancelled" ? (
                    <p className="document-muted">已放弃或取消</p>
                  ) : (
                    <>
                      <p className="document-ai-message">
                        <span>✦</span>
                        {op.result.message}
                      </p>
                      {op.status === "needs_input" && (
                        <>
                          <ul className="document-questions">
                            {op.result.questions?.map((question) => (
                              <li key={question}>{question}</li>
                            ))}
                          </ul>
                          <Button
                            size="small"
                            onClick={() => {
                              setInstruction(
                                op.result.questions
                                  ?.map((q) => `${q}\n`)
                                  .join("\n") || "",
                              );
                              setMobileTab("助手");
                            }}
                          >
                            补充信息
                          </Button>
                        </>
                      )}
                      {op.status === "proposed" && (
                        <div className="document-proposal">
                          <div className="document-proposal-heading">
                            {op.kind === "generate" ? "完整公文" : "修改建议"} ·
                            v{op.base_version}
                          </div>
                          {op.base_revision !== doc.revision && (
                            <Alert
                              type="warning"
                              message="文档已更新，此建议已过期，请按最新稿重新生成。"
                            />
                          )}
                          {op.result.content ? (
                            <div className="document-full-preview">
                              {op.result.content.doc_type !==
                                content.doc_type && (
                                <p className="document-muted">
                                  按“{op.result.content.doc_type}
                                  ”起草，应用后同步更新文章种类。
                                </p>
                              )}
                              {op.result.content.blocks.map((b) => (
                                <p key={b.id}>{contentText(b)}</p>
                              ))}
                            </div>
                          ) : (
                            <PatchPreview
                              patches={op.result.patches || []}
                              content={content}
                            />
                          )}
                          <div className="document-proposal-actions">
                            <Button
                              type="primary"
                              disabled={
                                busy ||
                                !canEdit ||
                                doc.status === "finalized" ||
                                op.base_revision !== doc.revision
                              }
                              loading={busy}
                              onClick={() => accept(op)}
                            >
                              {op.kind === "generate"
                                ? "应用整篇公文"
                                : "接受修改"}
                            </Button>
                            <Button
                              type="text"
                              onClick={() =>
                                run(async () => {
                                  await documentApi.cancel(id, op.id);
                                  await operations.refetch();
                                })
                              }
                            >
                              放弃
                            </Button>
                          </div>
                          <p className="document-muted">
                            接受后将保存为新版本。
                          </p>
                        </div>
                      )}
                      {op.kind === "review" && (
                        <div className="document-review-results">
                          {op.base_revision !== doc.revision && (
                            <Tag>基于旧版本 v{op.base_version}</Tag>
                          )}
                          {op.result.issues?.length === 0 && (
                            <p>本次检查未发现问题。</p>
                          )}
                          {op.result.issues?.map((issue) => (
                            <div
                              className="document-review-issue"
                              key={issue.id}
                            >
                              <Tag
                                color={
                                  issue.severity === "error" ? "red" : "gold"
                                }
                              >
                                {(
                                  {
                                    structure: "结构",
                                    facts: "事实",
                                    language: "表达",
                                  } as Record<string, string>
                                )[issue.category] || issue.category}
                              </Tag>
                              <strong>{issue.message}</strong>
                              {issue.suggestion && <p>{issue.suggestion}</p>}
                              {!!issue.patches?.length && (
                                <PatchPreview
                                  patches={issue.patches}
                                  content={content}
                                />
                              )}
                              <Space>
                                {issue.block_id && (
                                  <Button
                                    size="small"
                                    onClick={() => {
                                      setFocusBlock(issue.block_id!);
                                      setMobileTab("文档");
                                    }}
                                  >
                                    定位段落
                                  </Button>
                                )}
                                {!!issue.patches?.length && (
                                  <Button
                                    size="small"
                                    disabled={
                                      busy ||
                                      !canEdit ||
                                      doc.status === "finalized" ||
                                      op.base_revision !== doc.revision
                                    }
                                    onClick={() => accept(op, issue.id)}
                                  >
                                    接受建议
                                  </Button>
                                )}
                              </Space>
                            </div>
                          ))}
                        </div>
                      )}
                      {op.kind === "export" && op.status === "completed" && (
                        <Button
                          icon={<DownloadOutlined />}
                          onClick={() =>
                            run(() =>
                              documentApi.download(
                                id,
                                op.id,
                                `${doc.title}-v${op.base_version}.docx`,
                              ),
                            )
                          }
                        >
                          下载 Word · v{op.base_version}
                        </Button>
                      )}
                    </>
                  )}
                </article>
              ))}
            </div>
            <div className="document-composer">
              <div className="document-assistant-shortcuts">
                <Button
                  size="small"
                  disabled={busy || !selection.length || working || !canEdit}
                  onClick={() =>
                    send("rewrite", "请精简选定段落，保留原有事实和要求。")
                  }
                >
                  精简段落
                </Button>
                <Button
                  size="small"
                  disabled={busy || working || !canEdit}
                  onClick={() =>
                    send("review", "请检查全文的结构、事实完整性与语言规范。")
                  }
                >
                  检查全文
                </Button>
                {doc.status === "finalized" ? (
                  <Button
                    size="small"
                    disabled={!canEdit}
                    onClick={() =>
                      run(async () =>
                        replace(
                          await documentApi.mutate(
                            id,
                            "reopen",
                            (await flush()).revision,
                          ),
                        ),
                      )
                    }
                  >
                    重新编辑
                  </Button>
                ) : (
                  <Button size="small" disabled={!canEdit} onClick={finalize}>
                    标记定稿
                  </Button>
                )}
              </div>
              <Input.TextArea
                aria-label="公文助手输入"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder={
                  pendingQuestion
                    ? "补充上方问题所需的信息…"
                    : "描述需求，生成或更新整篇公文…"
                }
                rows={3}
                maxLength={20000}
              />
              <div className="document-composer-options">
                <span>
                  {selection.length
                    ? `全文写作 · 已选择 ${selection.length} 段供局部操作`
                    : "全文写作"}
                </span>
                <Dropdown
                  menu={{
                    items: [
                      {
                        key: "rewrite",
                        label: "仅修改选中文字所在段落",
                        disabled: !selection.length || !!pendingQuestion,
                        onClick: () => send("rewrite"),
                      },
                      {
                        key: "answer",
                        label: "仅回答问题",
                        onClick: () => send("answer"),
                      },
                    ],
                  }}
                >
                  <Button size="small" disabled={busy || working || !canEdit}>
                    更多
                  </Button>
                </Dropdown>
                <Button
                  type="primary"
                  aria-label="生成整篇公文"
                  icon={<ArrowUpOutlined />}
                  loading={busy}
                  disabled={working || !canEdit || !instruction.trim()}
                  onClick={() => send("generate", instruction, pendingQuestion)}
                >
                  生成全文
                </Button>
              </div>
              <Checkbox
                checked={allowPlaceholders}
                onChange={(e) => setAllowPlaceholders(e.target.checked)}
              >
                允许先起草，保留待确认信息
              </Checkbox>
            </div>
          </aside>
        )}
      </div>
      {!assistantVisible && (
        <Button
          className="document-open-assistant"
          onClick={() => {
            setAssistantVisible(true);
            setMobileTab("助手");
          }}
        >
          打开公文助手
        </Button>
      )}
      <Drawer
        title="历史版本"
        open={historyOpen}
        onClose={() => {
          setHistoryOpen(false);
          setPreview(null);
        }}
        width={Math.min(680, window.innerWidth)}
      >
        {versions.isLoading && <Spin />}
        {versions.error && <Alert type="error" message="历史版本加载失败" />}
        {preview ? (
          <>
            <Button onClick={() => setPreview(null)}>返回版本列表</Button>
            <h3>版本 v{preview.version}</h3>
            <div className="document-history-preview">
              {preview.content.blocks.map((b) => (
                <p data-role={b.role} key={b.id}>
                  {contentText(b)}
                </p>
              ))}
            </div>
            <Button
              type="primary"
              disabled={!canEdit || doc.status === "finalized"}
              onClick={() =>
                Modal.confirm({
                  title: `恢复版本 v${preview.version}？`,
                  content: "当前稿会先保存，恢复后创建一个新版本。",
                  okText: "恢复",
                  onOk: () =>
                    run(async () => {
                      const saved = await flush();
                      replace(
                        await documentApi.restore(
                          id,
                          preview.version,
                          saved.revision,
                        ),
                      );
                      setPreview(null);
                      await versions.refetch();
                    }),
                })
              }
            >
              恢复此版本
            </Button>
          </>
        ) : (
          versions.data?.map((v) => (
            <div className="document-history-row" key={v.version}>
              <div>
                <strong>
                  v{v.version} · {v.change_summary}
                </strong>
                <p>{new Date(v.created_at).toLocaleString("zh-CN")}</p>
              </div>
              <Button
                onClick={() =>
                  run(async () =>
                    setPreview({
                      ...(await documentApi.version(id, v.version)),
                      version: v.version,
                    }),
                  )
                }
              >
                查看
              </Button>
            </div>
          ))
        )}
      </Drawer>
    </div>
  );
}

export default function OfficialDocumentEditorPage() {
  const { documentId = "" } = useParams(),
    { user } = useAuth();
  return (
    <EditorSession
      key={`${user?.id}:${documentId}`}
      id={documentId}
      userId={user?.id}
    />
  );
}
