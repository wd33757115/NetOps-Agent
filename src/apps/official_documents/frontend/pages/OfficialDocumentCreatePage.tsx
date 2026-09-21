import React, { useState } from "react";
import { Form, Input, Select, Button, Alert, Space } from "antd";
import { ArrowLeftOutlined, FileTextOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { documentApi, requestId } from "../services/officialDocumentApi";
import { useDocumentTemplates } from "../hooks/useDocumentTemplates";
import { appRoutes } from "@netops/apps/appRoutes";
import "../components/documents/documents.css";

export default function OfficialDocumentCreatePage() {
  const templates = useDocumentTemplates();
  const navigate = useNavigate(),
    [form] = Form.useForm();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [key] = useState(requestId);
  const create = async (ai: boolean) => {
    setBusy(true);
    setError("");
    try {
      const values = await form.validateFields();
      const doc = await documentApi.create(
        values.title?.trim() || "未命名公文",
        values.doc_type,
        key,
      );
      if (ai && values.instruction?.trim()) {
        try {
          await documentApi.assistant(doc.id, {
            base_revision: doc.revision,
            request_id: requestId(),
            action: "generate",
            instruction: values.instruction,
            allow_placeholders: true,
            block_ids: [],
          });
        } catch {
          navigate(`${appRoutes.documents}/${doc.id}`, {
            state: { instruction: values.instruction, submissionFailed: true },
          });
          return;
        }
      }
      navigate(`${appRoutes.documents}/${doc.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "请检查填写内容");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="document-app">
      <header className="document-app-header">
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate(appRoutes.documents)}
        >
          公文列表
        </Button>
        <h1>新建公文</h1>
      </header>
      <main className="document-create">
        <FileTextOutlined className="document-create-icon" />
        <h2>开始一篇公文</h2>
        <p>描述写作需求，AI 会先向你确认缺少的关键信息。</p>
        {error && <Alert type="error" message={error} />}
        <Form
          form={form}
          layout="vertical"
          initialValues={{ doc_type: "通知" }}
        >
          <Form.Item name="doc_type" label="文种" rules={[{ required: true }]}>
            <Select
              loading={templates.isLoading}
              options={
                templates.options.length
                  ? templates.options
                  : [{ value: "通知", label: "通知" }]
              }
            />
          </Form.Item>
          <Form.Item name="title" label="标题（可由 AI 生成）">
            <Input maxLength={500} placeholder="关于……的通知" />
          </Form.Item>
          <Form.Item name="instruction" label="写作需求">
            <Input.TextArea
              rows={7}
              maxLength={20000}
              placeholder="请说明发文单位、主送对象、背景和需要表达的事项，以及已确定的时间、地点等信息。"
            />
          </Form.Item>
          <Space>
            <Button type="primary" loading={busy} onClick={() => create(true)}>
              开始写作
            </Button>
            <Button disabled={busy} onClick={() => create(false)}>
              空白起草
            </Button>
          </Space>
        </Form>
      </main>
    </div>
  );
}
