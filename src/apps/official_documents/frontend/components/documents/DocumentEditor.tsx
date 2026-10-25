import React, { useEffect, useRef, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Button, Select, Tooltip } from "antd";
import {
  BoldOutlined,
  UnderlineOutlined,
  UndoOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import {
  DocumentIdentity,
  toEditorDocument,
  fromEditorDocument,
} from "./editor";
import {
  useDocumentTemplates,
  documentFormatStyle,
} from "../../hooks/useDocumentTemplates";
import type { DocumentContent } from "../../services/officialDocumentApi";

export default function DocumentEditor({
  content,
  onChange,
  onSelection,
  readOnly = false,
  focusBlock,
}: {
  content: DocumentContent;
  onChange: (value: DocumentContent) => void;
  onSelection: (ids: string[]) => void;
  readOnly?: boolean;
  focusBlock?: string;
}) {
  const latest = useRef({ content, onChange, onSelection });
  latest.current = { content, onChange, onSelection };
  const templateQuery = useDocumentTemplates();
  const template = templateQuery.templates.find(
    (t) => t.doc_type === content.doc_type,
  );
  const [zoom, setZoom] = useState(90);
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: false,
        blockquote: false,
        code: false,
        codeBlock: false,
        bulletList: false,
        orderedList: false,
        listItem: false,
        horizontalRule: false,
        strike: false,
        italic: false,
        link: false,
        trailingNode: false,
      }),
      DocumentIdentity,
    ],
    content: toEditorDocument(content),
    editable: !readOnly,
    editorProps: {
      attributes: {
        "aria-label": "公文正文",
        role: "textbox",
        "aria-multiline": "true",
        spellcheck: "false",
      },
    },
    onUpdate: ({ editor }) => {
      if (editor.view.composing) return;
      latest.current.onChange(
        fromEditorDocument(editor.getJSON(), latest.current.content),
      );
    },
    onSelectionUpdate: ({ editor }) => {
      const { from, to } = editor.state.selection;
      const ids: string[] = [];
      if (from !== to)
        editor.state.doc.nodesBetween(from, to, (node) => {
          if (node.attrs.blockId) ids.push(node.attrs.blockId);
        });
      latest.current.onSelection(ids);
    },
  });
  useEffect(() => {
    if (
      editor &&
      JSON.stringify(fromEditorDocument(editor.getJSON(), content)) !==
        JSON.stringify(content)
    )
      editor.commands.setContent(toEditorDocument(content), {
        emitUpdate: false,
      });
  }, [content, editor]);
  useEffect(() => {
    editor?.setEditable(!readOnly, false);
  }, [editor, readOnly]);
  useEffect(() => {
    if (!editor || !focusBlock) return;
    editor.state.doc.descendants((node, pos) => {
      if (node.attrs.blockId === focusBlock)
        editor
          .chain()
          .focus()
          .setTextSelection({ from: pos + 1, to: pos + node.nodeSize - 1 })
          .scrollIntoView()
          .run();
    });
  }, [editor, focusBlock]);
  useEffect(() => {
    if (!editor) return;
    const element = editor.view.dom;
    const ended = () => {
      window.setTimeout(() => {
        if (!editor.isDestroyed)
          latest.current.onChange(
            fromEditorDocument(editor.getJSON(), latest.current.content),
          );
      }, 0);
    };
    element.addEventListener("compositionend", ended);
    return () => element.removeEventListener("compositionend", ended);
  }, [editor]);
  const actions = [
    {
      label: "撤销",
      icon: <UndoOutlined />,
      run: () => editor?.chain().focus().undo().run(),
    },
    {
      label: "重做",
      icon: <RedoOutlined />,
      run: () => editor?.chain().focus().redo().run(),
    },
    {
      label: "加粗",
      icon: <BoldOutlined />,
      run: () => editor?.chain().focus().toggleBold().run(),
    },
    {
      label: "下划线",
      icon: <UnderlineOutlined />,
      run: () => editor?.chain().focus().toggleUnderline().run(),
    },
  ];
  return (
    <section className="document-editor-region">
      <div className="document-formatbar">
        {actions.slice(0, 2).map((a) => (
          <Tooltip title={a.label} key={a.label}>
            <Button
              type="text"
              aria-label={a.label}
              icon={a.icon}
              disabled={readOnly}
              onClick={a.run}
            />
          </Tooltip>
        ))}
        <span className="document-tool-divider" />
        <Select
          aria-label="文章种类"
          value={content.doc_type}
          loading={templateQuery.isLoading}
          disabled={readOnly || !templateQuery.templates.length}
          style={{ width: 140 }}
          onChange={(doc_type) => onChange({ ...content, doc_type })}
          options={
            templateQuery.options.length
              ? templateQuery.options
              : [{ value: content.doc_type, label: content.doc_type }]
          }
        />
        {templateQuery.isError && (
          <Tooltip title="文种模板加载失败">
            <Button size="small" onClick={() => templateQuery.refetch()}>
              重试模板
            </Button>
          </Tooltip>
        )}
        {actions.slice(2).map((a) => (
          <Tooltip title={a.label} key={a.label}>
            <Button
              type="text"
              aria-label={a.label}
              icon={a.icon}
              disabled={readOnly}
              onClick={a.run}
            />
          </Tooltip>
        ))}
        <Select
          aria-label="缩放"
          value={zoom}
          onChange={setZoom}
          options={[75, 90, 100].map((value) => ({
            value,
            label: `${value}%`,
          }))}
        />
      </div>
      <div className="document-paper-scroll">
        <div
          className="document-paper"
          style={
            {
              ...documentFormatStyle(template),
              "--paper-scale": zoom / 100,
            } as React.CSSProperties
          }
        >
          <EditorContent editor={editor} />
        </div>
      </div>
      <footer className="document-editor-footer">
        <span>连续纸张 · 分页以 Word 导出为准</span>
        <span>
          字数：
          {content.blocks.reduce(
            (n, b) => n + b.runs.reduce((v, r) => v + r.text.length, 0),
            0,
          )}
        </span>
      </footer>
    </section>
  );
}
