import React from 'react'
import { Alert, Button, Input, List, Pagination, Select, Space, Tag, Typography } from 'antd'
import { EyeOutlined } from '@ant-design/icons'

import type { ParserWorkItem } from '../../../services/api'
import StandardTableShell from './StandardTableShell'
import { statusColor, statusLabel } from './parserUi'

const taskTypeLabel: Record<ParserWorkItem['task_type'], string> = {
  parser: 'Parser 生成',
  backfill: '历史回填',
  rule_generation: '规则资产',
}

const waitAge = (seconds: number) => {
  if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))} 分钟`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`
  return `${Math.floor(seconds / 86400)} 天`
}

const priorityTag = (priority: ParserWorkItem['priority']) => (
  <Tag color={priority === 'high' ? 'red' : priority === 'normal' ? 'blue' : 'default'}>
    {priority === 'high' ? '高' : priority === 'normal' ? '普通' : '低'}
  </Tag>
)

interface Props {
  isMobile: boolean
  items: ParserWorkItem[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  search: string
  taskType?: string
  status?: string
  onSearchChange: (value: string) => void
  onTaskTypeChange: (value?: string) => void
  onStatusChange: (value?: string) => void
  onPageChange: (page: number, pageSize: number) => void
  onOpen: (item: ParserWorkItem) => void
}

const ParserWorkQueueWorkspace: React.FC<Props> = ({
  isMobile,
  items,
  loading,
  total,
  page,
  pageSize,
  search,
  taskType,
  status,
  onSearchChange,
  onTaskTypeChange,
  onStatusChange,
  onPageChange,
  onOpen,
}) => {
  const columns = [
    { title: '优先级', dataIndex: 'priority', width: 90,
      render: (value: ParserWorkItem['priority']) => priorityTag(value) },
    { title: '影响面', dataIndex: 'production_impact', width: 100,
      render: (value: number | undefined) => (
        <Typography.Text type={(value || 0) > 0 ? 'danger' : 'secondary'}>
          {value ?? 0}
        </Typography.Text>
      ) },
    { title: '任务', dataIndex: 'title', width: 340,
      render: (value: string, row: ParserWorkItem) => (
        <Space direction="vertical" size={0}>
          <Space size={4}>
            <Typography.Text strong ellipsis style={{ maxWidth: 280 }}>{value}</Typography.Text>
            <Button type="text" size="small" icon={<EyeOutlined />} title="查看任务" onClick={() => onOpen(row)} />
          </Space>
          <Typography.Text type="secondary">{taskTypeLabel[row.task_type]} · {row.id}</Typography.Text>
        </Space>
      ) },
    { title: '状态', dataIndex: 'status', width: 130,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
    { title: '影响范围', dataIndex: 'scope', width: 130 },
    { title: '阻断原因', dataIndex: 'blocker', width: 220, ellipsis: true,
      render: (value: string | null) => value || '-' },
    { title: '下一步', dataIndex: 'next_action', width: 190 },
    { title: '等待时长', dataIndex: 'age_seconds', width: 100,
      render: (value: number) => waitAge(value) },
  ]

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="按生产影响面、风险和等待时长统一排列 Parser、历史回填与规则资产任务"
        description="影响面 = 绑定设备数 × 近 30 天 template_missing 次数。高优先级包含失败、验证未通过和待审批任务；操作会进入对应领域详情，不在待办列表中绕过治理流程。"
      />
      <Space wrap>
        <Input.Search
          allowClear
          placeholder="搜索任务、厂商、命令或资产"
          style={{ width: 300, maxWidth: '100%' }}
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
        />
        <Select
          allowClear
          placeholder="任务类型"
          style={{ width: 160 }}
          value={taskType}
          onChange={onTaskTypeChange}
          options={[
            { value: 'parser', label: 'Parser 生成' },
            { value: 'backfill', label: '历史回填' },
            { value: 'rule_generation', label: '规则资产' },
          ]}
        />
        <Select
          allowClear
          placeholder="任务状态"
          style={{ width: 170 }}
          value={status}
          onChange={onStatusChange}
          options={[
            'pending', 'collecting', 'running', 'validating', 'draft',
            'pending_approval', 'validation_failed', 'failed',
          ].map((value) => ({ value, label: statusLabel(value) }))}
        />
      </Space>
      {isMobile ? (
        <>
          <List<ParserWorkItem>
            loading={loading}
            dataSource={items}
            locale={{ emptyText: '当前没有待处理任务' }}
            renderItem={(row) => (
              <List.Item key={`${row.task_type}:${row.id}`}>
                <Space direction="vertical" size={6} style={{ width: '100%' }}>
                  <Space wrap>
                    {priorityTag(row.priority)}
                    <Tag color={statusColor(row.status)}>{statusLabel(row.status)}</Tag>
                    <Typography.Text type="secondary">{waitAge(row.age_seconds)}</Typography.Text>
                  </Space>
                  <Typography.Text strong>{row.title}</Typography.Text>
                  <Typography.Text type="secondary">
                    {taskTypeLabel[row.task_type]} · {row.scope}
                  </Typography.Text>
                  {row.blocker && <Alert type="warning" showIcon message={row.blocker} />}
                  <Typography.Text>下一步：{row.next_action}</Typography.Text>
                  <Button icon={<EyeOutlined />} block onClick={() => onOpen(row)}>处理任务</Button>
                </Space>
              </List.Item>
            )}
          />
          <Pagination
            simple
            current={page}
            pageSize={pageSize}
            total={total}
            onChange={onPageChange}
          />
        </>
      ) : (
        <StandardTableShell<ParserWorkItem>
          rowKey={(row) => `${row.task_type}:${row.id}`}
          loading={loading}
          dataSource={items}
          columns={columns}
          emptyText="当前没有待处理任务"
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (value) => `共 ${value} 条`,
          }}
          onChange={(pagination) => onPageChange(
            pagination.current || 1,
            pagination.pageSize || 20,
          )}
          scroll={{ x: 1090 }}
        />
      )}
    </Space>
  )
}

export default ParserWorkQueueWorkspace
