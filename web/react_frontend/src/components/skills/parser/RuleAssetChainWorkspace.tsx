import React from 'react'
import { Alert, Descriptions, Space, Tag, Typography } from 'antd'
import type { RuleAssetChainRow } from './parserWorkspaceModel'
import StandardTableShell from './StandardTableShell'

interface RuleAssetChainWorkspaceProps {
  rows: RuleAssetChainRow[]
  focusId?: string | null
}

const chainStatus = (status: RuleAssetChainRow['status']) => {
  if (status === 'ready') return { color: 'green', label: '已就绪' }
  if (status === 'blocked') return { color: 'red', label: '阻塞' }
  return { color: 'orange', label: '待补事件规则' }
}

const RuleAssetChainWorkspace: React.FC<RuleAssetChainWorkspaceProps> = ({ rows, focusId }) => (
  <Space direction="vertical" size={12} style={{ width: '100%' }}>
    <Alert
      type="info"
      showIcon
      message="按 Observation Type 检查 Contract → ChangePolicy → EventRule"
      description="Contract 与 Policy 是结构化状态和变化检测的必需资产；EventRule 仅统计明确声明 observation type 或实体作用域匹配的 active 规则。"
    />
      <StandardTableShell<RuleAssetChainRow>
        rowKey="observationType"
        rowClassName={(record) => (focusId && record.observationType === focusId ? 'ant-table-row-selected' : undefined)}
      pagination={{ pageSize: 20, showSizeChanger: false }}
      dataSource={rows}
      emptyText="暂无可识别的 Observation Type 资产"
      columns={[
        {
          title: 'Observation Type',
          dataIndex: 'observationType',
          width: 260,
          render: (value: string, row) => (
            <Space direction="vertical" size={0}>
              <Typography.Text strong>{value}</Typography.Text>
              <Typography.Text type="secondary">
                {row.entityTypes.join(', ') || '实体类型未声明'}
              </Typography.Text>
            </Space>
          ),
        },
        {
          title: '链路状态',
          dataIndex: 'status',
          width: 130,
          render: (value: RuleAssetChainRow['status']) => {
            const status = chainStatus(value)
            return <Tag color={status.color}>{status.label}</Tag>
          },
        },
        {
          title: 'ObservationContract',
          width: 220,
          render: (_: unknown, row) => row.activeContracts.length
            ? <Tag color="green">Active {row.activeContracts.length} / 共 {row.contracts.length}</Tag>
            : <Tag color="red">缺少 Active Contract</Tag>,
        },
        {
          title: 'ChangePolicy',
          width: 210,
          render: (_: unknown, row) => row.activePolicies.length
            ? <Tag color="green">Active {row.activePolicies.length} / 共 {row.policies.length}</Tag>
            : <Tag color="red">缺少 Active Policy</Tag>,
        },
        {
          title: 'EventRule',
          width: 210,
          render: (_: unknown, row) => row.activeRules.length
            ? <Tag color="green">Active {row.activeRules.length} / 匹配 {row.rules.length}</Tag>
            : <Tag color="orange">未证明存在匹配规则</Tag>,
        },
        {
          title: '来源任务',
          render: (_: unknown, row) => (
            <Space wrap>
              {Array.from(new Set([
                ...row.contracts.map((item) => item.source_job_id),
                ...row.policies.map((item) => item.source_job_id),
                ...row.rules.map((item) => item.source_job_id),
              ].filter(Boolean))).slice(0, 3).map((jobId) => (
                <Tag key={String(jobId)}>{String(jobId)}</Tag>
              ))}
            </Space>
          ),
        },
      ]}
      expandable={{
        expandedRowRender: (row) => (
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="Contract 版本">
              {row.contracts.map((item) => `${item.id} v${item.version} (${item.status})`).join('；') || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="Policy 版本">
              {row.policies.map((item) => `${item.version_id} v${item.version} (${item.version_status})`).join('；') || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="明确匹配的 EventRule">
              {row.rules.map((item) => `${item.rule_id} v${item.version} (${item.version_status})`).join('；') || '-'}
            </Descriptions.Item>
          </Descriptions>
        ),
      }}
      scroll={{ x: 1150 }}
    />
  </Space>
)

export default RuleAssetChainWorkspace
