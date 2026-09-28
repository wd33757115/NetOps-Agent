import { InspectionPageHost } from './InspectionDetailPage'
import React, { useEffect, useRef } from 'react'
import { Button } from 'antd'
import { ArrowLeftOutlined, ArrowRightOutlined, CheckCircleOutlined, CloudServerOutlined, FileTextOutlined, InboxOutlined, MessageOutlined, PlusOutlined, RadarChartOutlined, WarningOutlined } from '@ant-design/icons'
import { Link, useSearchParams } from 'react-router-dom'
import { appRoutes, workbenchEnabled } from '@netops/apps/appRoutes'
import { inspectionView, inspectionViews } from './inspectionNavigation'
import './InspectionWorkspace.css'

const icons = [InboxOutlined, CloudServerOutlined, FileTextOutlined, WarningOutlined, MessageOutlined]

export default function InspectionLayout({ children, onNewPatrol }: {
  children: React.ReactNode; onNewPatrol: () => void
}) {
  const [params, setParams] = useSearchParams()
  const view = inspectionView(params)
  const mainRef = useRef<HTMLElement>(null)
  useEffect(() => {
    const root = mainRef.current
    if (!root) return
    const labelTables = () => root.querySelectorAll<HTMLElement>('.ant-table-content, .ant-table-body').forEach(table => {
      table.tabIndex = 0
      table.setAttribute('aria-label', '巡检表格，可横向滚动查看完整内容')
    })
    labelTables()
    const observer = new MutationObserver(labelTables)
    observer.observe(root, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [])
  return <div className="inspection-app">
    <div className="inspection-toolbar">
      <Link to={workbenchEnabled ? appRoutes.workbench : '/'}><ArrowLeftOutlined aria-hidden="true" /> {workbenchEnabled ? '工作台' : '首页'}</Link>
      <span aria-hidden="true">/</span><span>网络巡检</span>
      <Link className="inspection-assistant-link" to="/">知识问答 <ArrowRightOutlined aria-hidden="true" /></Link>
    </div>
    <div className="inspection-window">
      <aside className="inspection-sidebar">
        <div className="inspection-brand"><span><RadarChartOutlined aria-hidden="true" /></span><div><strong>网络巡检</strong><small>NETOPS WORKSPACE</small></div></div>
        <div className="inspection-nav-label">巡检工作区</div>
        <nav aria-label="巡检导航">{inspectionViews.map((item, index) => {
          const Icon = icons[index]
          return <button type="button" key={item.key} aria-current={view.key === item.key ? 'page' : undefined} onClick={() => {
            const next = new URLSearchParams(params)
            next.set('patrol_view', item.key)
            setParams(next)
          }}><Icon aria-hidden="true" /><span>{item.label}</span></button>
        })}</nav>
        <div className="inspection-sidebar-note"><CheckCircleOutlined aria-hidden="true" /> 从采集到结论<p>核对检查范围，查看原始证据，<br />跟进风险与整改。</p></div>
      </aside>
      <main className="inspection-main" ref={mainRef}>
        <InspectionPageHost view={view.key}>
        <header className="inspection-heading">
          <div><span className="inspection-eyebrow">网络巡检 / {view.key === 'tasks' ? '任务中心' : view.label}</span><h1>{view.title}</h1><p>{view.description}</p></div>
          <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={onNewPatrol}>新建巡检</Button>
        </header>
        <div className="inspection-body">{children}</div>
        </InspectionPageHost>
      </main>
    </div>
  </div>
}
