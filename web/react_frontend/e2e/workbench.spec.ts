import { expect, test, type Page } from "@playwright/test";

const user = { id: 71, username: "工作台测试", email: "", role: "admin" };
const initial = () => ({
  schemaVersion: 1,
  revision: 0,
  nodes: [
    {
      id: "patrol",
      kind: "app",
      appId: "patrol",
      containerId: "desktop:0",
      col: 0,
      row: 0,
    },
    {
      id: "patrol-rules",
      kind: "app",
      appId: "patrol-rules",
      containerId: "desktop:0",
      col: 1,
      row: 0,
    },
  ],
  dockAppIds: ["patrol", "patrol-rules"],
});

async function setup(page: Page) {
  let saved = initial();
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript((account) => {
    localStorage.setItem("access_token", "workbench-test-token");
    localStorage.setItem("auth_user", JSON.stringify(account));
  }, user);
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me/")) return route.fulfill({ json: user });
    if (path.endsWith("/auth/me/desktop-layout/")) {
      if (route.request().method() === "PUT") {
        const incoming = route.request().postDataJSON();
        if (incoming.revision !== saved.revision)
          return route.fulfill({ status: 409, json: { error: "布局冲突" } });
        saved = { ...incoming, revision: saved.revision + 1 };
      }
      return route.fulfill({
        json: { success: true, data: saved, error: null },
      });
    }
    return route.fulfill({
      json: { items: [], total: 0, conversations: [], unread_count: 0 },
    });
  });
  await page.goto("/workbench");
  await expect(
    page.getByRole("button", { name: "网络巡检", exact: true }),
  ).toBeVisible();
  return { saved: () => saved, errors };
}

test("desktop folders, Dock, save and reload", async ({ page }) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "新建文件夹", exact: true }).click();
  await page.getByRole("textbox", { name: "文件夹名称" }).fill("网络运维");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "网络运维", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "网络巡检操作" }).click();
  await page.getByRole("button", { name: "移入「网络运维」" }).click();
  await page.getByRole("button", { name: "网络运维", exact: true }).click();
  await expect(page.locator(".desktop-folder-window")).toBeVisible();
  await page.screenshot({ path: `test-results/workbench-folder-${test.info().project.name}.png`, animations: "disabled" });
  await expect(
    page
      .locator(".desktop-folder-window")
      .getByRole("button", { name: "网络巡检", exact: true }),
  ).toBeVisible();
  await page
    .locator(".desktop-folder-window")
    .getByRole("button", { name: "关闭", exact: true })
    .click();
  await expect(page.locator(".desktop-save-status")).toContainText(
    "布局已保存",
  );
  expect(
    state.saved().nodes.find((n) => n.id === "patrol")!.containerId,
  ).toMatch(/^folder:/);
  await page.reload();
  await page.getByRole("button", { name: "网络运维", exact: true }).click();
  await expect(
    page
      .locator(".desktop-folder-window")
      .getByRole("button", { name: "网络巡检", exact: true }),
  ).toBeVisible();
  await page
    .locator(".desktop-folder-window")
    .getByRole("button", { name: "关闭", exact: true })
    .click();
  await page.getByRole("button", { name: "网络运维操作" }).click();
  await page.getByRole("button", { name: "解散文件夹，应用移回桌面" }).click();
  await expect(
    page.getByRole("button", { name: "网络巡检", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "网络巡检操作" }).click();
  await page.getByRole("button", { name: "取消固定到 Dock" }).click();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "打开网络巡检", exact: true }),
  ).toHaveCount(0);
  expect(state.errors).toEqual([]);
  await expect(page.locator(".ant-modal-wrap:visible")).toHaveCount(0);
  await expect(page.locator(".desktop-save-status")).toHaveText("布局已保存");
  await page.screenshot({
    path: `test-results/workbench-${test.info().project.name}.png`,
    fullPage: true,
  });
});

test("desktop drag snaps and persists without launching an app", async ({
  page,
  isMobile,
}) => {
  test.skip(
    isMobile,
    "Pointer drag is exercised on desktop; mobile uses the same move commands.",
  );
  const state = await setup(page);
  const icon = await page
    .getByRole("button", { name: "网络巡检", exact: true })
    .boundingBox();
  const target = await page.locator(".desktop-cell").nth(3).boundingBox();
  await page.mouse.move(icon!.x + 40, icon!.y + 30);
  await page.mouse.down();
  await page.mouse.move(target!.x + 50, target!.y + 30, { steps: 15 });
  await page.mouse.up();
  await expect
    .poll(() => state.saved().nodes.find((n) => n.id === "patrol")!.col)
    .toBe(3);
  await expect(page).toHaveURL(/\/workbench$/);
  await page.reload();
  await expect(
    page
      .locator(".desktop-cell")
      .nth(3)
      .getByRole("button", { name: "网络巡检", exact: true }),
  ).toBeVisible();
  expect(state.errors).toEqual([]);
});

test("desktop navigation menu does not overwrite the normal sidebar preference", async ({
  page,
}) => {
  await setup(page);
  await page.getByRole("button", { name: "打开菜单" }).click();
  await expect(
    page.locator(".grok-sidebar-drawer .grok-nav-list"),
  ).toBeVisible();
  await expect(page.locator(".grok-history-section")).toHaveCount(0);
  expect(
    await page.evaluate(() => localStorage.getItem("netops-sidebar:71")),
  ).toBeNull();
  await page.reload();
  await expect(page.getByRole("button", { name: "打开菜单" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
});

test("drag into and out of a folder, then pin to Dock", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "The mobile folder commands are covered above.");
  const state = await setup(page);
  await page.getByRole("button", { name: "新建文件夹", exact: true }).click();
  await page.getByRole("textbox", { name: "文件夹名称" }).fill("运维工具");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  async function drag(
    from: ReturnType<Page["locator"]>,
    to: ReturnType<Page["locator"]>,
  ) {
    const a = await from.boundingBox(),
      b = await to.boundingBox();
    await page.mouse.move(a!.x + a!.width / 2, a!.y + 25);
    await page.mouse.down();
    await page.mouse.move(b!.x + b!.width / 2, b!.y + b!.height / 2, {
      steps: 18,
    });
    await page.mouse.up();
  }
  await drag(
    page.getByRole("button", { name: "网络巡检", exact: true }),
    page.getByRole("button", { name: "运维工具", exact: true }),
  );
  await expect
    .poll(() => state.saved().nodes.find((n) => n.id === "patrol")!.containerId)
    .toMatch(/^folder:/);
  await page.getByRole("button", { name: "运维工具", exact: true }).click();
  await drag(
    page
      .locator(".desktop-folder-window")
      .getByRole("button", { name: "网络巡检", exact: true }),
    page.locator(".desktop-cell").first(),
  );
  await expect
    .poll(() => state.saved().nodes.find((n) => n.id === "patrol")!.containerId)
    .toBe("desktop:0");
  await page.getByRole("button", { name: "网络巡检操作" }).click();
  await page.getByRole("button", { name: "取消固定到 Dock" }).click();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await drag(
    page.getByRole("button", { name: "网络巡检", exact: true }),
    page.locator(".desktop-dock"),
  );
  await expect
    .poll(() => state.saved().dockAppIds.includes("patrol"))
    .toBe(true);
  expect(state.errors).toEqual([]);
});
