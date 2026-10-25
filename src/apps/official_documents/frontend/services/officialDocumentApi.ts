import { api } from "@netops/config/api";

export type Role =
  | "issuer"
  | "title"
  | "main_recipient"
  | "opening"
  | "section_heading"
  | "body"
  | "closing"
  | "signature_org"
  | "signature_date";
export interface DocumentBlock {
  id: string;
  role: Role;
  level: number;
  runs: { text: string; marks: ("bold" | "underline")[] }[];
}
export interface DocumentContent {
  schema_version: 1;
  doc_type: string;
  blocks: DocumentBlock[];
  unresolved_facts: string[];
}
export interface OfficialDocument {
  id: string;
  title: string;
  document_type: string;
  status: "draft" | "finalized";
  revision: number;
  current_version: number;
  content: DocumentContent;
  validation_issues?: DocumentIssue[];
  updated_at: string;
  deleted_at?: string;
}
export interface DocumentPatch {
  op: string;
  block_id: string;
  value?: DocumentBlock;
}
export interface DocumentIssue {
  id: string;
  block_id?: string;
  severity: string;
  category: string;
  message: string;
  suggestion?: string;
  patches?: DocumentPatch[];
}
export interface DocumentOperation {
  id: string;
  kind: string;
  status: string;
  base_revision: number;
  base_version: number;
  instruction: string;
  draft?: boolean;
  error?: string;
  result: {
    kind?: string;
    message?: string;
    questions?: string[];
    content?: DocumentContent;
    patches?: DocumentPatch[];
    issues?: DocumentIssue[];
  };
  created_at: string;
}
export interface DocumentVersion {
  version: number;
  change_summary: string;
  change_source: string;
  created_at: string;
}
export interface DocumentTemplate {
  id: string;
  doc_type: string;
  name: string;
  sections: string[];
  format: Record<string, string | number>;
}
const root = "/apps/official-documents/";
const path = (id: string, suffix = "") => `${root}${id}/${suffix}`;
export const requestId = () => crypto.randomUUID();
export const contentText = (b: DocumentBlock) =>
  b.runs.map((r) => r.text).join("");
export const documentApi = {
  templates: async () =>
    (
      await api.get<{ version: number; templates: DocumentTemplate[] }>(
        `${root}templates/`,
      )
    ).data,
  list: async (params: Record<string, unknown>) =>
    (
      await api.get<{ items: OfficialDocument[]; total: number }>(root, {
        params,
      })
    ).data,
  create: async (title: string, doc_type: string, request_id: string) =>
    (await api.post<OfficialDocument>(root, { title, doc_type, request_id }))
      .data,
  get: async (id: string, signal?: AbortSignal) =>
    (await api.get<OfficialDocument>(path(id), { signal })).data,
  save: async (
    id: string,
    content: DocumentContent,
    base_revision: number,
    request_id: string,
  ) =>
    (
      await api.put<OfficialDocument>(path(id, "content/"), {
        content,
        base_revision,
        request_id,
      })
    ).data,
  mutate: async (id: string, action: string, base_revision: number) =>
    (
      await api.patch<OfficialDocument>(path(id), {
        action,
        base_revision,
        request_id: requestId(),
      })
    ).data,
  remove: async (doc: OfficialDocument) =>
    api.delete(path(doc.id), {
      data: { base_revision: doc.revision, request_id: requestId() },
    }),
  undelete: async (doc: OfficialDocument) =>
    api.post(path(doc.id, "undelete/"), {
      base_revision: doc.revision,
      request_id: requestId(),
    }),
  versions: async (id: string) =>
    (await api.get<DocumentVersion[]>(path(id, "versions/"))).data,
  version: async (id: string, version: number) =>
    (
      await api.get<{ content: DocumentContent }>(
        path(id, `versions/${version}/`),
      )
    ).data,
  restore: async (id: string, version: number, base_revision: number) =>
    (
      await api.post<OfficialDocument>(path(id, "restore/"), {
        version,
        base_revision,
        request_id: requestId(),
      })
    ).data,
  operations: async (id: string) =>
    (await api.get<DocumentOperation[]>(path(id, "operations/"))).data,
  assistant: async (id: string, body: Record<string, unknown>) =>
    (await api.post<DocumentOperation>(path(id, "assistant/"), body)).data,
  apply: async (
    id: string,
    op: string,
    base_revision: number,
    issue_id?: string,
  ) =>
    (
      await api.post<OfficialDocument>(path(id, `operations/${op}/apply/`), {
        base_revision,
        issue_id,
        request_id: requestId(),
      })
    ).data,
  cancel: async (id: string, op: string) =>
    api.post(path(id, `operations/${op}/cancel/`)),
  export: async (doc: OfficialDocument) =>
    (
      await api.post<DocumentOperation>(path(doc.id, "exports/"), {
        version: doc.current_version,
        base_revision: doc.revision,
        request_id: requestId(),
      })
    ).data,
  download: async (id: string, op: string, filename: string) => {
    const { data } = await api.get(path(id, `exports/${op}/download/`), {
      responseType: "blob",
    });
    const url = URL.createObjectURL(data);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
