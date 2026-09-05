import { describe, it, expect } from "vitest";
import { toEditorDocument, fromEditorDocument } from "./editor";
import type { DocumentContent } from "../../services/officialDocumentApi";

describe("document editor semantic contract", () => {
  it("preserves block identity, headings, marks and unresolved facts through the editor", () => {
    const content: DocumentContent = {
      schema_version: 1,
      doc_type: "通知",
      unresolved_facts: ["地点"],
      blocks: [
        {
          id: "title",
          role: "title",
          level: 1,
          runs: [{ text: "测试通知", marks: [] }],
        },
        {
          id: "heading",
          role: "section_heading",
          level: 2,
          runs: [{ text: "（一）时间", marks: ["bold"] }],
        },
        {
          id: "body",
          role: "body",
          level: 1,
          runs: [
            { text: "15:00", marks: ["underline"] },
            { text: "\n准时参加", marks: [] },
          ],
        },
      ],
    };
    expect(fromEditorDocument(toEditorDocument(content), content)).toEqual(
      content,
    );
  });
  it("never retains unsupported executable nodes or marks in the semantic payload", () => {
    const previous: DocumentContent = {
      schema_version: 1,
      doc_type: "通知",
      unresolved_facts: [],
      blocks: [],
    };
    const result = fromEditorDocument(
      {
        type: "doc",
        content: [
          {
            type: "paragraph",
            attrs: { role: "title", blockId: "title" },
            content: [
              {
                type: "text",
                text: "正文",
                marks: [
                  { type: "link", attrs: { href: "javascript:alert(1)" } },
                ],
              },
            ],
          },
        ],
      },
      previous,
    );
    expect(result.blocks[0].runs).toEqual([{ text: "正文", marks: [] }]);
  });
});
