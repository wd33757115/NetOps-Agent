import { test, expect, type Page } from "@playwright/test";

const block = (id: string, role: string, text: string) => ({
  id,
  role,
  level: 1,
  runs: [{ text, marks: [] }],
});
const initial = () => ({
  id: "doc-1",
  title: "关于组织专题培训的通知",
  document_type: "通知",
  status: "draft",
  revision: 0,
  current_version: 1,
  updated_at: "2026-09-05T02:00:00Z",
  content: {
    schema_version: 1,
    doc_type: "通知",
    unresolved_facts: [] as string[],
    blocks: [
      block("title", "title", "集团综合办公室\n关于组织专题培训的通知"),
      block("recipient", "main_recipient", "集团各部门、各单位："),
      block(
        "opening",
        "opening",
        "为进一步提升员工专业能力，现就组织开展专题培训有关事项通知如下：",
      ),
      block("heading-1", "section_heading", "一、培训安排"),
      block("time", "body", "培训时间：2026年9月6日 15:00—17:00。"),
      block("location", "body", "培训地点：集团总部三楼会议室。"),
      block("people", "body", "参加人员：各部门、各单位相关工作人员。"),
      block("heading-2", "section_heading", "二、有关要求"),
      block(
        "requirements",
        "body",
        "请各单位统筹安排参训人员，提前十分钟入场，遵守培训纪律，做好学习记录。",
      ),
      block("closing", "closing", "请于9月5日前将参训名单报送综合办公室。"),
      block("org", "signature_org", "集团综合办公室"),
      block("date", "signature_date", "2026年9月5日"),
    ],
  },
});

async function setup(
  page: Page,
  withProposal = false,
  exportScenario: "ready" | "facts" | "history" | "failure" = "ready",
) {
  let document = initial();
  if (exportScenario === "facts") {
    document.content.unresolved_facts = ["会议地点"];
    document.content.blocks.find((b) => b.id === "location")!.runs[0].text =
      "【待确认：会议室】";
  }
  const described = () => ({
    ...document,
    validation_issues: document.content.unresolved_facts.length
      ? [
          {
            id: "facts",
            category: "facts",
            severity: "error",
            message: "仍有待确认事实，请补齐后定稿",
          },
        ]
      : [],
  });
  const versions = [structuredClone(document.content)];
  let operations: Record<string, any>[] = withProposal
    ? [
        {
          id: "op-1",
          kind: "rewrite",
          status: "proposed",
          base_revision: 0,
          base_version: 1,
          instruction: "请把“有关要求”这一段写得更简洁。",
          result: {
            message: "我已精简这一段，保留参训和纪律要求。",
            patches: [
              {
                op: "replace_block",
                block_id: "requirements",
                value: block(
                  "requirements",
                  "body",
                  "请各单位安排人员准时参训，遵守纪律并做好记录。",
                ),
              },
            ],
          },
          created_at: "2026-09-05T02:01:00Z",
        },
      ]
    : [];
  const errors: string[] = [];
  const submissions: Record<string, any>[] = [];
  const exports: Record<string, any>[] = [];
  let downloadRequests = 0;
  let exportPolls = 0;
  if (exportScenario === "history")
    operations = [
      {
        id: "historical-export",
        kind: "export",
        status: "completed",
        base_version: 1,
        draft: true,
        result: {},
      },
    ];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem("access_token", "document-e2e");
    localStorage.setItem(
      "auth_user",
      JSON.stringify({ id: 71, username: "测试用户", role: "admin" }),
    );
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname,
      method = route.request().method();
    if (path.endsWith("/auth/me/"))
      return route.fulfill({
        json: { id: 71, username: "测试用户", role: "admin" },
      });
    const ok = (data: unknown) =>
      route.fulfill({ json: { success: true, data, error: null } });
    if (!path.includes("/official-documents"))
      return ok({ items: [], total: 0 });
    const body = method === "GET" ? {} : route.request().postDataJSON() || {};
    if (path.endsWith("/templates/"))
      return ok({
        version: 1,
        templates: ["通知", "函", "会议邀请"].map((doc_type) => ({
          id: doc_type,
          doc_type,
          name: doc_type === "函" ? "函件" : doc_type,
          sections: ["事项安排"],
          format: { body_size_pt: 16, line_height_pt: 28, title_size_pt: 22 },
        })),
      });
    if (path.endsWith("/official-documents/"))
      return ok({ items: [document], total: 1 });
    if (path.endsWith("/doc-1/")) return ok(described());
    if (path.endsWith("/content/")) {
      if (body.base_revision !== document.revision)
        return route.fulfill({ status: 409, json: { error: "文档已更新" } });
      document = {
        ...document,
        content: body.content,
        revision: document.revision + 1,
        current_version: document.current_version + 1,
      };
      versions.push(structuredClone(body.content));
      return ok(described());
    }
    if (path.endsWith("/operations/")) {
      if (++exportPolls >= 2)
        operations
          .filter((op) => op.kind === "export" && op.status === "queued")
          .forEach((op) => {
            op.status =
              exportScenario === "failure" && exports.length === 1
                ? "failed"
                : "completed";
            if (op.status === "failed") op.error = "存储暂不可用";
          });
      return ok(operations);
    }
    if (path.endsWith("/exports/")) {
      exports.push(body);
      exportPolls = 0;
      const op = {
        id: `export-${exports.length}`,
        kind: "export",
        status: "queued",
        base_version: body.version,
        base_revision: body.base_revision,
        draft: body.draft,
        result: {},
      };
      operations = [op, ...operations];
      return ok(op);
    }
    if (path.endsWith("/download/")) {
      downloadRequests++;
      return route.fulfill({
        contentType:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        body: "PK-test-download",
      });
    }
    if (path.endsWith("/apply/")) {
      const op = operations.find((o) => path.includes(o.id))!;
      if (op.base_revision !== document.revision)
        return route.fulfill({ status: 409, json: { error: "候选过期" } });
      if (op.result.content)
        document.content = structuredClone(op.result.content);
      else
        document.content.blocks = document.content.blocks.map((b) =>
          b.id === "requirements" ? op.result.patches[0].value : b,
        );
      document.revision++;
      document.current_version++;
      versions.push(structuredClone(document.content));
      op.status = "completed";
      return ok(document);
    }
    if (path.endsWith("/assistant/")) {
      submissions.push(body);
      if (body.action === "generate") {
        const content = initial().content;
        content.doc_type = document.content.doc_type;
        content.blocks[0].runs[0].text = "关于参加集团年会的通知";
        const op = {
          id: "whole-draft",
          kind: "generate",
          status: "proposed",
          base_revision: document.revision,
          base_version: document.current_version,
          instruction: body.instruction,
          result: { message: "完整公文已生成", content },
        };
        operations = [op, ...operations];
        return ok(op);
      }
      operations = [
        {
          id: "review-1",
          kind: body.action,
          status: "completed",
          base_revision: document.revision,
          base_version: document.current_version,
          instruction: body.instruction,
          result: {
            message: "审查完成",
            issues: [
              {
                id: "i-1",
                block_id: "requirements",
                severity: "warning",
                category: "language",
                message: "建议精简表述",
              },
            ],
          },
        },
        ...operations,
      ];
      return ok(operations[0]);
    }
    if (path.endsWith("/versions/"))
      return ok(
        versions
          .map((_, i) => ({
            version: i + 1,
            change_summary: i ? "编辑公文" : "创建公文",
            change_source: "user",
            created_at: document.updated_at,
          }))
          .reverse(),
      );
    const v = path.match(/\/versions\/(\d+)\//);
    if (v) return ok({ content: versions[Number(v[1]) - 1] });
    if (path.endsWith("/restore/")) {
      document = {
        ...document,
        content: structuredClone(versions[body.version - 1]),
        revision: document.revision + 1,
        current_version: document.current_version + 1,
      };
      versions.push(document.content);
      return ok(document);
    }
    return ok({});
  });
  await page.goto("/apps/official-documents/doc-1");
  await expect(page.getByRole("textbox", { name: "公文正文" })).toBeVisible();
  return {
    errors,
    submissions,
    exports,
    downloadRequests: () => downloadRequests,
    saved: () => document,
    conflict: () => {
      document.revision++;
    },
  };
}

test("export downloads incomplete documents without a draft choice or banner", async ({
  page,
  isMobile,
}, testInfo) => {
  const state = await setup(page, false, "facts");
  if (isMobile) await page.getByText("助手", { exact: true }).click();
  await page.getByRole("button", { name: "标记定稿", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "定稿前还需补齐以下内容",
  );
  await page.getByRole("button", { name: "返回编辑", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 Word", exact: true }).click();
  expect((await downloaded).suggestedFilename()).toContain("-v1.docx");
  expect(state.exports[0]).not.toHaveProperty("draft");
  expect(state.saved().content.unresolved_facts).toEqual(["会议地点"]);
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(page.getByText(/Word 已生成/)).toHaveCount(0);
  await expect(page.getByText(/导出草稿/)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "导出 Word", exact: true }),
  ).toBeEnabled();
  expect(state.downloadRequests()).toBe(1);
  expect(state.errors).toEqual([]);
  await page.screenshot({
    path: `logs/document-export-simple-${testInfo.project.name}.png`,
    animations: "disabled",
  });
});

test("export flushes edits and automatically downloads the requested version", async ({
  page,
}) => {
  const state = await setup(page);
  await page
    .locator('[data-block-id="time"]')
    .fill("培训时间：2026年9月6日 16:00。");
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 Word", exact: true }).click();
  expect((await downloaded).suggestedFilename()).toContain("-v2.docx");
  expect(state.exports[0]).toMatchObject({
    version: 2,
    base_revision: 1,
  });
  await page.reload();
  await expect(
    page.getByRole("button", { name: "导出 Word", exact: true }),
  ).toBeVisible();
  expect(state.downloadRequests()).toBe(1);
  expect(state.errors).toEqual([]);
});

test("previous exports remain accessible without automatic downloads on load", async ({
  page,
  isMobile,
}) => {
  const state = await setup(page, false, "history");
  await expect(page.getByText(/Word 已生成/)).toHaveCount(0);
  if (isMobile) await page.getByText("助手", { exact: true }).click();
  expect(state.downloadRequests()).toBe(0);
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: /下载 Word · v1/ }).click();
  expect((await downloaded).suggestedFilename()).toContain("-v1.docx");
  expect(state.exports).toHaveLength(0);
  expect(state.downloadRequests()).toBe(1);
});

test("failed exports can retry without introducing a draft mode", async ({
  page,
}) => {
  const state = await setup(page, false, "failure");
  await page.getByRole("button", { name: "导出 Word", exact: true }).click();
  await expect(page.getByText("存储暂不可用").first()).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page
    .getByRole("button", { name: /^重\s*试$/ })
    .first()
    .click();
  expect((await downloaded).suggestedFilename()).toContain("-v1.docx");
  expect(state.exports).toHaveLength(2);
  expect(state.exports.every((request) => !("draft" in request))).toBe(true);
  expect(state.errors).toEqual([]);
});

test("document manual edit persists after refresh and restores a new version", async ({
  page,
}) => {
  const state = await setup(page);
  await page
    .locator('[data-block-id="time"]')
    .fill("培训时间：2026年9月6日 16:00。");
  await expect(page.getByRole("status")).toContainText("已保存");
  await expect
    .poll(
      () =>
        state.saved().content.blocks.find((b) => b.id === "time")!.runs[0].text,
    )
    .toContain("16:00");
  await page.reload();
  await expect(page.locator('[data-block-id="time"]')).toContainText("16:00");
  await page.getByRole("button", { name: "历史版本", exact: true }).click();
  await page
    .locator(".document-history-row")
    .filter({ hasText: "v1" })
    .getByRole("button", { name: /查\s*看/ })
    .click();
  await page.getByRole("button", { name: "恢复此版本" }).click();
  await page.getByRole("button", { name: /^恢\s*复$/ }).click();
  await expect.poll(() => state.saved().current_version).toBe(3);
  expect(state.errors).toEqual([]);
});

test("AI proposal previews then replaces only the intended paragraph", async ({
  page,
  isMobile,
}) => {
  const state = await setup(page, true);
  if (isMobile) await page.getByText("助手", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "接受修改", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "接受修改", exact: true }).click();
  await expect.poll(() => state.saved().current_version).toBe(2);
  expect(
    state.saved().content.blocks.find((b) => b.id === "requirements")!.runs[0]
      .text,
  ).toContain("准时参训");
  expect(
    state.saved().content.blocks.find((b) => b.id === "time")!.runs[0].text,
  ).toContain("15:00");
  expect(state.errors).toEqual([]);
});

test("conflicting save keeps local draft visible", async ({ page }) => {
  const state = await setup(page);
  state.conflict();
  await page.locator('[data-block-id="time"]').fill("我的本地时间修改");
  await expect(page.getByRole("button", { name: "另存本地稿" })).toBeVisible();
  await expect(page.locator('[data-block-id="time"]')).toContainText(
    "我的本地时间修改",
  );
});

test("document UI preview and review actions", async ({ page, isMobile }) => {
  const state = await setup(page, true);
  await page.emulateMedia({ reducedMotion: "reduce" });
  if (!isMobile) {
    for (const width of [1440, 1280, 768]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(
        page.getByRole("textbox", { name: "公文正文" }),
      ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: `test-results/official-document-editor-${width}.png`,
        animations: "disabled",
      });
    }
  } else {
    await page.setViewportSize({ width: 390, height: 844 });
  }
  if (!isMobile) await page.setViewportSize({ width: 1536, height: 1024 });
  await expect(page.getByRole("status")).toContainText("已保存");
  await page.screenshot({
    path: `test-results/official-document-editor-${isMobile ? "mobile" : "desktop"}.png`,
    animations: "disabled",
  });
  if (isMobile) await page.getByText("助手", { exact: true }).click();
  await page.getByRole("button", { name: "检查全文", exact: true }).click();
  await expect(page.getByText("建议精简表述", { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test("splitting and undoing Chinese paragraphs preserves unique block identities", async ({
  page,
}) => {
  const state = await setup(page);
  await page.locator('[data-block-id="time"]').click();
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("补充中文说明");
  await expect.poll(() => state.saved().content.blocks.length).toBe(13);
  const ids = state.saved().content.blocks.map((b) => b.id);
  expect(new Set(ids).size).toBe(ids.length);
  await page.keyboard.press("Control+z");
  await page.keyboard.press("Control+z");
  await expect(page.locator('[data-block-id="time"]')).toContainText("15:00");
  expect(state.errors).toEqual([]);
});

test("ordinary send generates the whole document despite editor focus or selection", async ({
  page,
  isMobile,
}) => {
  const state = await setup(page);
  await page.locator('[data-block-id="time"]').click();
  if (isMobile) await page.getByText("助手", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "精简段落", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("textbox", { name: "公文助手输入" })
    .fill("帮我写一份会议邀请，2026年12月5日邀请集团下属各部门参加集团年会");
  await page.getByRole("button", { name: "生成整篇公文" }).click();
  await expect(
    page.getByRole("button", { name: "应用整篇公文" }),
  ).toBeVisible();
  expect(state.submissions[0].action).toBe("generate");
  expect(state.submissions[0].allow_placeholders).toBe(true);
  expect(state.submissions[0].block_ids).toEqual([]);
  await page.getByRole("button", { name: "应用整篇公文" }).click();
  await expect
    .poll(() => state.saved().content.blocks[0].runs[0].text)
    .toBe("关于参加集团年会的通知");
  expect(
    state.saved().content.blocks.some((b) => b.role === "signature_date"),
  ).toBe(true);
});

test("document type choices come from the registry and persist without replacing paragraphs", async ({
  page,
}) => {
  const state = await setup(page);
  const before = structuredClone(state.saved().content.blocks);
  await expect(page.getByRole("combobox", { name: "段落样式" })).toHaveCount(0);
  await page.getByRole("combobox", { name: "文章种类" }).focus();
  await page.getByRole("combobox", { name: "文章种类" }).press("ArrowDown");
  await page.getByText("会议邀请", { exact: true }).click();
  await expect.poll(() => state.saved().content.doc_type).toBe("会议邀请");
  expect(state.saved().content.blocks).toEqual(before);
  await page.reload();
  await expect(page.locator(".document-formatbar")).toContainText("会议邀请");
  const styles = await page
    .locator('[data-block-id="time"]')
    .evaluate((el) => ({
      font: parseFloat(getComputedStyle(el).fontSize),
      line: parseFloat(getComputedStyle(el).lineHeight),
    }));
  expect(styles.line).toBeCloseTo(33.6, 1);
  expect(styles.font).toBeCloseTo(19.2, 1);
});
