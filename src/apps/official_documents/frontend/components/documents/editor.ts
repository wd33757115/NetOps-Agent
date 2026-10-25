import { Extension, type JSONContent } from "@tiptap/core";
import { Plugin } from "@tiptap/pm/state";
import type {
  DocumentBlock,
  DocumentContent,
} from "../../services/officialDocumentApi";

export const DocumentIdentity = Extension.create({
  name: "documentIdentity",
  addGlobalAttributes() {
    return [
      {
        types: ["paragraph"],
        attributes: {
          blockId: {
            default: null,
            parseHTML: () => null,
            renderHTML: (attrs) => ({ "data-block-id": attrs.blockId }),
          },
          role: {
            default: "body",
            parseHTML: () => "body",
            renderHTML: (attrs) => ({ "data-role": attrs.role }),
          },
          level: {
            default: 1,
            renderHTML: (attrs) => ({ "data-level": attrs.level }),
          },
        },
      },
    ];
  },
  addProseMirrorPlugins() {
    return [
      new Plugin({
        appendTransaction(transactions, _old, state) {
          if (!transactions.some((t) => t.docChanged)) return null;
          const seen = new Set<string>();
          const singleton = new Set<string>();
          const tr = state.tr;
          state.doc.descendants((node, pos) => {
            if (node.type.name !== "paragraph") return;
            const attrs = { ...node.attrs };
            if (!attrs.blockId || seen.has(attrs.blockId))
              attrs.blockId = crypto.randomUUID();
            seen.add(attrs.blockId);
            if (!["body", "section_heading"].includes(attrs.role)) {
              if (singleton.has(attrs.role)) attrs.role = "body";
              else singleton.add(attrs.role);
            }
            if (
              attrs.blockId !== node.attrs.blockId ||
              attrs.role !== node.attrs.role
            )
              tr.setNodeMarkup(pos, undefined, attrs);
          });
          return tr.docChanged ? tr : null;
        },
      }),
    ];
  },
});

export function toEditorDocument(content: DocumentContent): JSONContent {
  return {
    type: "doc",
    content: content.blocks.map((block) => ({
      type: "paragraph",
      attrs: { blockId: block.id, role: block.role, level: block.level },
      content: block.runs
        .filter((r) => r.text)
        .map((r) => ({
          type: "text",
          text: r.text,
          marks: r.marks.map((type) => ({ type })),
        })),
    })),
  };
}
export function fromEditorDocument(
  json: JSONContent,
  previous: DocumentContent,
): DocumentContent {
  const blocks: DocumentBlock[] = (json.content || []).map((node) => ({
    id: node.attrs?.blockId || crypto.randomUUID(),
    role: node.attrs?.role || "body",
    level: node.attrs?.level || 1,
    runs: (node.content || []).map((n) => ({
      text: n.type === "hardBreak" ? "\n" : n.text || "",
      marks: (n.marks || [])
        .filter((m) => m.type === "bold" || m.type === "underline")
        .map((m) => m.type as "bold" | "underline"),
    })),
  }));
  if (!blocks.some((b) => b.role === "title"))
    blocks.unshift({
      id: crypto.randomUUID(),
      role: "title",
      level: 1,
      runs: [{ text: "未命名公文", marks: [] }],
    });
  return { ...previous, blocks };
}
