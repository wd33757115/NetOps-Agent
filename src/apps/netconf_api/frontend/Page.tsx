import React, { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Button, ConfigProvider, Empty, Result, Spin } from 'antd'
import { ApiOutlined, ArrowLeftOutlined, DatabaseOutlined, EditOutlined, ExperimentOutlined, FileTextOutlined } from '@ant-design/icons'
import { useAuth } from '@netops/context/AuthContext'
import { FORBIDDEN, listDefinitions, readDefinition, saveDraft, type DefinitionDetail, type DefinitionSummary, type Refusal } from './api'
import Definitions, { SKELETON } from './Definitions'
import Editor from './Editor'
import Devices from './Devices'
import Trials from './Trials'
import './Page.css'

type Step = 'definitions' | 'editor' | 'devices' | 'trials'

const STEPS: { key: Step; title: string; hint: string; icon: React.ReactNode; heading: string; intro: string }[] = [
  { key: 'definitions', title: '接口定义', hint: '表、值与翻译', icon: <FileTextOutlined aria-hidden="true" />, heading: '接口定义',
    intro: '每条定义把设备的一张 NETCONF 表映射成规则治理检查项的值，和 CLI 采集产出同样的行。新设备型号只需新建定义，不改代码。' },
  { key: 'editor', title: '编辑与发布', hint: '草稿、版本与差异', icon: <EditOutlined aria-hidden="true" />, heading: '编辑与发布',
    intro: '草稿保存时由后端校验；发布时检查是否覆盖了检查项的必填值。发布后的版本不可修改，拓扑采集只用已发布版本。' },
  { key: 'devices', title: '设备能力', hint: 'hello 通告的模块', icon: <DatabaseOutlined aria-hidden="true" />, heading: '设备能力',
    intro: '经平台打开 NETCONF 会话读取设备的 hello，记录它通告的模块，并据此判断哪些定义能在这台设备上使用；还可以只读地浏览设备的数据。' },
  { key: 'trials', title: '试运行', hint: '值行与问题', icon: <ExperimentOutlined aria-hidden="true" />, heading: '试运行',
    intro: '在一台设备上运行一条定义（已发布版本或草稿），查看它产出的值行和发现的问题。试运行不影响拓扑采集。' },
]

export default function NetconfApiPage() {
  const { user } = useAuth()
  const [step, setStep] = useState<Step>('definitions')
  const [rows, setRows] = useState<DefinitionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [forbidden, setForbidden] = useState(false)
  const [selected, setSelected] = useState<string>()
  const [detail, setDetail] = useState<DefinitionDetail>()
  const [detailError, setDetailError] = useState('')

  const loadList = useCallback(async () => {
    try {
      setRows(await listDefinitions())
      setError('')
    } catch (err) {
      const refusal = err as Refusal
      if (refusal.status === 403) setForbidden(true)
      setError(refusal.message)
    } finally {
      setLoading(false)
    }
  }, [])
  const loadDetail = useCallback(async (id: string) => {
    try {
      setDetail(await readDefinition(id))
      setDetailError('')
    } catch (err) {
      setDetail(undefined)
      setDetailError((err as Refusal).message)
    }
  }, [])
  useEffect(() => { void loadList() }, [loadList])

  async function open(id: string) {
    setSelected(id)
    setDetail(undefined)
    setStep('editor')
    await loadDetail(id)
  }
  async function create(id: string, from?: string) {
    let body: Record<string, unknown> = SKELETON(id)
    if (from) {
      const source = await readDefinition(from)
      body = { ...(source.draft || source.published?.body || {}), id }
    }
    await saveDraft(id, body, 0)
    await loadList()
    await open(id)
  }
  async function changed() {
    if (selected) await loadDetail(selected)
    await loadList()
  }

  const current = STEPS.find(item => item.key === step)!
  const index = STEPS.indexOf(current) + 1
  return <ConfigProvider theme={{ token: { colorPrimary: '#1671e8', borderRadius: 9, colorText: '#242b38', colorBorder: '#dce1e9', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif', controlHeight: 40 } }}>
    <div className="nc-app">
      <header className="nc-toolbar">
        <div className="nc-breadcrumb"><Link className="nc-back" to="/workbench"><ArrowLeftOutlined aria-hidden="true" /> 工作台</Link><span className="nc-breadcrumb-divider">/</span><span className="nc-toolbar-title">NETCONF 接口</span></div>
        <span className="nc-account" title={user?.username} aria-label={`当前用户 ${user?.username || ''}`}>{user?.username?.slice(0, 1).toUpperCase() || 'N'}</span>
      </header>
      <div className="nc-window">
        <aside className="nc-sidebar">
          <div className="nc-product"><span className="nc-product-icon"><ApiOutlined aria-hidden="true" /></span><div><strong>NETCONF 接口</strong><span>NETOPS WORKSPACE</span></div></div>
          <div className="nc-nav-label">工作流程</div>
          <nav className="nc-workflow" aria-label="NETCONF 接口">
            {STEPS.map((item, number) => <button key={item.key} type="button" className={item.key === step ? 'active' : undefined}
              aria-current={item.key === step ? 'page' : undefined} disabled={forbidden} onClick={() => setStep(item.key)}>
              {item.icon}<span><strong>{item.title}</strong><small>{item.hint}</small></span><em>{String(number + 1).padStart(2, '0')}</em>
            </button>)}
          </nav>
        </aside>
        <main className="nc-main">
          <div className="nc-heading"><div className="nc-eyebrow">{current.heading} / {String(index).padStart(2, '0')}</div><h1>{current.heading}</h1>
            <p>{current.intro}</p></div>
          {forbidden ? <Result status="403" title="无权限" subTitle={FORBIDDEN} /> : <>
            {step === 'definitions' && <Definitions rows={rows} loading={loading} error={error} onOpen={id => void open(id)} onCreate={create} />}
            {step === 'editor' && (!selected
              ? <Empty description={<>先在“接口定义”里选一条定义 <Button type="link" onClick={() => setStep('definitions')}>去选择</Button></>} />
              : detailError ? <Alert type="error" showIcon message={detailError} />
              : !detail ? <div className="nc-loading"><Spin tip="加载中" /></div>
              : <Editor key={`${detail.id}`} detail={detail} onChanged={changed} />)}
            {step === 'devices' && <Devices definitions={rows} />}
            {step === 'trials' && <Trials definitions={rows} initial={selected} />}
          </>}
        </main>
      </div>
    </div>
  </ConfigProvider>
}
