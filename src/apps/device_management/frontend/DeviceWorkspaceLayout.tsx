import React, { useEffect, useRef } from 'react'
import { ConfigProvider } from 'antd'
import { ArrowLeftOutlined, CloudServerOutlined, ImportOutlined, SearchOutlined, SafetyCertificateOutlined, CodeOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import './DeviceWorkspace.css'

export default function DeviceWorkspaceLayout({ children, activeKey, title, subtitle, toolbar, onImport, onGroups }: {
  children: React.ReactNode; activeKey: string; title: string; subtitle: string; toolbar: React.ReactNode; onImport: () => void; onGroups: () => void
}) {
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const labelTables = () => root.current?.querySelectorAll<HTMLElement>('.ant-table-content, .ant-table-body').forEach(table => {
      table.tabIndex = 0; table.setAttribute('aria-label', '设备表格，可横向滚动查看完整内容')
    })
    labelTables()
    const observer = new MutationObserver(labelTables)
    if (root.current) observer.observe(root.current, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [])
  return <ConfigProvider theme={{ token: { colorPrimary: '#1667cb', borderRadius: 8, colorText: '#253248', colorTextSecondary: '#5e6e83' } }}
    drawer={{ classNames: { content: 'devices-overlay-content', header: 'devices-overlay-header', body: 'devices-overlay-body', footer: 'devices-overlay-footer' } }}
    modal={{ classNames: { content: 'devices-overlay-content', header: 'devices-overlay-header', body: 'devices-overlay-body', footer: 'devices-overlay-footer' } }}>
    <div className="devices-app" ref={root}>
      <a className="devices-skip" href="#devices-main">跳到工作区</a>
      <header className="devices-toolbar"><Link to="/workbench"><ArrowLeftOutlined aria-hidden="true" /> 工作台</Link><span>/</span><span>设备管理</span></header>
      <div className="devices-window">
        <aside className="devices-sidebar">
          <div className="devices-brand"><span><CloudServerOutlined aria-hidden="true" /></span><div><h1>设备管理</h1><small>NETOPS WORKSPACE</small></div></div>
          <div className="devices-nav-label">资产工作区</div>
          <nav aria-label="设备管理导航">
            <Link to="/apps/device-management?tab=commands" aria-current={activeKey === 'commands' ? 'page' : undefined}><CodeOutlined aria-hidden="true" /><span>配置命令集<small>维护采集命令与设备绑定</small></span></Link>
            <Link to="/apps/device-management" aria-current={activeKey === 'overview' ? 'page' : undefined}><CloudServerOutlined aria-hidden="true" /><span>设备资产<small>清单、连接与巡检命令</small></span></Link>
            <button onClick={onImport}><ImportOutlined aria-hidden="true" /><span>导入设备<small>预览核对后统一入库</small></span></button>
            <button onClick={onGroups}><SearchOutlined aria-hidden="true" /><span>动态分组<small>按属性维护设备范围</small></span></button>
          </nav>
          <div className="devices-sidebar-note"><SafetyCertificateOutlined aria-hidden="true" /> 统一设备资产<p>维护连接与设备信息，<br />为巡检提供准确的资产范围。</p></div>
        </aside>
        <main className="devices-main" id="devices-main" tabIndex={-1}>
          <div className="devices-eyebrow">设备管理 <span>/</span> {activeKey === 'commands' ? '配置命令集' : '资产清单'}</div>
          <header className="devices-page-heading"><div><h2>{activeKey === 'commands' ? '配置命令集' : title}</h2><p>{activeKey === 'commands' ? '维护巡检采集命令，预览匹配设备后确认绑定。' : subtitle}</p></div>{activeKey === 'overview' && <div className="devices-heading-actions">{toolbar}</div>}</header>
          <div className="devices-content">{children}</div>
        </main>
      </div>
    </div>
  </ConfigProvider>
}
