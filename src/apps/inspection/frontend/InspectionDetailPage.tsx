import React, { createContext, useCallback, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button, Typography } from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'

interface PageContext {
  host: HTMLDivElement | null
  active?: string
  view?: string
  register: (id: string) => () => void
}
const Pages = createContext<PageContext | null>(null)

/** Keep the underlying list mounted so filters, selection and cached evidence survive Back. */
export function InspectionPageHost({ children, view }: { children: React.ReactNode; view?: string }) {
  const [host, setHost] = useState<HTMLDivElement | null>(null)
  const [pages, setPages] = useState<string[]>([])
  const register = useCallback((id: string) => {
    setPages(current => [...current.filter(value => value !== id), id])
    return () => setPages(current => current.filter(value => value !== id))
  }, [])
  const value = useMemo(() => ({ host, active: pages.at(-1), register, view }), [host, pages, register, view])
  return <Pages.Provider value={value}>
    <div hidden={pages.length > 0}>{children}</div>
    <div className="inspection-page-host" ref={setHost} />
  </Pages.Provider>
}

export interface InspectionDetailPageProps {
  open?: boolean
  title?: React.ReactNode
  extra?: React.ReactNode
  subtitle?: React.ReactNode
  eyebrow?: string
  backLabel?: string
  onClose?: () => void
  children?: React.ReactNode
}

export default function InspectionDetailPage({ open, title, extra, subtitle, eyebrow = '网络巡检 / 任务与证据', backLabel = '返回上一页', onClose, children }: InspectionDetailPageProps) {
  const context = useContext(Pages)
  const view = context?.view
  const previousView = useRef(view)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    if (previousView.current !== view && open) close.current?.()
    previousView.current = view
  }, [view, open])
  const id = useId()
  const heading = useRef<HTMLHeadingElement>(null)
  const host = context?.host
  const register = context?.register
  useLayoutEffect(() => {
    if (!open || !host || !register) return
    const previousFocus = document.activeElement as HTMLElement | null
    const scroller = host.closest('.inspection-app')
    const scrollTop = scroller?.scrollTop || 0
    const unregister = register(id)
    if (scroller) scroller.scrollTop = 0
    return () => {
      unregister()
      // Restore after the parent surface has become visible again.
      requestAnimationFrame(() => {
        if (scroller) scroller.scrollTop = scrollTop
        if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true })
      })
    }
  }, [open, host, id, register])
  const visible = !context || context.active === id
  useLayoutEffect(() => {
    if (open && visible) heading.current?.focus({ preventScroll: true })
  }, [open, visible])
  if (!open) return null
  const page = <section className="inspection-detail-page" hidden={!visible} aria-labelledby={id}>
    <header className="inspection-detail-heading">
      <Button className="inspection-detail-back" type="text" icon={<ArrowLeftOutlined aria-hidden="true" />} onClick={onClose}>{backLabel}</Button>
      <div className="inspection-detail-title-row">
        <div><span className="inspection-eyebrow">{eyebrow}</span><h1 id={id} ref={heading} tabIndex={-1}>{title}</h1>
          {subtitle && <Typography.Text className="inspection-detail-subtitle" type="secondary">{subtitle}</Typography.Text>}
        </div>
        {extra && <div className="inspection-detail-actions">{extra}</div>}
      </div>
    </header>
    <div className="inspection-detail-content">{children}</div>
  </section>
  return host ? createPortal(page, host) : context ? null : page
}
