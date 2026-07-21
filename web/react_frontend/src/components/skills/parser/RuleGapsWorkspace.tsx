import React, { useState } from 'react'
import { Alert, Button, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'
import { parserApi } from '../../../services/api'

const RuleGapsWorkspace: React.FC = () => {
  const [days, setDays] = useState(90)
  const query = useQuery(
    ['parser-rule-gaps', days],
    () => parserApi.ruleGapsFromChanges({ days, limit: 200 }),
    { staleTime: 30_000 },
  )

  const seed = useMutation(
    () => parserApi.seedRuleGapsFromChanges({ days, limit: 20 }),
    {
      onSuccess: (result: any) => {
        message.success(`已 seed ${result?.created_count ?? result?.seeded_count ?? (result?.created || []).length ?? 0} 个规则缺口任务`)
        query.refetch()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '批量 seed 失败'),
    },
  )

  const gaps = (query.data?.gaps || query.data?.items || []) as Array<Record<string, unknown>>
  const byReason = query.data?.by_reason || {}

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="历史 Change 未生成 Event 的缺口分析"
        description="按 no_event 原因聚合，可批量 seed Policy/Rule 生成 Job；激活后需人工审批，不会自动上线。"
      />
      <Space wrap>
        <Button icon={<ReloadOutlined />} onClick={() => query.refetch()} loading={query.isFetching}>
          刷新缺口
        </Button>
        <Button
          type="primary"
          icon={<ThunderboltOutlined />}
          loading={seed.isLoading}
          onClick={() => seed.mutate()}
        >
          批量 Seed Job
        </Button>
        <Typography.Text type="secondary">窗口 {days} 天</Typography.Text>
        {[30, 90, 180].map((value) => (
          <Button key={value} size="small" type={days === value ? 'primary' : 'default'} onClick={() => setDays(value)}>
            {value} 天
          </Button>
        ))}
      </Space>
      <Space wrap>
        {Object.entries(byReason).slice(0, 8).map(([reason, count]) => (
          <Tag key={reason} color="orange">{reason}: {String(count)}</Tag>
        ))}
      </Space>
      <Table
        rowKey={(row, index) => String(row.id || row.command || row.field || index)}
        loading={query.isLoading}
        dataSource={gaps}
        size="small"
        pagination={{ pageSize: 20 }}
        columns={[
          { title: '观测类型', dataIndex: 'observation_type', width: 220,
            render: (value: string) => value || '-' },
          { title: '命令/字段', width: 220,
            render: (_: unknown, row: Record<string, unknown>) =>
              String(row.command || row.field || '-') },
          { title: '缺口类型', dataIndex: 'gap_kind', width: 180,
            render: (value: string) => value || '-' },
          { title: '次数', dataIndex: 'change_count', width: 90,
            render: (value: number) => Number(value || 0) },
          { title: '建议 Job', dataIndex: 'suggested_job_type', width: 140 },
          { title: '主因', dataIndex: 'top_reason',
            render: (value: string) => value || '-' },
        ]}
        locale={{ emptyText: '当前窗口没有可展示的变化缺口' }}
        scroll={{ x: 780 }}
      />
    </Space>
  )
}

export default RuleGapsWorkspace
