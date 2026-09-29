import React, { useEffect, useRef } from 'react'
import { ConfigProvider } from 'antd'
import { ArrowLeftOutlined, SafetyCertificateOutlined, TeamOutlined, UserOutlined, AppstoreOutlined } from '@ant-design/icons'
import { Link, useLocation } from 'react-router-dom'
import './system.css'
import { useAuth } from '../../context/AuthContext'

export default function SystemLayout({ title, subtitle, toolbar, children }: {
  title: string; subtitle: string; toolbar?: React.ReactNode; children: React.ReactNode
}) {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const { pathname } = useLocation()
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const label = () => root.current?.querySelectorAll<HTMLElement>('.ant-table-content').forEach(el => {
      el.tabIndex = 0; el.setAttribute('aria-label', '管理表格，可横向滚动查看完整内容')
    })
    label()
    const observer = new MutationObserver(label)
    if (root.current) observer.observe(root.current, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [])
  return <ConfigProvider theme={{ token: { colorPrimary: '#1667cb', borderRadius: 8, colorText: '#253248', colorTextSecondary: '#5e6e83' } }}
    drawer={{ classNames: { content: 'system-overlay-content', header: 'system-overlay-header', body: 'system-overlay-body', footer: 'system-overlay-footer' } }}
    modal={{ classNames: { content: 'system-overlay-content', header: 'system-overlay-header', body: 'system-overlay-body', footer: 'system-overlay-footer' } }}>
    <div className="system-app" ref={root}>
      <a className="system-skip" href="#system-main">跳到工作区</a>
      <header className="system-toolbar"><Link to="/workbench"><ArrowLeftOutlined aria-hidden="true" /> 工作台</Link><span>/</span><span>系统管理</span></header>
      <div className="system-window">
        <aside className="system-sidebar">
          <div className="system-brand"><span><SafetyCertificateOutlined aria-hidden="true" /></span><div><h1>系统管理</h1><small>NETOPS ADMINISTRATION</small></div></div>
          <div className="system-nav-label">组织与访问</div>
          <nav aria-label="系统管理导航">{[
            { path: '/system/users', title: '用户', hint: '账号、角色与登录状态', icon: UserOutlined },
            { path: '/system/groups', title: '用户组', hint: '业务分组与成员维护', icon: TeamOutlined },
            { path: '/system/apps', title: '应用授权', hint: '使用权限与授权来源', icon: AppstoreOutlined },
          ].filter(item => isAdmin || item.path === '/system/apps').map(item => <Link key={item.path} to={item.path} aria-current={pathname === item.path ? 'page' : undefined}><item.icon aria-hidden="true" /><span>{item.title}<small>{item.hint}</small></span></Link>)}</nav>
          <div className="system-sidebar-note"><SafetyCertificateOutlined aria-hidden="true" /> {isAdmin ? 'admin · 平台管理员' : 'netops-config · 应用授权管理'}<p>{isAdmin ? '维护账号、用户组与全部系统权限。' : '为用户和用户组授予或撤销应用使用权。'}</p></div>
        </aside>
        <main id="system-main" className="system-main" tabIndex={-1}>
          <div className="system-eyebrow">系统管理 <span>/</span> {title}</div>
          <header className="system-page-heading"><div><h2>{title}</h2><p>{subtitle}</p></div></header>
          {toolbar && <div className="system-filters">{toolbar}</div>}
          <div className="system-content">{children}</div>
        </main>
      </div>
    </div>
  </ConfigProvider>
}
