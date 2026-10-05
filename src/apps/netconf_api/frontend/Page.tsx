import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, ConfigProvider, Table, Tag } from 'antd'
import { ApiOutlined, ArrowLeftOutlined, DatabaseOutlined, ExperimentOutlined, FileTextOutlined } from '@ant-design/icons'
import { api } from '@netops/config/api'
import { useAuth } from '@netops/context/AuthContext'
import './Page.css'

interface DefinitionSummary {
  id: string
  check_item: string | null
  vendor: string | null
  platforms: string[]
  description: string
  published_version: number | null
  has_draft: boolean
}

// Placeholder until the definition, capability and trial pages are designed: the
// published definitions, read-only, in the firewall-policy shell.
export default function NetconfApiPage() {
  const { user } = useAuth()
  const [rows, setRows] = useState<DefinitionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    api.get<DefinitionSummary[]>('/apps/netconf-api/definitions')
      .then(result => setRows(result.data))
      .catch(() => setError('无法读取定义，请检查应用权限。'))
      .finally(() => setLoading(false))
  }, [])
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
            <button type="button" className="active" aria-current="page"><FileTextOutlined aria-hidden="true" /><span><strong>接口定义</strong><small>表、值与翻译</small></span><em>01</em></button>
            <button type="button" disabled><DatabaseOutlined aria-hidden="true" /><span><strong>设备能力</strong><small>hello 通告的模块</small></span><em>02</em></button>
            <button type="button" disabled><ExperimentOutlined aria-hidden="true" /><span><strong>试运行</strong><small>值行与问题</small></span><em>03</em></button>
          </nav>
        </aside>
        <main className="nc-main">
          <div className="nc-heading"><div className="nc-eyebrow">接口定义 / 01</div><h1>接口定义</h1>
            <p>每条定义把设备的一张 NETCONF 表映射成规则治理检查项的值，和 CLI 采集产出同样的行。</p></div>
          {error && <Alert type="error" message={error} style={{ marginBottom: 20 }} />}
          <Table<DefinitionSummary>
            rowKey="id"
            loading={loading}
            dataSource={rows}
            pagination={false}
            columns={[
              { title: '定义', dataIndex: 'id', render: (id: string, row) => <div><span className="nc-mono">{id}</span>{row.description && <div className="nc-muted">{row.description}</div>}</div> },
              { title: '检查项', dataIndex: 'check_item', render: (value: string | null) => value ? <span className="nc-mono">{value}</span> : <span className="nc-muted">仅供关联</span> },
              { title: '设备', render: (_, row) => `${row.vendor || ''} ${row.platforms.join(', ')}` },
              { title: '版本', render: (_, row) => <>{row.published_version ? `v${row.published_version}` : <span className="nc-muted">未发布</span>}{row.has_draft && <Tag style={{ marginLeft: 8 }}>草稿</Tag>}</> },
            ]}
          />
        </main>
      </div>
    </div>
  </ConfigProvider>
}
