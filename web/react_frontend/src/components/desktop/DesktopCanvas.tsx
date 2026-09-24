import WallpaperSettings from './WallpaperSettings';
import { wallpaperBackground } from './wallpaper';
import { useAuth } from '../../context/AuthContext';
import React, { useEffect, useRef, useState } from "react";
import { Button, Dropdown, Input, Modal, Space } from "antd";
import {
  CloseOutlined,
  MoreOutlined,
  PlusOutlined,
  FolderOpenOutlined,
} from "@ant-design/icons";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  pointerWithin,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  horizontalListSortingStrategy,
  useSortable,
  arrayMove,
} from "@dnd-kit/sortable";
import { useNavigate } from "react-router-dom";
import { canOpenApp, findApp } from "../../apps/appRegistry";
import { useDesktopStore } from "../../store/useDesktopStore";
import {
  dissolveFolder,
  desktopColumns,
  firstFree,
  moveNode,
  type DesktopNode,
} from "./desktopLayout";
import "./desktop.css";

export function AppGlyph({ id }: { id?: string }) {
  const symbol = id ? findApp(id)?.symbol : undefined;
  return (
    <span
      className={`desktop-glyph ${symbol === "shield" ? "is-firewall" : symbol === "radar" ? "is-patrol" : symbol === "document" ? "is-document" : id ? "is-rules" : "is-folder"}`}
    >
      <svg viewBox="0 0 64 64" fill="none" aria-hidden="true">
        {!id ? (
          <>
            <path
              d="M5 18a5 5 0 0 1 5-5h15l6 6h23a5 5 0 0 1 5 5v25a5 5 0 0 1-5 5H10a5 5 0 0 1-5-5Z"
              fill="#269bd7"
            />
            <path
              d="M5 26a5 5 0 0 1 5-5h44a5 5 0 0 1 5 5v23a5 5 0 0 1-5 5H10a5 5 0 0 1-5-5Z"
              fill="#7cd3f5"
            />
            <path
              d="M11 22h42"
              stroke="#b9eeff"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </>
        ) : symbol === "server" ? (
          <><rect x="12" y="10" width="40" height="19" rx="4" fill="white" /><rect x="12" y="35" width="40" height="19" rx="4" fill="white" /><path d="M20 20h16M20 45h16" stroke="#4076b8" strokeWidth="3" strokeLinecap="round" /><circle cx="44" cy="20" r="2" fill="#4076b8" /><circle cx="44" cy="45" r="2" fill="#4076b8" /></>
        ) : symbol === "shield" ? (
          <><path d="M32 7 51 15v16c0 13-10 21-19 26-9-5-19-13-19-26V15L32 7Z" fill="white" fillOpacity=".94" /><path d="m23 31 6 6 13-14" stroke="#4076b8" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" /></>
        ) : symbol === "document" ? (
          <><rect x="15" y="8" width="34" height="48" rx="4" fill="white" /><path d="M23 21h18M23 29h18M23 37h13M23 45h15" stroke="#4d84cd" strokeWidth="2.5" strokeLinecap="round" /></>
        ) : symbol === "radar" ? (
          <>
            <circle
              cx="32"
              cy="32"
              r="23"
              stroke="currentColor"
              strokeOpacity=".4"
              strokeWidth="1.5"
            />
            <circle
              cx="32"
              cy="32"
              r="15"
              stroke="currentColor"
              strokeOpacity=".5"
              strokeWidth="1.5"
            />
            <path
              d="M32 9v46M9 32h46"
              stroke="currentColor"
              strokeOpacity=".25"
            />
            <path
              d="M32 32V9a23 23 0 0 1 20 11Z"
              fill="currentColor"
              fillOpacity=".25"
            />
            <path
              d="m32 32 17-17"
              stroke="white"
              strokeWidth="2.5"
              strokeLinecap="round"
            />
            <circle cx="32" cy="32" r="3" fill="white" />
            <circle cx="20" cy="40" r="3" fill="#b1ffdd" />
            <circle cx="42" cy="39" r="2" fill="#b1ffdd" />
          </>
        ) : (
          <>
            <rect
              x="15"
              y="10"
              width="34"
              height="44"
              rx="5"
              fill="white"
              fillOpacity=".96"
            />
            <path
              d="m22 24 3 3 5-6m-8 17 3 3 5-6"
              stroke="#4e78d3"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d="M35 25h7M35 39h7"
              stroke="#829dd6"
              strokeWidth="2.5"
              strokeLinecap="round"
            />
          </>
        )}
      </svg>
    </span>
  );
}
function DropArea({
  id,
  children,
  className = "",
  style,
}: {
  id: string;
  children?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return (
    <div
      ref={setNodeRef}
      className={`${className}${isOver ? " is-drop-target" : ""}`}
      style={style}
    >
      {children}
    </div>
  );
}
function DesktopTile({
  node,
  onOpen,
  onMenu,
  selected,
  onSelect,
  disabled,
}: {
  node: DesktopNode;
  onOpen: () => void;
  onMenu: () => void;
  selected: boolean;
  onSelect: () => void;
  disabled: boolean;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `node:${node.id}`,
    disabled,
  });
  const name = node.kind === "folder" ? node.name : findApp(node.appId!)?.name;
  return (
    <div
      className={`desktop-tile${selected ? " is-selected" : ""}${isDragging ? " is-dragging" : ""}`}
    >
      <button
        ref={setNodeRef}
        {...attributes}
        {...listeners}
        className="desktop-launch"
        type="button"
        aria-label={name}
        onClick={() => {
          onSelect();
          if (
            node.kind === "folder" ||
            window.matchMedia("(pointer: coarse)").matches
          )
            onOpen();
        }}
        onDoubleClick={onOpen}
        onKeyDown={(event) => {
          if (event.key === "Enter") onOpen();
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onMenu();
        }}
      >
        <AppGlyph id={node.appId} />
        <span className="desktop-label">{name}</span>
      </button>
      <button
        className="desktop-item-menu"
        type="button"
        aria-label={`${name}操作`}
        onClick={onMenu}
      >
        <MoreOutlined />
      </button>
    </div>
  );
}
function DockItem({
  appId,
  open,
  unpin,
}: {
  appId: string;
  open: () => void;
  unpin: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: `dock:${appId}` });
  const app = findApp(appId);
  return (
    <Dropdown
      trigger={["contextMenu"]}
      menu={{ items: [{ key: "unpin", label: "取消固定", onClick: unpin }] }}
    >
      <button
        ref={setNodeRef}
        {...attributes}
        {...listeners}
        type="button"
        className="desktop-dock-app"
        aria-label={`打开${app?.name}`}
        title={app?.name}
        style={{
          transform: transform
            ? `translate3d(${transform.x}px, ${transform.y}px, 0)`
            : undefined,
          transition,
          opacity: isDragging ? 0.4 : 1,
        }}
        onClick={open}
      >
        <AppGlyph id={appId} />
        <span className="desktop-dock-tooltip" aria-hidden="true">
          {app?.name}
        </span>
      </button>
    </Dropdown>
  );
}

function DesktopClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <time className="desktop-clock" dateTime={now.toISOString()}>
      <span className="desktop-clock-date">
        {now.toLocaleDateString("zh-CN", {
          month: "long",
          day: "numeric",
          weekday: "short",
        })}
      </span>
      <span>
        {now.toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        })}
      </span>
    </time>
  );
}

export default function DesktopCanvas() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { layout, edit, status } = useDesktopStore();
  const [selected, setSelected] = useState<string | null>(null);
  const [folderId, setFolderId] = useState<string | null>(null);
  const [menuNodeId, setMenuNodeId] = useState<string | null>(null);
  const [nameDialog, setNameDialog] = useState<{
    id?: string;
    name: string;
  } | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [columns, setColumns] = useState(6);
  const [canvasHeight, setCanvasHeight] = useState(464);
  const [wallpaperOpen, setWallpaperOpen] = useState(false);
  const canvas = useRef<HTMLDivElement>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
  );
  useEffect(() => {
    if (!canvas.current) return;
    const observer = new ResizeObserver(([entry]) => {
      setColumns(desktopColumns(entry.contentRect.width));
      setCanvasHeight(entry.contentRect.height);
    });
    observer.observe(canvas.current);
    return () => observer.disconnect();
  }, []);
  const disabled = status === "conflict";
  const visibleNodes = layout.nodes.filter(
    (n) => n.kind === "folder" || (findApp(n.appId!)?.showOnDesktop && canOpenApp(n.appId!, user)),
  );
  const nodes = visibleNodes.filter((n) => n.containerId === "desktop:0");
  // Responsive projection is render-only: resize never saves or overwrites positions.
  const slots = new Map<number, DesktopNode>();
  nodes
    .slice()
    .sort((a, b) => a.row! - b.row! || a.col! - b.col!)
    .forEach((node) => {
      let slot = node.row! * columns + Math.min(node.col!, columns - 1);
      while (slots.has(slot)) slot++;
      slots.set(slot, node);
    });
  const count = Math.max(
    columns * Math.max(4, Math.floor(canvasHeight / 116)),
    Math.max(-1, ...slots.keys()) + columns + 1,
  );
  const folder = visibleNodes.find(
    (n) => n.id === folderId && n.kind === "folder",
  );
  const children = visibleNodes
    .filter((n) => n.containerId === `folder:${folderId}`)
    .sort((a, b) => (a.order || 0) - (b.order || 0));
  const menuNode = visibleNodes.find((n) => n.id === menuNodeId);
  const dragNode = visibleNodes.find((n) => `node:${n.id}` === dragging);
  const open = (node: DesktopNode) =>
    node.kind === "folder"
      ? setFolderId(node.id)
      : navigate(findApp(node.appId!)!.route);
  const dockIds = layout.dockAppIds.filter((id) => findApp(id)?.showOnDesktop && canOpenApp(id, user));
  const pin = (appId: string) =>
    edit((value) => ({
      ...value,
      dockAppIds: value.dockAppIds.includes(appId)
        ? value.dockAppIds.filter((id) => id !== appId)
        : [...value.dockAppIds, appId],
    }));
  const move = (node: DesktopNode, containerId: string) => {
    edit((value) => moveNode(value, node.id, containerId));
    setMenuNodeId(null);
  };
  function onDragEnd(event: DragEndEvent) {
    setDragging(null);
    if (disabled || !event.over) return;
    const source = String(event.active.id),
      target = String(event.over.id);
    if (source.startsWith("dock:")) {
      const appId = source.slice(5);
      if (target.startsWith("dock:"))
        edit((value) => ({
          ...value,
          dockAppIds: arrayMove(
            value.dockAppIds,
            value.dockAppIds.indexOf(appId),
            value.dockAppIds.indexOf(target.slice(5)),
          ),
        }));
      else if (target.startsWith("cell:"))
        edit((value) => ({
          ...value,
          dockAppIds: value.dockAppIds.filter((id) => id !== appId),
        }));
      return;
    }
    const node = layout.nodes.find((n) => `node:${n.id}` === source);
    if (!node) return;
    if (target === "dock-zone" || target.startsWith("dock:")) {
      if (node.appId && !layout.dockAppIds.includes(node.appId))
        pin(node.appId);
    } else if (
      target.startsWith("folder:") ||
      target.startsWith("folder-icon:")
    ) {
      edit((value) =>
        moveNode(value, node.id, target.replace("folder-icon:", "folder:")),
      );
    } else if (target.startsWith("inside:")) {
      edit((value) =>
        moveNode(
          value,
          node.id,
          `folder:${folderId}`,
          undefined,
          target.slice(7),
        ),
      );
    } else if (target.startsWith("cell:")) {
      const index = Number(target.slice(5)),
        occupied = slots.get(index);
      edit((value) =>
        moveNode(
          value,
          node.id,
          "desktop:0",
          occupied
            ? { col: occupied.col!, row: occupied.row! }
            : { col: index % columns, row: Math.floor(index / columns) },
        ),
      );
      setFolderId(null);
    }
  }
  function saveName() {
    const name = nameDialog?.name.trim();
    if (!name || !nameDialog) return;
    edit((value) =>
      nameDialog.id
        ? {
            ...value,
            nodes: value.nodes.map((n) =>
              n.id === nameDialog.id ? { ...n, name } : n,
            ),
          }
        : {
            ...value,
            nodes: [
              ...value.nodes,
              {
                id: crypto.randomUUID(),
                kind: "folder",
                name,
                containerId: "desktop:0",
                ...firstFree(value.nodes, columns),
              },
            ],
          },
    );
    setNameDialog(null);
  }
  const tile = (node: DesktopNode) => (
    <DesktopTile
      node={node}
      selected={selected === node.id}
      onSelect={() => setSelected(node.id)}
      onOpen={() => open(node)}
      onMenu={() => setMenuNodeId(node.id)}
      disabled={disabled}
    />
  );
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={(args) => {
        const hits = pointerWithin(args);
        // Small explicit targets take precedence over their parent canvas/overlay.
        return hits.sort((a, b) => {
          const priority = (id: string) =>
            id.startsWith("inside:") || id.startsWith("dock:")
              ? 0
              : id.startsWith("folder:") || id.startsWith("folder-icon:")
                ? 1
                : id === "dock-zone"
                  ? 2
                  : 3;
          return priority(String(a.id)) - priority(String(b.id));
        });
      }}
      onDragStart={(event) => setDragging(String(event.active.id))}
      onDragCancel={() => setDragging(null)}
      onDragEnd={onDragEnd}
    >
      <section
        className="desktop-surface"
        aria-label="工作台桌面"
        style={{ background: wallpaperBackground(layout.wallpaper) }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setFolderId(null);
            setSelected(null);
          }
        }}
      >
        <header className="desktop-topbar">
          <div className="desktop-menu-items">
            <h1>工作台</h1>
            <Button
              type="text"
              aria-label="新建文件夹"
              icon={<PlusOutlined />}
              disabled={disabled}
              onClick={() => setNameDialog({ name: "新建文件夹" })}
            >
              新建文件夹
            </Button>
          </div>
          {(user?.role === 'admin' || user?.permissions?.includes('apps.manage')) && <Button type="text" onClick={() => navigate(user?.role === 'admin' ? '/system/users' : '/system/apps')} style={{ marginLeft: 'auto', color: 'inherit' }}>系统管理</Button>}
          <DesktopClock />
        </header>
        <Dropdown
          trigger={["contextMenu"]}
          menu={{
            items: [
              {
                key: "wallpaper",
                label: "更换壁纸",
                disabled,
                onClick: () => setWallpaperOpen(true),
              },
              {
                key: "new",
                label: "新建文件夹",
                disabled,
                onClick: () => setNameDialog({ name: "新建文件夹" }),
              },
            ],
          }}
        >
          <div
            ref={canvas}
            className="desktop-canvas"
            onClick={(event) => {
              if (event.target === event.currentTarget) setSelected(null);
            }}
          >
            <div
              className="desktop-grid"
              style={{ gridTemplateColumns: `repeat(${columns}, 112px)` }}
            >
              {Array.from({ length: count }, (_, index) => {
                const node = slots.get(index);
                return (
                  <DropArea
                    key={index}
                    id={`cell:${index}`}
                    className="desktop-cell"
                  >
                    {node?.kind === "folder" ? (
                      <DropArea
                        id={`folder-icon:${node.id}`}
                        className="desktop-folder-target"
                      >
                        {tile(node)}
                      </DropArea>
                    ) : node ? (
                      tile(node)
                    ) : null}
                  </DropArea>
                );
              })}
            </div>
          </div>
        </Dropdown>
        {folder && (
          <div
            className={`desktop-folder-layer${dragging ? " is-dragging" : ""}`}
          >
            <button
              className="desktop-folder-backdrop"
              type="button"
              aria-label="关闭文件夹"
              onClick={() => setFolderId(null)}
            />
            <DropArea
              id={`folder:${folder.id}`}
              className="desktop-folder-window"
            >
              <header>
                <button
                  className="desktop-window-close"
                  type="button"
                  aria-label="关闭"
                  onClick={() => setFolderId(null)}
                >
                  <CloseOutlined />
                </button>
                <h2>
                  <FolderOpenOutlined /> {folder.name}
                </h2>
                <span className="desktop-folder-count">
                  {children.length} 个应用
                </span>
              </header>
              <div className="desktop-folder-grid">
                {children.map((node) => (
                  <DropArea id={`inside:${node.id}`} key={node.id}>
                    {tile(node)}
                  </DropArea>
                ))}
              </div>
              {!children.length && (
                <p className="desktop-empty-folder">
                  将应用拖到这里，或在应用操作中选择移入文件夹。
                </p>
              )}
            </DropArea>
          </div>
        )}
        <footer className="desktop-footer">
          <DropArea id="dock-zone" className="desktop-dock">
            <SortableContext
              items={dockIds.map((id) => `dock:${id}`)}
              strategy={horizontalListSortingStrategy}
            >
              {dockIds.map((id) => (
                <DockItem
                  key={id}
                  appId={id}
                  open={() => navigate(findApp(id)!.route)}
                  unpin={() => pin(id)}
                />
              ))}
            </SortableContext>
            {!dockIds.length && <span>拖入常用应用</span>}
          </DropArea>
          <span className="desktop-hint">
            {window.matchMedia("(pointer: coarse)").matches
              ? "轻点应用打开"
              : "双击应用打开 · 拖动整理桌面"}
          </span>
        </footer>
      </section>
      {wallpaperOpen && <WallpaperSettings value={layout.wallpaper} disabled={disabled} onClose={() => setWallpaperOpen(false)} onSave={wallpaper => { edit(value => ({ ...value, wallpaper })); setWallpaperOpen(false); }} />}
      <DragOverlay>
        {dragNode ? (
          <div className="desktop-drag-preview">
            <AppGlyph id={dragNode.appId} />
            <span>{dragNode.name || findApp(dragNode.appId!)?.name}</span>
          </div>
        ) : null}
      </DragOverlay>
      <Modal
        open={Boolean(nameDialog)}
        title={nameDialog?.id ? "重命名文件夹" : "新建文件夹"}
        onCancel={() => setNameDialog(null)}
        onOk={saveName}
        okText="保存"
        cancelText="取消"
        okButtonProps={{
          "aria-label": "保存",
          disabled: !nameDialog?.name.trim() || disabled,
        }}
        destroyOnClose
      >
        <Input
          autoFocus
          aria-label="文件夹名称"
          maxLength={40}
          value={nameDialog?.name || ""}
          onChange={(event) =>
            setNameDialog((value) =>
              value ? { ...value, name: event.target.value } : value,
            )
          }
          onPressEnter={saveName}
        />
      </Modal>
      <Modal
        open={Boolean(menuNode)}
        title={menuNode?.name || findApp(menuNode?.appId || "")?.name}
        onCancel={() => setMenuNodeId(null)}
        footer={null}
        destroyOnClose
      >
        {menuNode && (
          <Space direction="vertical" className="desktop-action-list">
            <Button
              block
              onClick={() => {
                open(menuNode);
                setMenuNodeId(null);
              }}
            >
              打开
            </Button>
            {menuNode.kind === "app" ? (
              <>
                <Button
                  block
                  disabled={disabled}
                  onClick={() => pin(menuNode.appId!)}
                >
                  {layout.dockAppIds.includes(menuNode.appId!)
                    ? "取消固定到 Dock"
                    : "固定到 Dock"}
                </Button>
                {menuNode.containerId !== "desktop:0" && (
                  <Button
                    block
                    disabled={disabled}
                    onClick={() => move(menuNode, "desktop:0")}
                  >
                    移回桌面
                  </Button>
                )}
                {menuNode.containerId !== "desktop:0" && (
                  <Space>
                    {[-1, 1].map((direction) => (
                      <Button
                        key={direction}
                        disabled={disabled}
                        onClick={() =>
                          edit((value) => {
                            const siblings = value.nodes
                              .filter(
                                (n) => n.containerId === menuNode.containerId,
                              )
                              .sort((a, b) => (a.order || 0) - (b.order || 0));
                            const index = siblings.findIndex(
                              (n) => n.id === menuNode.id,
                            );
                            const next = index + direction;
                            if (next < 0 || next >= siblings.length)
                              return value;
                            const reordered = arrayMove(siblings, index, next);
                            return moveNode(
                              value,
                              menuNode.id,
                              menuNode.containerId,
                              undefined,
                              reordered[next + 1]?.id,
                            );
                          })
                        }
                      >
                        {direction < 0 ? "在文件夹中前移" : "在文件夹中后移"}
                      </Button>
                    ))}
                  </Space>
                )}
                {layout.dockAppIds.includes(menuNode.appId!) && (
                  <Space>
                    {[-1, 1].map((direction) => (
                      <Button
                        key={direction}
                        disabled={disabled}
                        onClick={() =>
                          edit((value) => {
                            const index = value.dockAppIds.indexOf(
                              menuNode.appId!,
                            );
                            const next = index + direction;
                            return next < 0 || next >= value.dockAppIds.length
                              ? value
                              : {
                                  ...value,
                                  dockAppIds: arrayMove(
                                    value.dockAppIds,
                                    index,
                                    next,
                                  ),
                                };
                          })
                        }
                      >
                        {direction < 0 ? "在 Dock 中前移" : "在 Dock 中后移"}
                      </Button>
                    ))}
                  </Space>
                )}

                {visibleNodes
                  .filter(
                    (n) =>
                      n.kind === "folder" &&
                      `folder:${n.id}` !== menuNode.containerId,
                  )
                  .map((n) => (
                    <Button
                      key={n.id}
                      block
                      disabled={disabled}
                      onClick={() => move(menuNode, `folder:${n.id}`)}
                    >
                      移入「{n.name}」
                    </Button>
                  ))}
              </>
            ) : (
              <>
                <Button
                  block
                  disabled={disabled}
                  onClick={() => {
                    setNameDialog({ id: menuNode.id, name: menuNode.name! });
                    setMenuNodeId(null);
                  }}
                >
                  重命名
                </Button>
                <Button
                  block
                  disabled={disabled}
                  onClick={() => {
                    edit((value) => dissolveFolder(value, menuNode.id));
                    setMenuNodeId(null);
                    setFolderId(null);
                  }}
                >
                  解散文件夹，应用移回桌面
                </Button>
              </>
            )}
            {menuNode.containerId === "desktop:0" && (
              <Space wrap>
                {[
                  ["向左", -1, 0],
                  ["向右", 1, 0],
                  ["向上", 0, -1],
                  ["向下", 0, 1],
                ].map(([label, x, y]) => (
                  <Button
                    key={label}
                    disabled={disabled}
                    onClick={() =>
                      edit((value) =>
                        moveNode(value, menuNode.id, "desktop:0", {
                          col: Math.max(
                            0,
                            Math.min(99, menuNode.col! + Number(x)),
                          ),
                          row: Math.max(
                            0,
                            Math.min(199, menuNode.row! + Number(y)),
                          ),
                        }),
                      )
                    }
                  >
                    {label}
                  </Button>
                ))}
              </Space>
            )}
          </Space>
        )}
      </Modal>
    </DndContext>
  );
}
