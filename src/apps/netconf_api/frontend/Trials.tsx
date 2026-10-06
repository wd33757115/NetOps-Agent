import React, { useEffect, useState } from 'react'
import { Alert, Button, Empty, Select, Statistic, Switch, Table, Tag } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { listDevices, listTrials, PROBLEM_TEXT, readTrial, runTrial, type DefinitionSummary, type Device, type Problem, type Refusal, type TrialView, failureText } from './api'

const duration = (trial: TrialView) => trial.started_at && trial.finished_at
  ? `${Math.max(0, new Date(trial.finished_at).getTime() - new Date(trial.started_at).getTime())} ms` : '—'

const size = (bytes: number | null) => bytes === null || bytes === undefined ? '—'
  : bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`

function TrialResult({ trial }: { trial: TrialView }) {
  const rows = trial.rows || []
  const problems = trial.problems || []
  const names = [...new Set(rows.flatMap(row => Object.keys(row)).filter(name => name !== 'ref'))]
  return <section className="nc-panel" aria-label="试运行结果">
    <div className="nc-stats">
      <Statistic title="结果" valueRender={() => trial.status === 'succeeded' ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag>} />
      <Statistic title="设备返回的行" value={trial.read_count ?? '—'} />
      <Statistic title="输出的值行" value={trial.row_count ?? '—'} />
      <Statistic title="问题" value={trial.problem_count ?? '—'} valueStyle={trial.problem_count ? { color: '#cf1322' } : undefined} />
      <Statistic title="耗时" value={duration(trial)} />
      <Statistic title="回包大小" value={size(trial.reply_bytes)} />
      <Statistic title="定义版本" value={trial.version ? `v${trial.version}` : '草稿'} />
    </div>
    {trial.status === 'failed' && <Alert type="error" showIcon message={`试运行失败：${failureText(trial.failure_code)}`} description={trial.message || undefined} style={{ marginBottom: 16 }} />}
    {problems.length > 0 && <>
      <Alert type="warning" showIcon message={`${problems.length} 个问题：有问题的表不会被拓扑使用，请修正定义后再发布`} style={{ marginBottom: 12 }} />
      <Table<Problem> size="small" rowKey={(p, index) => `${p.row}-${p.value}-${index}`} dataSource={problems} pagination={{ pageSize: 20, hideOnSinglePage: true }} className="nc-problems"
        columns={[
          { title: '类型', dataIndex: 'kind', render: (kind: string) => <Tag color="red">{PROBLEM_TEXT[kind] || kind}</Tag> },
          { title: '设备第几行', dataIndex: 'row' },
          { title: '行标识', dataIndex: 'key', render: (key: string) => <span className="nc-mono">{key}</span> },
          { title: '值或叶子', dataIndex: 'value', render: (value: string) => <span className="nc-mono">{value}</span> },
          { title: '设备原文', dataIndex: 'raw', render: (raw: string | null) => raw === null ? <span className="nc-muted">—</span> : <span className="nc-mono">{raw}</span> },
          { title: '表', dataIndex: 'table', render: (table: string) => <span className="nc-mono nc-muted">{table}</span> },
        ]} />
    </>}
    {trial.status === 'succeeded' && <>
      <h3>值行（{trial.row_count}{rows.length < (trial.row_count || 0) ? `，显示前 ${rows.length} 行` : ''}）</h3>
      <Table size="small" rowKey="ref" dataSource={rows} pagination={{ pageSize: 50, hideOnSinglePage: true }} scroll={{ x: 'max-content' }}
        locale={{ emptyText: '没有值行' }}
        columns={[{ title: '#', dataIndex: 'ref', width: 60 }, ...names.map(name => ({
          title: <span className="nc-mono">{name}</span>, dataIndex: name,
          render: (value: string | number) => value === '' || value === undefined ? <span className="nc-muted">—</span> : <span className="nc-mono">{String(value)}</span>,
        }))]} />
    </>}
  </section>
}

/** Step 4: run a definition (published or draft) on one device, and the runs kept. */
export default function Trials({ definitions, initial }: { definitions: DefinitionSummary[]; initial?: string }) {
  const [devices, setDevices] = useState<Device[]>([])
  const [definitionId, setDefinitionId] = useState<string | undefined>(initial)
  const [deviceId, setDeviceId] = useState<string>()
  const [draft, setDraft] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [trial, setTrial] = useState<TrialView>()
  const [history, setHistory] = useState<TrialView[]>([])
  const definition = definitions.find(row => row.id === definitionId)

  useEffect(() => { listDevices().then(setDevices).catch(err => setError((err as Refusal).message)) }, [])
  useEffect(() => {
    listTrials(definitionId).then(setHistory).catch(err => setError((err as Refusal).message))
  }, [definitionId, trial])
  useEffect(() => { if (!definition?.has_draft) setDraft(false) }, [definition])

  async function run() {
    if (!definitionId || !deviceId) return
    setRunning(true)
    setError('')
    try {
      setTrial(await runTrial(deviceId, definitionId, draft))
    } catch (err) {
      setError((err as Refusal).message)
    } finally {
      setRunning(false)
    }
  }
  async function open(id: string) {
    try {
      setTrial(await readTrial(id))
    } catch (err) {
      setError((err as Refusal).message)
    }
  }
  const deviceName = (id: string) => devices.find(device => device.device_id === id)?.device_name || id
  return <>
    <div className="nc-filters">
      <Select showSearch aria-label="选择定义" placeholder="选择定义" value={definitionId} onChange={setDefinitionId} style={{ width: 320 }}
        options={definitions.map(row => ({ value: row.id, label: row.id }))} />
      <Select showSearch aria-label="选择设备" placeholder="选择设备" value={deviceId} onChange={setDeviceId} style={{ width: 260 }}
        optionFilterProp="label"
        options={devices.map(device => ({ value: device.device_id, label: `${device.device_name || device.device_id} ${device.ip || ''}` }))} />
      <label className="nc-inline"><Switch aria-label="用草稿" checked={draft} disabled={!definition?.has_draft} onChange={setDraft} /> 用草稿</label>
      <Button type="primary" icon={<PlayCircleOutlined aria-hidden="true" />} loading={running} disabled={!definitionId || !deviceId} onClick={run}>试运行</Button>
    </div>
    {devices.length === 0 && !error && <p className="nc-muted">设备清单里没有启用的设备，先在设备清单中添加。</p>}
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
    {trial && <TrialResult trial={trial} />}
    <section className="nc-panel" aria-label="试运行记录">
      <h3>试运行记录{definitionId ? `（${definitionId}）` : ''}</h3>
      <Table<TrialView> size="small" rowKey="id" dataSource={history} pagination={{ pageSize: 10, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="还没有试运行记录" /> }}
        onRow={row => ({ onClick: () => void open(row.id), className: 'nc-clickable' })}
        columns={[
          { title: '时间', dataIndex: 'started_at', render: (at: string | null) => at ? new Date(at).toLocaleString('zh-CN') : '—' },
          { title: '定义', dataIndex: 'definition_id', render: (id: string, row) => <><span className="nc-mono">{id}</span> <span className="nc-muted">{row.version ? `v${row.version}` : '草稿'}</span></> },
          { title: '设备', dataIndex: 'device_id', render: (id: string) => deviceName(id) },
          { title: '结果', render: (_, row) => row.status === 'succeeded'
            ? <>{row.row_count} 行{row.problem_count ? <Tag color="red" style={{ marginLeft: 8 }}>{row.problem_count} 个问题</Tag> : null}</>
            : <Tag color="red">{failureText(row.failure_code)}</Tag> },
          { title: '耗时', render: (_, row) => duration(row) },
        ]} />
    </section>
  </>
}
