import React, { useState } from 'react'
import { Alert, Button, Input, List, Select, Space, Table, Tag, Typography } from 'antd'
import { useQuery } from 'react-query'
import { useSearchParams } from 'react-router-dom'
import { useIsMobile } from '../../hooks/useBreakpoint'
import { offlinePatrolApi, workflowApi } from '../../services/api'

type Task = { id: string; name: string; source: 'online' | 'offline'; time: string; status: string; scope: string; note: string }
const labels: Record<string, string> = { completed: '执行完成', active: '进行中', running: '进行中', queued: '排队中',
  pending: '等待执行', failed: '需要修复', blocked: '需要处理', draft: '待上传', uploading: '待预检', ready: '可执行', cancelled: '已取消' }

export default function PatrolTaskInbox() {
  const isMobile = useIsMobile()
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [source, setSource] = useState<'online' | 'offline'>()
  const online = useQuery(['patrol-inbox-online', search], () => workflowApi.listPatrolBatches({ limit: 20, search }), { refetchInterval: 10000 })
  const offline = useQuery(['patrol-inbox-offline', search], () => offlinePatrolApi.list({ page_size: 20, search }), { refetchInterval: 10000 })
  const rows: Task[] = [
    ...(online.data?.items || []).map(row => ({ id: row.patrol_batch_id, name: row.target_label || row.ticket_id || '在线巡检', source: 'online' as const,
      time: row.created_at || '', status: row.aggregate_status || 'pending', scope: `${row.target_count ?? row.total} 台设备`,
      note: row.failed_count || row.skipped_count ? `${row.failed_count} 台失败 · ${row.skipped_count || 0} 台跳过` : '查看检查项结论和覆盖范围' })),
    ...(offline.data?.items || []).map(row => ({ id: row.id, name: row.display_name || row.name, source: 'offline' as const,
      time: row.created_at, status: row.status, scope: `${row.preview_totals?.device_count || 0} 台设备 · ${row.preview_totals?.file_count || 0} 份文件`,
      note: row.blocker_count ? `${row.blocker_count} 项待处理` : row.warning_count ? `${row.warning_count} 项质量提醒` : '历史分析 · 查看采集周期和结果' })),
  ].filter(row => !source || row.source === source).sort((a, b) => Date.parse(b.time) - Date.parse(a.time))
  const open = (task: Task) => {
    const next = new URLSearchParams(params)
    next.delete('offline_job_id'); next.delete('offline_job_tab'); next.delete('patrol_batch_id')
    next.set('patrol_view', task.source)
    if (task.source === 'offline') { next.set('offline_job_id', task.id); next.set('offline_job_tab', task.status === 'completed' ? 'result' : 'overview') }
    else next.set('patrol_batch_id', task.id)
    setParams(next)
  }
  return <section>
    <Typography.Title level={3}>巡检任务</Typography.Title>
    <Typography.Paragraph type="secondary">在线和离线各显示最近 20 项。执行完成后，请继续查看业务结论与未覆盖项目。</Typography.Paragraph>
    <Space wrap style={{ marginBottom: 16 }}>
      <Input.Search allowClear placeholder="搜索范围、任务或工单" onSearch={setSearch} />
      <Select aria-label="任务来源" allowClear placeholder="全部来源" value={source} onChange={setSource}
        style={{ width: 150 }} options={[{ value: 'online', label: '在线采集' }, { value: 'offline', label: '离线文件' }]} />
      <Button onClick={() => { void online.refetch(); void offline.refetch() }}>刷新</Button>
    </Space>
    {(online.isError || offline.isError) && <Alert type="error" message="部分任务加载失败" description="已加载任务仍可打开，请刷新重试。" />}
    {isMobile ? <List dataSource={rows} loading={online.isLoading || offline.isLoading}
      renderItem={row => <List.Item><Space direction="vertical" style={{ width: '100%' }}>
        <Typography.Text strong>{row.name}</Typography.Text>
        <Typography.Text type="secondary">{new Date(row.time).toLocaleString()}</Typography.Text>
        <span>{row.source === 'online' ? '在线采集' : '离线文件'} · {row.scope}</span>
        <span><Tag>{labels[row.status] || row.status}</Tag>{row.note}</span>
        <Button onClick={() => open(row)}>{row.status === 'completed' ? '查看结果' : '继续处理'}</Button>
      </Space></List.Item>} /> : <Table<Task> rowKey={row => `${row.source}:${row.id}`} dataSource={rows} loading={online.isLoading || offline.isLoading}
      pagination={false} size="middle" scroll={{ x: 800 }} columns={[
        { title: '任务', render: (_, row) => <Space direction="vertical" size={2}><Typography.Text strong>{row.name}</Typography.Text><Typography.Text type="secondary">{new Date(row.time).toLocaleString()}</Typography.Text></Space> },
        { title: '来源 / 范围', render: (_, row) => <Space direction="vertical" size={2}><span>{row.source === 'online' ? '在线采集' : '离线文件'}</span><span>{row.scope}</span></Space> },
        { title: '状态 / 待办', render: (_, row) => <Space direction="vertical" size={2}><Tag color={['failed', 'blocked'].includes(row.status) ? 'orange' : 'blue'}>{labels[row.status] || row.status}</Tag><span>{row.note}</span></Space> },
        { title: '操作', render: (_, row) => <Button onClick={() => open(row)}>{row.status === 'completed' ? '查看结果' : '继续处理'}</Button> },
      ]} />}
  </section>
}
