// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useRef, useState } from "react";
import { Button, Drawer } from "antd";
import { MenuOutlined } from "@ant-design/icons";
import ChatShellSidebar from "../chat/ChatShellSidebar";
import NotificationBell from "../NotificationBell";
import { useIsMobile } from "../../hooks/useBreakpoint";

interface GrokShellLayoutProps {
  children: React.ReactNode;
  title?: string;
  subtitle?: string;
  /** 页面顶部工具栏（搜索、操作按钮） */
  toolbar?: React.ReactNode;
  /** @deprecated 使用 toolbar */
  actions?: React.ReactNode;
  mode?: "page" | "chat" | "workbench";
  footer?: React.ReactNode;
  scrollRef?: React.RefObject<HTMLDivElement | null>;
}

const GrokShellLayout: React.FC<GrokShellLayoutProps> = ({
  children,
  title,
  subtitle,
  toolbar,
  actions,
  mode = "page",
  footer,
  scrollRef,
}) => {
  const mainRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const root = mainRef.current;
    if (!root) return;
    const makeTablesFocusable = () =>
      root
        .querySelectorAll<HTMLElement>(".ant-table-content, .ant-table-body")
        .forEach((element) => {
          element.tabIndex = 0;
          if (!element.hasAttribute("aria-label"))
            element.setAttribute("aria-label", "表格滚动区域");
        });
    makeTablesFocusable();
    const observer = new MutationObserver(makeTablesFocusable);
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);
  const isMobile = useIsMobile();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const drawerWidth = Math.min(
    300,
    typeof window !== "undefined" ? window.innerWidth * 0.88 : 300,
  );

  const showPageHeader = mode === "page" && (title || subtitle) && !isMobile;
  const pageToolbar = toolbar ?? actions;
  const mobileTitle = mode === "chat" ? "NetOps Agent" : title;

  return (
    <div className="grok-shell">
      {!isMobile && mode !== "workbench" && <ChatShellSidebar />}

      <Drawer
        title={null}
        placement="left"
        open={(isMobile || mode === "workbench") && sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        width={drawerWidth}
        styles={{ body: { padding: 0 } }}
        className="grok-sidebar-drawer"
      >
        <ChatShellSidebar
          embedded
          workbench={mode === "workbench"}
          onNavigate={() => setSidebarOpen(false)}
        />
      </Drawer>

      <main
        ref={mainRef}
        className={`grok-main${mode === "chat" ? " grok-main-chat" : mode === "workbench" ? " grok-main-workbench" : ""}`}
      >
        {mode === "workbench" && (
          <button
            className="desktop-navigation-toggle"
            type="button"
            aria-label="打开菜单"
            aria-expanded={sidebarOpen}
            onClick={() => setSidebarOpen(true)}
          >
            <span className="desktop-navigation-mark" aria-hidden="true">
              N
            </span>
            <span>NetOps</span>
          </button>
        )}
        {!isMobile && mode === "chat" && (
          <div className="grok-chat-topbar">
            <NotificationBell />
          </div>
        )}

        {isMobile && mode !== "workbench" && (
          <header className="grok-mobile-bar">
            <Button
              type="text"
              icon={<MenuOutlined />}
              aria-label="打开菜单"
              className="grok-mobile-bar-menu"
              onClick={() => setSidebarOpen(true)}
            />
            {mobileTitle && (
              <span className="grok-mobile-bar-title">{mobileTitle}</span>
            )}
            <div className="grok-mobile-bar-actions">
              <NotificationBell />
            </div>
          </header>
        )}

        {mode === "workbench" ? (
          children
        ) : mode === "chat" ? (
          <>
            <div ref={scrollRef} className="grok-messages-scroll">
              <div className="grok-messages-inner">{children}</div>
            </div>
            {footer}
          </>
        ) : (
          <div className="grok-content-scroll">
            <div className="grok-content-inner">
              {showPageHeader && (
                <header className="grok-page-header">
                  <div className="grok-page-heading">
                    {title && <h1 className="grok-page-title">{title}</h1>}
                    {subtitle && (
                      <p className="grok-page-subtitle">{subtitle}</p>
                    )}
                  </div>
                </header>
              )}
              {isMobile && subtitle && (
                <p className="grok-page-subtitle grok-page-subtitle--mobile">
                  {subtitle}
                </p>
              )}
              {pageToolbar && (
                <div className="grok-page-toolbar">{pageToolbar}</div>
              )}
              <div className="grok-page-body">{children}</div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
};

export default GrokShellLayout;
