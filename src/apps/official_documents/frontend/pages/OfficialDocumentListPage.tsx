import React, { useState } from "react";
import {
  Button,
  Input,
  Select,
  Table,
  Space,
  Alert,
  Popconfirm,
  Tag,
} from "antd";
import {
  PlusOutlined,
  ArrowLeftOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import { useQuery } from "react-query";
import { useNavigate } from "react-router-dom";
import {
  documentApi,
  type OfficialDocument,
} from "../services/officialDocumentApi";
import { appRoutes, workbenchEnabled } from "@netops/apps/appRoutes";
import { useDocumentTemplates } from "../hooks/useDocumentTemplates";
import { useAuth } from "@netops/context/AuthContext";
import "../components/documents/documents.css";

export default function OfficialDocumentListPage() {
  const navigate = useNavigate();
  const templates = useDocumentTemplates();
  const { user } = useAuth();
  const [q, setQ] = useState(""),
    [docType, setDocType] = useState(""),
    [status, setStatus] = useState("");
  const [page, setPage] = useState(1),
    [deleted, setDeleted] = useState(false),
    [error, setError] = useState("");
  const query = useQuery(
    ["official-documents", user?.id, q, docType, status, page, deleted],
    () =>
      documentApi.list({
        q,
        doc_type: docType,
        status,
        offset: (page - 1) * 20,
        limit: 20,
        deleted,
      }),
  );
  const act = async (doc: OfficialDocument) => {
    try {
      if (deleted) await documentApi.undelete(doc);
      else await documentApi.remove(doc);
      await query.refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    }
  };
  return (
    <div className="document-app">
      <header className="document-app-header">
        <button
          className="document-brand"
          onClick={() =>
            navigate(workbenchEnabled ? appRoutes.workbench : "/chat")
          }
        >
          <b>N</b> NetOps
        </button>
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() =>
            navigate(workbenchEnabled ? appRoutes.workbench : "/chat")
          }
        >
          工作台
        </Button>
        <span className="document-breadcrumb">/</span>
        <h1>公文写作</h1>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => navigate(`${appRoutes.documents}/new`)}
        >
          新建公文
        </Button>
      </header>
      <main className="document-library">
        <div className="document-library-heading">
          <div>
            <FileTextOutlined />
            <h2>{deleted ? "回收站" : "我的公文"}</h2>
            <p>起草、编辑与审查，在一处完成。</p>
          </div>
          <Button
            type="text"
            onClick={() => {
              setDeleted(!deleted);
              setPage(1);
            }}
          >
            {deleted ? "返回文档" : "回收站"}
          </Button>
        </div>
        <div className="document-library-filters">
          <Input.Search
            placeholder="搜索公文标题"
            aria-label="搜索公文标题"
            allowClear
            onSearch={(value) => {
              setQ(value);
              setPage(1);
            }}
          />
          <Select
            aria-label="筛选文种"
            value={docType}
            onChange={(v) => {
              setDocType(v);
              setPage(1);
            }}
            options={[{ value: "", label: "全部文种" }, ...templates.options]}
          />
          <Select
            aria-label="筛选状态"
            value={status}
            onChange={(v) => {
              setStatus(v);
              setPage(1);
            }}
            options={[
              { value: "", label: "全部状态" },
              { value: "draft", label: "草稿" },
              { value: "finalized", label: "已定稿" },
            ]}
          />
        </div>
        {(error || query.error) && (
          <Alert
            type="error"
            message={error || String(query.error)}
            action={<Button onClick={() => query.refetch()}>重试</Button>}
          />
        )}
        <Table<OfficialDocument>
          rowKey="id"
          loading={query.isLoading}
          dataSource={query.data?.items || []}
          pagination={{
            current: page,
            pageSize: 20,
            total: query.data?.total || 0,
            onChange: setPage,
            showSizeChanger: false,
          }}
          locale={{
            emptyText: q
              ? "没有匹配的公文"
              : deleted
                ? "回收站为空"
                : "还没有公文，点击“新建公文”开始写作",
          }}
          columns={[
            {
              title: "标题",
              dataIndex: "title",
              render: (title, doc) =>
                deleted ? (
                  title
                ) : (
                  <button
                    className="document-title-link"
                    onClick={() => navigate(`${appRoutes.documents}/${doc.id}`)}
                  >
                    <FileTextOutlined />
                    {title}
                  </button>
                ),
            },
            {
              title: "文种",
              dataIndex: "document_type",
              width: 90,
              responsive: ["sm"],
            },
            {
              title: "状态",
              dataIndex: "status",
              width: 100,
              render: (value) => (
                <Tag color={value === "finalized" ? "blue" : "default"}>
                  {value === "finalized" ? "已定稿" : "草稿"}
                </Tag>
              ),
            },
            {
              title: "修改时间",
              dataIndex: "updated_at",
              responsive: ["md"],
              render: (value) => new Date(value).toLocaleString("zh-CN"),
            },
            {
              title: "操作",
              width: 130,
              render: (_, doc) => (
                <Space>
                  {!deleted && (
                    <Button
                      type="link"
                      onClick={() =>
                        navigate(`${appRoutes.documents}/${doc.id}`)
                      }
                    >
                      编辑
                    </Button>
                  )}
                  <Popconfirm
                    title={deleted ? "恢复这篇公文？" : "将公文移入回收站？"}
                    onConfirm={() => act(doc)}
                  >
                    <Button type="text" danger={!deleted}>
                      {deleted ? "恢复" : "删除"}
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </main>
    </div>
  );
}
