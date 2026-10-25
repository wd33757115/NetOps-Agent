import React from 'react'
import { ConfigProvider } from 'antd'
import {
  ApartmentOutlined, ApiOutlined, AppstoreOutlined, ArrowLeftOutlined, ClockCircleOutlined, CloudServerOutlined, ContactsOutlined,
  DatabaseOutlined, GlobalOutlined, ImportOutlined, NodeIndexOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { Link } from 'react-router-dom'
import './ledger.css'

export const ROOT = '/apps/resource-ledger'

type NavKey = 'prefixes' | 'zones' | 'systems' | 'hosts' | 'circuits' | 'resilience' | 'impact' | 'providers' | 'import' | 'changes'
const GROUPS: { label: string; items: { key: NavKey; to: string; icon: React.ReactNode; title: string; hint: string }[] }[] = [
  { label: 'IP 地址', items: [
    { key: 'prefixes', to: ROOT, icon: <ApartmentOutlined aria-hidden="true" />, title: '网段规划', hint: '总体资源逐级划分' },
    { key: 'zones', to: `${ROOT}/zones`, icon: <GlobalOutlined aria-hidden="true" />, title: '网络区域', hint: '地域、站点与区域' },
  ] },
  { label: '应用', items: [
    { key: 'systems', to: `${ROOT}/systems`, icon: <AppstoreOutlined aria-hidden="true" />, title: '业务系统', hint: '系统、子系统与地址块' },
    { key: 'hosts', to: `${ROOT}/hosts`, icon: <DatabaseOutlined aria-hidden="true" />, title: '主机台账', hint: '主机、状态与服务端口' },
  ] },
  { label: '专线', items: [
    { key: 'circuits', to: `${ROOT}/circuits`, icon: <ApiOutlined aria-hidden="true" />, title: '专线', hint: '电路、端点与报障' },
    { key: 'resilience', to: `${ROOT}/resilience`, icon: <NodeIndexOutlined aria-hidden="true" />, title: '站点韧性', hint: '到核心站点有没有单点' },
    { key: 'impact', to: `${ROOT}/impact`, icon: <ThunderboltOutlined aria-hidden="true" />, title: '故障影响', hint: '断一处会影响谁' },
    { key: 'providers', to: `${ROOT}/providers`, icon: <ContactsOutlined aria-hidden="true" />, title: '运营商与分类', hint: '运营商网络、局端、类型、共享风险' },
  ] },
  { label: '维护', items: [
    { key: 'import', to: `${ROOT}/import`, icon: <ImportOutlined aria-hidden="true" />, title: '导入与导出', hint: '预览核对后写入' },
    { key: 'changes', to: `${ROOT}/changes`, icon: <ClockCircleOutlined aria-hidden="true" />, title: '变更记录', hint: '谁在何时改了什么' },
  ] },
]

export default function Layout({ active, crumbs, title, subtitle, actions, children }: {
  active: NavKey; crumbs: React.ReactNode[]; title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode
}) {
  return <ConfigProvider theme={{ token: { colorPrimary: '#1671e8', borderRadius: 8, colorText: '#242b38', colorTextSecondary: '#5d6c82' } }}
    modal={{ classNames: { content: 'rl-overlay', header: 'rl-overlay-header', body: 'rl-overlay-body', footer: 'rl-overlay-footer' } }}
    drawer={{ classNames: { content: 'rl-overlay', header: 'rl-overlay-header', body: 'rl-overlay-body', footer: 'rl-overlay-footer' } }}
    button={{ autoInsertSpace: false }}>
    <div className="rl-app">
      <a className="rl-skip" href="#rl-main">跳到工作区</a>
      <header className="rl-toolbar"><Link to="/workbench"><ArrowLeftOutlined aria-hidden="true" /> 工作台</Link><span>/</span><span>资源台账</span></header>
      <div className="rl-window">
        <aside className="rl-sidebar">
          <div className="rl-brand"><span><CloudServerOutlined aria-hidden="true" /></span><div><strong>资源台账</strong><small>NETOPS WORKSPACE</small></div></div>
          {GROUPS.map(group => <div key={group.label}>
            <div className="rl-nav-label">{group.label}</div>
            <nav aria-label={group.label}>{group.items.map(item =>
              <Link key={item.key} to={item.to} aria-current={item.key === active ? 'page' : undefined}>
                {item.icon}<span>{item.title}<small>{item.hint}</small></span>
              </Link>)}
            </nav>
          </div>)}
        </aside>
        <main className="rl-main" id="rl-main" tabIndex={-1}>
          <div className="rl-crumbs">{crumbs.map((crumb, index) => <React.Fragment key={index}>{index > 0 && <span>/</span>}{crumb}</React.Fragment>)}</div>
          <header className="rl-heading"><div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>{actions && <div className="rl-actions">{actions}</div>}</header>
          {children}
        </main>
      </div>
    </div>
  </ConfigProvider>
}
