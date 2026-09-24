import React, { useState } from 'react'
import { Alert, Button, Grid, Input, List, Select, Space, Table, Tag, Typography } from 'antd'
import { useQuery } from 'react-query'
import { FileSearchOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ruleAssetSourceApi, type CheckItemProductionGroup, type OfflineCommandAssetUnit } from '@netops/services/api'
import { businessHref } from '@netops/apps/appRoutes'

/** Aggregates existing persisted command units; no parallel job lifecycle. */
export default function RuleProductionTasks() {
  const navigate = useNavigate()
  const screens = Grid.useBreakpoint()
  const [params] = useSearchParams()
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [bucket, setBucket] = useState<string>()
  const query = useQuery(['rule-production-tasks', page, search, bucket], () =>
    ruleAssetSourceApi.checkItemProductionGroups({ page, page_size: 20, search, bucket }),
  { keepPreviousData: true, refetchInterval: 10000 })
  const open = (target: string) => {
    const url = new URL(businessHref(target), window.location.origin)
    const origin = params.get('return_to')
    if (origin?.startsWith('/apps/patrol?')) url.searchParams.set('return_to', origin)
    navigate(`${url.pathname}${url.search}`)
  }
  const openUnit = (unit: OfflineCommandAssetUnit) => {
    const next = new URLSearchParams({ workspace: 'production', subtab: 'asset-production-units',
      action: 'parser', offline_job_id: unit.offline_job_id, command: unit.canonical_command, unit_id: unit.id })
    open(`/skills/parser?${next}`)
  }
  const taskName = (row: CheckItemProductionGroup) => row.classification_required && !row.preproduction_associated
    ? row.implementations[0]?.canonical_command || row.display_name : row.display_name || row.name
  const taskAction = (row: CheckItemProductionGroup) => row.next_action?.target_url
    ? <Button onClick={() => open(row.next_action!.target_url)}>{row.next_action.label}</Button>
    : row.implementations[0] && <Button onClick={() => openUnit(row.implementations[0])}>{row.governance_bucket === 'published' ? '查看验证' : '继续制作'}</Button>
  return <section className="rule-production-tasks">
    <header className="rule-task-heading"><div><Typography.Title level={2}>制作任务</Typography.Title>
      <Typography.Text type="secondary">从已有样本继续，校对检查结果后验证发布。</Typography.Text></div>
      <Space wrap>
        <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => open('/skills/parser?workspace=production&subtab=source-jobs&action=new-adapter')}>适配新设备</Button>
        <Button onClick={() => open('/skills/parser?workspace=production&subtab=catalog-management')}>修改检查标准</Button>
      </Space>
    </header>
    <div className="rules-task-filters">
      <Input.Search aria-label="搜索制作任务" allowClear placeholder="检查项、设备或命令" onSearch={(value) => { setSearch(value); setPage(1) }} />
      <Select aria-label="制作任务状态" allowClear placeholder="全部状态" value={bucket} style={{ width: 170 }}
        onChange={(value) => { setBucket(value); setPage(1) }} options={[
          { value: 'action_required', label: '待处理' }, { value: 'in_progress', label: '制作中' },
          { value: 'awaiting_approval', label: '待审批' }, { value: 'published', label: '已发布' },
          { value: 'exception', label: '需要修复' },
        ]} />
      <Button icon={<ReloadOutlined aria-hidden="true" />} onClick={() => query.refetch()}>刷新</Button>
      <span className="rules-task-scope">{query.isError ? '列表暂不可用' : query.isLoading ? '正在加载任务' : `共 ${query.data?.total || 0} 个检查项制作任务`}</span>
    </div>
    {query.isError && <Alert type="error" message="制作任务加载失败" action={<Button onClick={() => query.refetch()}>重试</Button>} />}
    {!screens.md && (query.data?.items.length || 0) > 0 ? <List className="rules-task-mobile" loading={query.isLoading}
      dataSource={query.data?.items || []} pagination={{current:page,pageSize:20,total:query.data?.total || 0,onChange:setPage,showSizeChanger:false}}
      renderItem={(row: CheckItemProductionGroup) => <List.Item><article>
        <div className="rules-mobile-task-heading"><strong>{taskName(row)}</strong><Tag>{row.governance_bucket === 'published' ? '已发布' : row.current_stage_label}</Tag></div>
        <p>{row.vendors.join(' / ')} · {row.command_count} 条命令 · {row.sample_count} 份输出</p>
        <p>{row.next_action?.description || '查看命令与验证结果，继续制作。'}</p>
        <div className="rules-mobile-task-actions">{taskAction(row)}<details><summary>命令明细</summary>{row.implementations.map(unit => <div key={unit.id}><code>{unit.canonical_command}</code><Button type="link" onClick={() => openUnit(unit)}>打开制作任务</Button></div>)}</details></div>
      </article></List.Item>} /> : <Table<CheckItemProductionGroup> locale={{ emptyText: <div className="rules-task-empty"><FileSearchOutlined aria-hidden="true" /><strong>{search || bucket ? '没有符合条件的制作任务' : '从一份命令样本开始'}</strong><p>{search || bucket ? '调整关键词或状态筛选，查看其他任务。' : '导入设备命令输出，关联检查项，再继续规则生成和验证。'}</p>{!search && !bucket && <Button onClick={() => open('/skills/parser?workspace=production&subtab=source-jobs')}>导入 CLI 样本</Button>}</div> }} rowKey="id" size="middle" loading={query.isLoading}
      dataSource={query.data?.items || []} scroll={{ x: 850 }}
      pagination={{ current: page, pageSize: 20, total: query.data?.total || 0, onChange: setPage, showSizeChanger: false }}
      columns={[
        { title: '检查内容', render: (_, row) => <Space direction="vertical" size={2}>
          <Typography.Text strong>{taskName(row)}</Typography.Text>
          <Typography.Text type="secondary">{row.vendors.join(' / ')} · {row.command_count} 条命令</Typography.Text>
        </Space> },
        { title: '当前阶段', render: (_, row) => <Space direction="vertical" size={2}>
          <Tag color={row.blocker_count ? 'orange' : row.governance_bucket === 'published' ? 'green' : 'blue'}>{row.governance_bucket === 'published' ? '已发布' : row.current_stage_label}</Tag>
          <span>{row.next_action?.description || (row.classification_required ? '确认样本对应的检查项' : '展开命令，查看验证结果与发布状态')}</span>
        </Space> },
        { title: '验证样本', render: (_, row) => `${row.sample_count} 份输出 · ${row.evidence_device_count} 台设备` },
        { title: '操作', render: (_, row) => taskAction(row) },
      ]}
      expandable={{ columnTitle: '明细', columnWidth: 64, expandedRowRender: (group) => <Table<OfflineCommandAssetUnit> rowKey="id" size="small" pagination={false}
        dataSource={group.implementations} columns={[
          { title: '厂商 / 型号', render: (_, unit) => `${unit.vendor} / ${unit.model || unit.family || '通用'}` },
          { title: '命令', dataIndex: 'canonical_command' },
          { title: '阶段', dataIndex: 'current_stage_label' },
          { title: '操作', render: (_, unit) => <Button onClick={() => openUnit(unit)}>打开制作任务</Button> },
        ]} /> }} />}
  </section>
}
