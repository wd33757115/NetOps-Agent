import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useDocumentSession } from "./useDocumentSession";
import {
  documentApi,
  type OfficialDocument,
} from "../services/officialDocumentApi";

vi.mock("../services/officialDocumentApi", () => ({
  documentApi: { get: vi.fn(), save: vi.fn() },
  requestId: () => crypto.randomUUID(),
}));
const initial: OfficialDocument = {
  id: "doc",
  title: "通知",
  document_type: "通知",
  status: "draft",
  revision: 0,
  current_version: 1,
  updated_at: "",
  content: {
    schema_version: 1,
    doc_type: "通知",
    blocks: [
      {
        id: "title",
        role: "title",
        level: 1,
        runs: [{ text: "通知", marks: [] }],
      },
    ],
    unresolved_facts: [],
  },
};
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(documentApi.get).mockResolvedValue(structuredClone(initial));
});

it("drains edits made during an in-flight save before a dependent action can continue", async () => {
  let release!: (doc: OfficialDocument) => void;
  vi.mocked(documentApi.save)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    )
    .mockImplementationOnce(async (_, content, revision) => ({
      ...initial,
      content,
      revision: revision + 1,
      current_version: 3,
    }));
  const { result } = renderHook(() => useDocumentSession("doc", 1));
  await waitFor(() => expect(result.current.status).toBe("saved"));
  const first = { ...initial.content, unresolved_facts: ["地点"] };
  const second = { ...initial.content, unresolved_facts: ["地点", "日期"] };
  let pending!: Promise<OfficialDocument>;
  act(() => {
    result.current.change(first);
  });
  act(() => {
    pending = result.current.flush();
  });
  act(() => {
    result.current.change(second);
  });
  await act(async () => {
    release({ ...initial, content: first, revision: 1, current_version: 2 });
    await pending;
  });
  expect(documentApi.save).toHaveBeenCalledTimes(2);
  expect(vi.mocked(documentApi.save).mock.calls[1][2]).toBe(1);
  expect(result.current.doc?.content).toEqual(second);
  expect(result.current.dirty.current).toBe(false);
});

it("keeps an incompatible cached draft across reloads until the user resolves it", async () => {
  const content = { ...initial.content, unresolved_facts: ["本地内容"] };
  localStorage.setItem(
    "official-document:1:doc",
    JSON.stringify({ content, revision: 4 }),
  );
  const { result, unmount } = renderHook(() => useDocumentSession("doc", 1));
  await waitFor(() => expect(result.current.status).toBe("conflict"));
  expect(result.current.content).toEqual(content);
  await expect(result.current.flush()).rejects.toThrow("版本冲突");
  unmount();
  expect(
    JSON.parse(localStorage.getItem("official-document:1:doc")!).content,
  ).toEqual(content);
  expect(documentApi.save).not.toHaveBeenCalled();
});
