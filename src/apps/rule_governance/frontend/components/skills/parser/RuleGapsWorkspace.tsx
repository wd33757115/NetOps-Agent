import { useRuleTaskNavigate } from '@netops/hooks/useRuleTaskNavigate'
import { businessHref } from '@netops/apps/appRoutes'
import React, { useState } from 'react'
import { Alert, Button, Input, Segmented, Space, Table, Tabs, Tag, Typography } from 'antd'
import { ArrowRightOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'

import { parserApi, type ParserAssetGap } from '@netops/services/api'

type RuleGap = {
  observation_type?: string
  command?: string
  field?: string
  vendor?: string
  gap_kind?: string
  change_count?: number
  suggested_job_type?: string
  top_reason?: string
}

const RuleGapsWorkspace: React.FC = () => {
  const [days, setDays] = useState(90)
  const [assetPage, setAssetPage] = useState(1)
  const [assetPageSize, setAssetPageSize] = useState(20)
  const [assetSearch, setAssetSearch] = useState('')
  const navigate = useRuleTaskNavigate()
  const assetGapsQuery = useQuery(
    ['parser-asset-gaps', assetPage, assetPageSize, assetSearch],
    () => parserApi.assetGaps({
      status: 'open',
      search: assetSearch || undefined,
      limit: assetPageSize,
      offset: (assetPage - 1) * assetPageSize,
    }),
    { keepPreviousData: true, staleTime: 15_000 },
  )
  const changeGapsQuery = useQuery(
    ['parser-rule-gaps', days],
    () => parserApi.ruleGapsFromChanges({ days, limit: 200 }),
    { staleTime: 30_000 },
  )

  const gaps = (changeGapsQuery.data?.gaps || changeGapsQuery.data?.items || []) as RuleGap[]
  const byReason = changeGapsQuery.data?.by_reason || {}

  const openOfflineSource = (gap: RuleGap) => {
    const params = new URLSearchParams({
      workspace: 'production',
      subtab: 'source-jobs',
      action: 'rule-gap-source',
      command: gap.command || '',
      observation_type: gap.observation_type || '',
      vendor: gap.vendor || '',
      suggested_job_type: gap.suggested_job_type || '',
    })
    navigate(businessHref(`/skills/parser?${params.toString()}`))
  }

  const runtimeGaps = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="运行缺口只创建治理待办"
        description="生产巡检不会直接调用模型或创建规则。这里保留批次、检查项和命令上下文；工程师进入离线样本导入并确认数据后，才可启动 Parser 或规则资产生产。"
      />
      <Space wrap>
        <Input
          allowClear
          value={assetSearch}
          prefix={<SearchOutlined />}
          placeholder="搜索检查项、命令或阻断原因"
          style={{ width: 320, maxWidth: '100%' }}
          onChange={(event) => {
            setAssetSearch(event.target.value)
            setAssetPage(1)
          }}
        />
        <Button
          icon={<ReloadOutlined />}
          loading={assetGapsQuery.isFetching}
          onClick={() => assetGapsQuery.refetch()}
        >
          刷新
        </Button>
      </Space>
      <Table<ParserAssetGap>
        rowKey="id"
        loading={assetGapsQuery.isLoading}
        dataSource={assetGapsQuery.data?.items || []}
        size="small"
        pagination={{
          current: assetPage,
          pageSize: assetPageSize,
          total: assetGapsQuery.data?.total || 0,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 个运行缺口`,
          onChange: (page, pageSize) => {
            setAssetPage(pageSize === assetPageSize ? page : 1)
            setAssetPageSize(pageSize)
          },
        }}
        columns={[
          {
            title: '检查项',
            dataIndex: 'check_item_id',
            width: 220,
            render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
          },
          {
            title: '命令',
            dataIndex: 'canonical_command',
            width: 220,
            render: (value: string) => value || '-',
          },
          {
            title: '缺口',
            width: 230,
            render: (_: unknown, row) => (
              <Space direction="vertical" size={0}>
                <Typography.Text>{row.gap_type}</Typography.Text>
                <Typography.Text type="secondary">{row.reason_code}</Typography.Text>
              </Space>
            ),
          },
          {
            title: '影响',
            dataIndex: 'occurrence_count',
            width: 90,
            render: (value: number) => `${Number(value || 0)} 次`,
          },
          {
            title: '最近发现',
            dataIndex: 'last_seen_at',
            width: 170,
            render: (value: string) => value ? new Date(value).toLocaleString() : '-',
          },
          {
            title: '操作',
            key: 'action',
            width: 150,
            fixed: 'right',
            render: (_: unknown, row) => (
              <Button
                type="link"
                icon={<ArrowRightOutlined />}
                onClick={() => navigate(row.recommended_action.target_url)}
              >
                {row.recommended_action.label}
              </Button>
            ),
          },
        ]}
        locale={{ emptyText: '当前没有运行发现的资产缺口' }}
        scroll={{ x: 1080 }}
      />
    </Space>
  )

  const changeGaps = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="历史变化缺口仅用于定位规则样本"
        description="生产 Change 不会直接触发规则生成。选择一个缺口后，先导入明确的离线命令输出；平台完成来源校验和 Parser 解析后，才会进入 Contract、Policy 与 Event Rule 生产流程。"
      />
      <Space wrap>
        <Button icon={<ReloadOutlined />} onClick={() => changeGapsQuery.refetch()} loading={changeGapsQuery.isFetching}>
          刷新缺口
        </Button>
        <Typography.Text type="secondary">分析窗口</Typography.Text>
        <Segmented
          value={days}
          options={[
            { value: 30, label: '30 天' },
            { value: 90, label: '90 天' },
            { value: 180, label: '180 天' },
          ]}
          onChange={(value) => setDays(Number(value))}
        />
      </Space>
      <Space wrap>
        {Object.entries(byReason).slice(0, 8).map(([reason, count]) => (
          <Tag key={reason} color="orange">{reason}: {String(count)}</Tag>
        ))}
      </Space>
      <Table<RuleGap>
        rowKey={(row) => [
          row.observation_type || 'unknown',
          row.vendor || 'any',
          row.command || row.field || 'unknown',
          row.gap_kind || 'unknown',
          row.suggested_job_type || 'unknown',
        ].join(':')}
        loading={changeGapsQuery.isLoading}
        dataSource={gaps}
        size="small"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        columns={[
          {
            title: '观测类型',
            dataIndex: 'observation_type',
            width: 220,
            render: (value: string) => value || '-',
          },
          {
            title: '命令 / 字段',
            width: 220,
            render: (_: unknown, row: RuleGap) => row.command || row.field || '-',
          },
          {
            title: '缺口类型',
            dataIndex: 'gap_kind',
            width: 180,
            render: (value: string) => value || '-',
          },
          {
            title: '变化数',
            dataIndex: 'change_count',
            width: 90,
            render: (value: number) => Number(value || 0),
          },
          {
            title: '建议资产',
            dataIndex: 'suggested_job_type',
            width: 150,
          },
          {
            title: '主要原因',
            dataIndex: 'top_reason',
            render: (value: string) => value || '-',
          },
          {
            title: '操作',
            key: 'action',
            width: 150,
            fixed: 'right',
            render: (_: unknown, row: RuleGap) => (
              <Button
                type="link"
                icon={<ArrowRightOutlined />}
                onClick={() => openOfflineSource(row)}
              >
                导入离线样本
              </Button>
            ),
          },
        ]}
        locale={{ emptyText: '当前窗口没有可展示的变化缺口' }}
        scroll={{ x: 1050 }}
      />
    </Space>
  )

  return (
    <Tabs
      defaultActiveKey="runtime"
      items={[
        { key: 'runtime', label: '运行资产缺口', children: runtimeGaps },
        { key: 'changes', label: '变化事件缺口', children: changeGaps },
      ]}
    />
  )
}

export default RuleGapsWorkspace
