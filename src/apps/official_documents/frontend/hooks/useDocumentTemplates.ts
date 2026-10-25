import { useQuery } from "react-query";
import {
  documentApi,
  type DocumentTemplate,
} from "../services/officialDocumentApi";

export function useDocumentTemplates() {
  const query = useQuery(["document-templates"], documentApi.templates, {
    staleTime: 60000,
  });
  const templates = query.data?.templates || [];
  return {
    ...query,
    templates,
    options: templates.map((t) => ({ value: t.doc_type, label: t.name })),
  };
}

export function documentFormatStyle(template?: DocumentTemplate) {
  return Object.fromEntries(
    Object.entries(template?.format || {}).map(([key, value]) => [
      `--doc-${key.replaceAll("_", "-")}`,
      value,
    ]),
  );
}
