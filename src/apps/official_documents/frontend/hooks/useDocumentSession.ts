import { useCallback, useEffect, useRef, useState } from "react";
import {
  documentApi,
  requestId,
  type DocumentContent,
  type OfficialDocument,
} from "../services/officialDocumentApi";

export function useDocumentSession(id: string, userId: number | undefined) {
  const [doc, setDoc] = useState<OfficialDocument | null>(null);
  const [content, setContent] = useState<DocumentContent | null>(null);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const current = useRef<OfficialDocument | null>(null);
  const local = useRef<DocumentContent | null>(null);
  const dirty = useRef(false);
  const conflicted = useRef(false);
  const saving = useRef<Promise<OfficialDocument> | null>(null);
  const alive = useRef(true);
  const cacheKey = `official-document:${userId}:${id}`;
  const replace = useCallback(
    (value: OfficialDocument) => {
      current.current = value;
      local.current = value.content;
      dirty.current = false;
      conflicted.current = false;
      setDoc(value);
      setContent(value.content);
      setStatus("saved");
      setError("");
      try {
        localStorage.removeItem(cacheKey);
      } catch {
        /* cache is optional */
      }
    },
    [cacheKey],
  );
  const reload = useCallback(
    async (discard = false) => {
      const value = await documentApi.get(id);
      if (!alive.current) return;
      replace(value);
      if (!discard) {
        // Read before replacement's cache cleanup in the initial loader below.
        return value;
      }
    },
    [id, replace],
  );
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    let cached: { content: DocumentContent; revision: number } | null = null;
    try {
      cached = JSON.parse(localStorage.getItem(cacheKey) || "null");
    } catch {
      /* optional draft */
    }
    documentApi
      .get(id, controller.signal)
      .then((value) => {
        if (!alive.current || controller.signal.aborted) return;
        replace(value);
        if (
          cached &&
          JSON.stringify(cached.content) !== JSON.stringify(value.content)
        ) {
          local.current = cached.content;
          dirty.current = true;
          setContent(cached.content);
          try {
            localStorage.setItem(cacheKey, JSON.stringify(cached));
          } catch {
            /* optional */
          }
          conflicted.current = cached.revision !== value.revision;
          setStatus(conflicted.current ? "conflict" : "pending");
          setError(
            conflicted.current
              ? "本地草稿与服务器版本不同，已保留草稿。请另存或加载最新版本。"
              : "",
          );
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setError(String(e.message || "加载失败"));
          setStatus("error");
        }
      });
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [id, cacheKey, replace]);
  const change = useCallback(
    (value: DocumentContent) => {
      local.current = value;
      dirty.current = true;
      setContent(value);
      if (!conflicted.current) setStatus("pending");
      try {
        localStorage.setItem(
          cacheKey,
          JSON.stringify({
            content: value,
            revision: current.current?.revision,
          }),
        );
      } catch {
        /* server remains authoritative */
      }
    },
    [cacheKey],
  );
  const flush = useCallback(async (): Promise<OfficialDocument> => {
    while (saving.current) await saving.current;
    if (!current.current || !local.current) throw new Error("文档尚未加载");
    if (conflicted.current) throw new Error("请先处理版本冲突");
    if (!dirty.current) return current.current;
    do {
      const value = local.current!;
      const revision = current.current.revision;
      setStatus("saving");
      const promise = documentApi.save(id, value, revision, requestId());
      saving.current = promise;
      try {
        const saved = await promise;
        if (!alive.current) return saved;
        current.current = saved;
        setDoc(saved);
        if (local.current === value) {
          dirty.current = false;
          setStatus("saved");
          setError("");
          try {
            localStorage.removeItem(cacheKey);
          } catch {
            /* optional */
          }
        } else {
          setStatus("pending");
          try {
            localStorage.setItem(
              cacheKey,
              JSON.stringify({
                content: local.current,
                revision: saved.revision,
              }),
            );
          } catch {
            /* optional */
          }
        }
      } catch (e) {
        const message = e instanceof Error ? e.message : "保存失败";
        if (alive.current) {
          const statusCode =
            (e as { status?: number; response?: { status?: number } }).status ??
            (e as { response?: { status?: number } }).response?.status;
          conflicted.current = statusCode === 409;
          setStatus(conflicted.current ? "conflict" : "error");
          setError(message);
        }
        throw e;
      } finally {
        saving.current = null;
      }
    } while (dirty.current && alive.current);
    return current.current;
  }, [id, cacheKey]);
  useEffect(() => {
    if (status !== "pending") return;
    const timer = window.setTimeout(() => {
      void flush().catch(() => {});
    }, 1000);
    return () => clearTimeout(timer);
  }, [content, status, flush]);
  useEffect(() => {
    const onLeave = (event: BeforeUnloadEvent) => {
      if (dirty.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", onLeave);
    return () => window.removeEventListener("beforeunload", onLeave);
  }, []);
  return { doc, content, status, error, change, flush, replace, reload, dirty };
}
