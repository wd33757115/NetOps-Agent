import React, { useState } from 'react'
import { Alert, Button, Input, Select, Spin, Tag } from 'antd'
import { ArrowRightOutlined, CloudServerOutlined, FileTextOutlined, PlusOutlined, RadarChartOutlined, ReloadOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { useSearchParams } from 'react-router-dom'
import { offlinePatrolApi, workflowApi } from '@netops/services/api'

type Task = { id: string; name: string; source: 'online' | 'offline'; time: string; status: string; scope: string; note: string }
const labels: Record<string, string> = {
  completed: '执行完成', active: '进行中', running: '进行中', in_progress: '进行中', collecting: '采集中', dispatching: '派发中',
  queued: '排队中', pending: '等待执行', failed: '需要修复', blocked: '需要处理', draft: '待上传', uploading: '待预检', ready: '可执行', cancelled: '已取消',
}
const activeStates = new Set(['active', 'running', 'in_progress', 'collecting', 'dispatching', 'queued', 'pending'])
const attentionStates = new Set(['failed', 'blocked', 'draft', 'uploading', 'ready'])
const taskKey = (task: Task) => `${task.source}:${task.id}`
const dateLabel = (value: string) => Number.isNaN(Date.parse(value)) ? '时间未记录' : new Date(value).toLocaleString('zh-CN', { hour12: false })
const statusColor = (status: string) => ['failed', 'blocked'].includes(status) ? 'orange' : status === 'completed' ? 'green' : activeStates.has(status) ? 'blue' : 'default'

export default function PatrolTaskInbox({ onNewPatrol, embedded = false }: { onNewPatrol?: () => void; embedded?: boolean }) {
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [source, setSource] = useState<'online' | 'offline'>()
  const [selectedKey, setSelectedKey] = useState<string>()
  const online = useQuery(['patrol-inbox-online', search], () => workflowApi.listPatrolBatches({ limit: 20, search }), { staleTime: 10000, keepPreviousData: true, refetchOnWindowFocus: false,
    refetchInterval: page => page?.items.some(row => activeStates.has(row.aggregate_status || 'pending')) ? 10000 : 60000 })
  const offline = useQuery(['patrol-inbox-offline', search], () => offlinePatrolApi.list({ page_size: 20, search }), { staleTime: 10000, keepPreviousData: true, refetchOnWindowFocus: false,
    refetchInterval: page => page?.items.some(row => activeStates.has(row.status)) ? 10000 : 60000 })
  const rows: Task[] = [
    ...(online.data?.items || []).map(row => ({ id: row.patrol_batch_id, name: row.target_label || row.ticket_id || '在线巡检', source: 'online' as const,
      time: row.created_at || '', status: row.aggregate_status || 'pending', scope: `${row.target_count ?? row.total ?? 0} 台设备`,
      note: row.failed_count || row.skipped_count ? `${row.failed_count || 0} 台失败 · ${row.skipped_count || 0} 台跳过` : '查看检查项结论和覆盖范围' })),
    ...(offline.data?.items || []).map(row => ({ id: row.id, name: row.display_name || row.name || '离线巡检', source: 'offline' as const,
      time: row.created_at, status: row.status, scope: `${row.preview_totals?.device_count || 0} 台设备 · ${row.preview_totals?.file_count || 0} 份文件`,
      note: row.blocker_count ? `${row.blocker_count} 项待处理` : row.warning_count ? `${row.warning_count} 项质量提醒` : '历史分析 · 查看采集周期和结果' })),
  ].filter(row => !source || row.source === source).sort((a, b) => (Date.parse(b.time) || 0) - (Date.parse(a.time) || 0))
  const selected = rows.find(row => taskKey(row) === selectedKey) || rows[0]
  const loading = online.isLoading || offline.isLoading
  const hasError = online.isError || offline.isError
  const refresh = () => { void online.refetch(); void offline.refetch() }
  const open = (task: Task) => {
    const next = new URLSearchParams(params)
    next.delete('offline_job_id'); next.delete('offline_job_tab'); next.delete('patrol_batch_id')
    next.set('patrol_view', task.source)
    if (task.source === 'offline') { next.set('offline_job_id', task.id); next.set('offline_job_tab', task.status === 'completed' ? 'result' : 'overview') }
    else next.set('patrol_batch_id', task.id)
    setParams(next)
  }
  return <section className="inspection-inbox" aria-label="最近巡检任务">
    {!embedded && <h2>巡检任务</h2>}
    <div className="inspection-overview" aria-label="当前列表统计">
      <span><b>{loading ? '—' : rows.length}</b> 最近任务</span>
      <span><b>{loading ? '—' : rows.filter(row => activeStates.has(row.status)).length}</b> 进行中</span>
      <span><b>{loading ? '—' : rows.filter(row => attentionStates.has(row.status)).length}</b> 待处理</span>
      <span className="inspection-overview-note">{hasError ? '部分数据未加载' : '在线、离线各取最近 20 项'}</span>
    </div>
    <div className="inspection-filters">
      <Input.Search aria-label="搜索巡检任务" allowClear placeholder="搜索范围、任务或工单" value={searchInput}
        onChange={event => { setSearchInput(event.target.value); if (!event.target.value) setSearch('') }} onSearch={value => setSearch(value.trim())} />
      <Select aria-label="任务来源" value={source || 'all'} onChange={value => setSource(value === 'all' ? undefined : value)}
        options={[{ value: 'all', label: '全部来源' }, { value: 'online', label: '在线采集' }, { value: 'offline', label: '离线文件' }]} />
      <Button icon={<ReloadOutlined aria-hidden="true" spin={online.isFetching || offline.isFetching} />} onClick={refresh}>刷新</Button>
    </div>
    {hasError && <Alert showIcon type="warning" message="部分任务加载失败" description="已加载任务仍可打开；统计仅包含已加载的数据。" action={<Button onClick={refresh}>重试</Button>} />}
    <div className="inspection-task-layout">
      <div className="inspection-task-list" aria-busy={loading}>
        {loading && !rows.length ? <div className="inspection-empty"><Spin /><p role="status">正在加载巡检任务…</p></div> : rows.length ? <>
          <div className="inspection-list-heading" aria-hidden="true"><span>任务 / 范围</span><span>状态 / 待办</span><span>操作</span></div>
          <ul>{rows.map(row => <li key={taskKey(row)} className={taskKey(selected) === taskKey(row) ? 'is-selected' : ''}>
            <div className="inspection-task-identity">
              <span className="inspection-task-icon">{row.source === 'online' ? <CloudServerOutlined aria-hidden="true" /> : <FileTextOutlined aria-hidden="true" />}</span>
              <div><button type="button" className="inspection-task-name" aria-pressed={taskKey(selected) === taskKey(row)} onClick={() => setSelectedKey(taskKey(row))}>{row.name}</button>
                <p>{row.source === 'online' ? '在线采集' : '离线文件'}<span>·</span>{row.scope}</p><time dateTime={row.time || undefined}>{dateLabel(row.time)}</time></div>
            </div>
            <div className="inspection-task-status"><Tag color={statusColor(row.status)}>{labels[row.status] || row.status}</Tag><p>{row.note}</p></div>
            <Button type="link" className="inspection-task-open" onClick={() => open(row)}>{row.status === 'completed' ? '查看结果' : '继续处理'} <ArrowRightOutlined aria-hidden="true" /></Button>
          </li>)}</ul>
          <p className="inspection-list-note">执行完成后，请核对业务结论和未覆盖项。更多历史任务可在在线、离线巡检中查看。</p>
        </> : <div className="inspection-empty">
          <span className="inspection-empty-icon"><RadarChartOutlined aria-hidden="true" /></span>
          <h2>{hasError ? '暂时无法显示任务' : search || source ? '没有匹配的巡检任务' : '从一次巡检开始'}</h2>
          <p>{hasError ? '刷新重试后，继续查看任务和历史结果。' : search || source ? '调整关键词或任务来源，再试一次。' : '连接设备在线采集，或导入已有采集文件。'}</p>
          {hasError ? <Button onClick={refresh}>重新加载</Button> : search || source ? <Button onClick={() => { setSearch(''); setSearchInput(''); setSource(undefined) }}>清除筛选</Button> : onNewPatrol && <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={onNewPatrol}>创建首次巡检</Button>}
        </div>}
      </div>
      <aside className="inspection-inspector" aria-label={selected ? '任务摘要' : '巡检指引'}>
        <span className="inspection-eyebrow">{selected ? '任务摘要' : '开始之前'}</span>
        <h2>{selected ? selected.name : '选择合适的数据来源'}</h2>
        {selected ? <>
          <Tag color={statusColor(selected.status)}>{labels[selected.status] || selected.status}</Tag>
          <dl><div><dt>数据来源</dt><dd>{selected.source === 'online' ? '在线设备采集' : '离线采集文件'}</dd></div><div><dt>巡检范围</dt><dd>{selected.scope}</dd></div><div><dt>创建时间</dt><dd>{dateLabel(selected.time)}</dd></div><div><dt>任务编号</dt><dd className="inspection-task-id">{selected.id}</dd></div></dl>
          <Button block onClick={() => open(selected)}>{selected.status === 'completed' ? '打开巡检结果' : '打开任务'} <ArrowRightOutlined aria-hidden="true" /></Button>
          <div className="inspection-inspector-note"><strong>下一步</strong><p>{selected.note}。{selected.status === 'completed' ? '执行完成不等于设备健康，请结合覆盖范围与证据判断。' : '进入任务查看进度及待办事项。'}</p></div>
        </> : <div className="inspection-guidance">
          <div><CloudServerOutlined aria-hidden="true" /><h3>在线采集</h3><p>选择设备范围，预检命令与检查项，再发起采集。</p></div>
          <div><FileTextOutlined aria-hidden="true" /><h3>离线文件</h3><p>上传已有采集文件，核对设备映射与采集时间。</p></div>
          <div className="inspection-inspector-note"><strong>结果可追溯</strong><p>完成后查看检查结论与原始证据，生成报告并跟进风险。</p></div>
        </div>}
      </aside>
    </div>
  </section>
}
