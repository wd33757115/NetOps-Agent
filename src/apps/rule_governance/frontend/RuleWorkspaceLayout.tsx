import React, { useEffect, useRef } from 'react'
import { ConfigProvider } from 'antd'
import { AppstoreOutlined, ArrowLeftOutlined, ArrowRightOutlined, AuditOutlined, CheckSquareOutlined, CloudServerOutlined, DatabaseOutlined, DeploymentUnitOutlined, ImportOutlined, UnorderedListOutlined } from '@ant-design/icons'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { resolveParserControlFocus } from './components/skills/parser/parserControlFocus'
import ParserValueMappingManager from './components/skills/parser/ParserValueMappingManager'
import './components/skills/parser/parser-workspace.css'
import './RuleWorkspace.css'

const sections = [
  { key: 'tasks', label: '制作任务', hint: '生成、验证与发布', query: 'workspace=production&subtab=asset-production-units', icon: UnorderedListOutlined, group: '规则制作' },
  { key: 'sources', label: '样本导入', hint: '命令输出与原始证据', query: 'workspace=production&subtab=source-jobs', icon: ImportOutlined },
  { key: 'catalog', label: '检查项目录', hint: '检查标准与设备适配', query: 'workspace=production&subtab=catalog-management', icon: AppstoreOutlined },
  { key: 'inventory', label: '已发布资产', hint: '生效版本与验证依据', query: 'workspace=inventory', icon: DatabaseOutlined, group: '运行与治理' },
  { key: 'transfer', label: '规则包导入导出', hint: '跨环境迁移与版本核对', query: 'workspace=transfer', icon: ImportOutlined },
  { key: 'runtime', label: '覆盖与回填', hint: '覆盖健康与渐进发布', query: 'workspace=runtime', icon: DeploymentUnitOutlined },
  { key: 'audit', label: '审计记录', hint: '追溯每次规则变更', query: 'workspace=audit', icon: AuditOutlined },
]

export default function RuleWorkspaceLayout({ children, title, subtitle, toolbar, activeKey }: {
  children: React.ReactNode; title?: string; subtitle?: string; toolbar?: React.ReactNode; activeKey?: string
}) {
  const [params] = useSearchParams()
  const location = useLocation()
  const focus = resolveParserControlFocus(params)
  const selected = activeKey || (focus.workspace === 'production'
    ? focus.subWorkspace === 'source-jobs' ? 'sources' : focus.subWorkspace === 'catalog-management' ? 'catalog' : 'tasks'
    : ['inventory', 'runtime', 'audit', 'transfer'].includes(focus.workspace) ? focus.workspace : 'tasks')
  const view = sections.find(item => item.key === selected) || sections[0]
  const root = useRef<HTMLDivElement>(null)
  const main = useRef<HTMLElement>(null)
  const nav = useRef<HTMLElement>(null)
  const workspace = params.get('workspace'), subtab = params.get('subtab')
  useEffect(() => { main.current?.scrollTo?.({ top: 0 }) }, [location.pathname, workspace, subtab])
  useEffect(() => {
    if (window.matchMedia('(max-width: 767px)').matches)
      nav.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest', inline: 'center' })
  }, [selected])
  useEffect(() => {
    const element = root.current
    if (!element) return
    const labelTables = () => {
      element.querySelectorAll<HTMLElement>('.ant-table-content, .ant-table-body').forEach(table => {
        table.tabIndex = 0
        table.setAttribute('aria-label', '规则表格，可横向滚动查看完整内容')
      })
      // rc-table clones header controls into an aria-hidden measuring row.
      element.querySelectorAll<HTMLElement>('.ant-table-measure-row').forEach(row => row.setAttribute('inert', ''))
    }
    labelTables()
    const observer = new MutationObserver(labelTables)
    observer.observe(element, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [])
  const destination = (item: typeof sections[number]) => {
    const query = new URLSearchParams(item.query)
    const origin = params.get('return_to')
    if (origin?.startsWith('/apps/patrol?')) query.set('return_to', origin)
    return `/apps/rule-governance?${query}`
  }
  return <ConfigProvider theme={{ token: { colorPrimary: '#1667cb', borderRadius: 8, colorText: '#253248', colorTextSecondary: '#5e6e83' } }}
    drawer={{ classNames: { content: 'rules-overlay-content', header: 'rules-overlay-header', body: 'rules-overlay-body', footer: 'rules-overlay-footer' } }}
    modal={{ classNames: { content: 'rules-overlay-content', header: 'rules-overlay-header', body: 'rules-overlay-body', footer: 'rules-overlay-footer' } }}>
    <div className="rules-app" ref={root}>
      <a className="rules-skip" href="#rules-main">跳到工作区</a>
      <header className="rules-toolbar"><Link to="/workbench"><ArrowLeftOutlined aria-hidden="true" /> 工作台</Link><span aria-hidden="true">/</span><span>规则治理</span>
        <Link className="rules-chat" to="/chat">聊天问答 <ArrowRightOutlined aria-hidden="true" /></Link></header>
      <div className="rules-window">
        <aside className="rules-sidebar">
          <div className="rules-brand"><span><CheckSquareOutlined aria-hidden="true" /></span><div><h1>规则治理</h1><small>NETOPS WORKSPACE</small></div></div>
          <nav aria-label="规则治理导航" ref={nav}>{sections.map(item => <React.Fragment key={item.key}>
            {item.group && <div className="rules-nav-label">{item.group}</div>}
            <Link to={destination(item)} aria-current={selected === item.key ? 'page' : undefined}>
              <item.icon aria-hidden="true" /><span>{item.label}<small>{item.hint}</small></span>
            </Link>
          </React.Fragment>)}</nav>
          <div className="rules-settings"><ParserValueMappingManager /></div>
          <div className="rules-sidebar-note"><CheckSquareOutlined aria-hidden="true" /> 验证后发布<p>从命令样本制作规则，<br />保留原始证据与变更记录。</p></div>
        </aside>
        <main className="rules-main" id="rules-main" ref={main} tabIndex={-1}>
          <div className="rules-eyebrow">规则治理 <span>/</span> {view.label}{activeKey === 'sources' && title ? ' / 任务详情' : ''}</div>
          {title && <header className="rules-page-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{toolbar && <div className="rules-heading-actions">{toolbar}</div>}</header>}
          <div className="rules-content parser-detail-shell">{children}</div>
        </main>
      </div>
    </div>
  </ConfigProvider>
}
