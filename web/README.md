<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Web 应用与 BFF

React 负责工作台、巡检、规则、公文和通用管理页面；Django BFF 负责认证、用户设置和业务 API 代理。业务处理位于 FastAPI。

## 开发入口

- 前端：`react_frontend/src/App.tsx` 注册路由，`src/apps/generated.ts` 由业务包清单生成，`appRegistry.ts` 适配桌面入口。
- 页面布局：`GrokShellLayout` 与当前聊天侧栏；旧 Ant Design 双侧栏已移除。
- API 客户端：`react_frontend/src/services/`。请求走同源 `/api`，由 Vite 或 Nginx 转发至 BFF。
- BFF：`django_backend/bff/`；Django 默认连接 PostgreSQL 的 `django` schema，SQLite 仅为显式兼容配置。
- 权限：页面守卫和应用图标过滤改善体验，BFF/FastAPI 负责实际授权。业务包按 App 使用权授权；个人桌面布局与壁纸按账号保存。

完整部署见仓库根目录的 README 和 `docs/` 下的部署文档。只启动前端可在仓库根目录执行：

```powershell
npm --prefix web/react_frontend ci
npm --prefix web/react_frontend run dev
```

前端通常运行在 `3000`，BFF 为 `8001`；业务调用仍需后端及相应 Worker。不要让浏览器绕过 BFF 直接调用 FastAPI。

## 检查

```powershell
npm --prefix web/react_frontend run check
npm --prefix web/react_frontend run test:e2e
npm --prefix web/react_frontend run test:visual
```

`check` 包含 UTF-8、ESLint、单元测试与生产构建。浏览器用例覆盖桌面和手机；视觉基线与 CI 使用 Windows Chrome。结果不能替代真实设备与模型服务的验收。

公文模板从服务端配置读取，桌面应用权限来自注册表；
