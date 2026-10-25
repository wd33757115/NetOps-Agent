import React, { useEffect, useState } from 'react'
import { Alert, Button, Empty, Input, Table, Tag } from 'antd'
import { RadarChartOutlined, SearchOutlined } from '@ant-design/icons'
import { discover, failureText, listDevices, type CapabilityView, type DefinitionSummary, type Device, type Refusal } from './api'
import Browse from './Browse'


function capabilityTag(capability: CapabilityView | null) {
  if (!capability) return <Tag>未发现</Tag>
  return capability.status === 'available' ? <Tag color="green">可用</Tag> : <Tag color="red">不可用</Tag>
}

/** Step 3: what each inventory device announces in its NETCONF hello. */
export default function Devices({ definitions = [], onDevices }: { definitions?: DefinitionSummary[]; onDevices?: (devices: Device[]) => void }) {
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string>()
  const [running, setRunning] = useState<string>()
  const [result, setResult] = useState<{ device: string; failure?: string; error?: string }>()
  const [search, setSearch] = useState('')

  async function load() {
    try {
      const found = await listDevices()
      setDevices(found)
      onDevices?.(found)
      setError('')
    } catch (err) {
      setError((err as Refusal).message)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { void load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function run(device: Device) {
    setRunning(device.device_id)
    setSelected(device.device_id)
    setResult(undefined)
    try {
      const view = await discover(device.device_id)
      setResult({ device: device.device_id, failure: view.failure_code || undefined })
      await load()
    } catch (err) {
      setResult({ device: device.device_id, error: (err as Refusal).message })
    } finally {
      setRunning(undefined)
    }
  }

  const current = devices.find(device => device.device_id === selected)
  const capability = current?.capability
  const modules = (capability?.modules || []).filter(module => !search || module.name.toLowerCase().includes(search.toLowerCase()))
  return <>
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
    {result?.failure && <Alert type="error" showIcon message={`能力发现失败：${failureText(result.failure)}`} style={{ marginBottom: 16 }} />}
    {result?.error && <Alert type="error" showIcon message={`能力发现失败：${result.error}`} style={{ marginBottom: 16 }} />}
    {result && !result.failure && !result.error && <Alert type="success" showIcon message="能力发现完成" style={{ marginBottom: 16 }} />}
    <Table<Device>
      rowKey="device_id"
      loading={loading}
      dataSource={devices}
      pagination={false}
      locale={{ emptyText: <Empty description="设备清单里没有启用的设备" /> }}
      rowClassName={device => device.device_id === selected ? 'nc-selected' : ''}
      onRow={device => ({ onClick: () => setSelected(device.device_id) })}
      columns={[
        { title: '设备', render: (_, d) => <div><strong>{d.device_name || d.device_id}</strong><div className="nc-muted">{d.ip}</div></div> },
        { title: '厂商与平台', render: (_, d) => `${d.vendor || '—'} ${d.platform || ''}${d.model ? ` · ${d.model}` : ''}` },
        { title: 'NETCONF', render: (_, d) => <>{capabilityTag(d.capability)}{d.capability?.failure_code && <span className="nc-muted">{failureText(d.capability.failure_code)}</span>}</> },
        { title: '模块 / 可用定义', render: (_, d) => d.capability ? `${d.capability.module_count} / ${d.capability.definitions.length}` : '—' },
        { title: '最近发现', render: (_, d) => d.capability?.checked_at ? new Date(d.capability.checked_at).toLocaleString('zh-CN') : '—' },
        { title: '', width: 140, render: (_, d) => <Button icon={<RadarChartOutlined aria-hidden="true" />} loading={running === d.device_id} disabled={!!running} onClick={event => { event.stopPropagation(); void run(d) }} aria-label={`能力发现 ${d.device_name || d.device_id}`}>能力发现</Button> },
      ]}
    />
    {current && <section className="nc-panel" aria-label="设备能力">
      <h2>{current.device_name || current.device_id}</h2>
      {!capability && <Empty description="还没有读取过这台设备的 NETCONF 能力，点击“能力发现”读取 hello" />}
      {capability && <>
        <div className="nc-split">
          <div>
            <h3>可用的定义（{capability.definitions.length}）</h3>
            {capability.definitions.length ? <div className="nc-tags">{capability.definitions.map(id => <Tag key={id} color="green" className="nc-mono">{id}</Tag>)}</div> : <p className="nc-muted">没有</p>}
          </div>
          <div>
            <h3>不可用的定义（{capability.unavailable?.length || 0}）</h3>
            {capability.unavailable?.length
              ? <ul className="nc-plain">{capability.unavailable.map(item => <li key={item.id}><span className="nc-mono">{item.id}</span> <span className="nc-muted">设备未通告模块 {item.module}</span></li>)}</ul>
              : <p className="nc-muted">没有（同厂商、同平台的已发布定义都可用）</p>}
          </div>
        </div>
        <div className="nc-filters" style={{ marginTop: 20 }}>
          <h3 style={{ margin: 0 }}>通告的模块（{capability.module_count}）</h3>
          <Input allowClear prefix={<SearchOutlined aria-hidden="true" />} placeholder="搜索模块" aria-label="搜索模块" value={search} onChange={event => setSearch(event.target.value)} style={{ width: 240, marginLeft: 'auto' }} />
        </div>
        <Table size="small" rowKey="name" dataSource={modules} pagination={{ pageSize: 20, hideOnSinglePage: true }}
          columns={[
            { title: '模块', dataIndex: 'name', render: (name: string) => <span className="nc-mono">{name}</span> },
            { title: '版本', dataIndex: 'revision', render: (revision: string | null) => revision || <span className="nc-muted">未声明</span> },
            { title: '命名空间', dataIndex: 'namespace', render: (ns: string) => <span className="nc-mono nc-muted">{ns}</span> },
          ]} />
      </>}
      <Browse key={current.device_id} deviceId={current.device_id} definitions={definitions} />
    </section>}
  </>
}
