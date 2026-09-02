import React from 'react'
import { Alert, Button, Descriptions, Space, Tag, Tooltip, Typography } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import type { ParserRuleGenerationJob } from '../../../services/api'
import { asRecord, type RuleAssetChainRow } from './parserWorkspaceModel'
import StandardTableShell from './StandardTableShell'

interface RuleAssetChainWorkspaceProps {
  rows: RuleAssetChainRow[]
  focusId?: string | null
  seedJobs?: ParserRuleGenerationJob[]
  onGenerate?: (job: ParserRuleGenerationJob) => void
}

const chainStatus = (status: RuleAssetChainRow['status']) => {
  if (status === 'ready') return { color: 'green', label: '已就绪' }
  if (status === 'blocked') return { color: 'red', label: '阻塞' }
  return { color: 'orange', label: '待补事件规则' }
}

const seedObservationType = (job: ParserRuleGenerationJob) => {
  const defaults = asRecord(asRecord(job.payload_json)?.workflow_context_defaults)
  return typeof defaults?.observation_type === 'string' ? defaults.observation_type : ''
}

const RuleAssetChainWorkspace: React.FC<RuleAssetChainWorkspaceProps> = ({
  rows,
  focusId,
  seedJobs = [],
  onGenerate,
}) => (
  <Space direction="vertical" size={12} style={{ width: '100%' }}>
    <Alert
      type="info"
      showIcon
      message="按标准语义 B 检查 ChangePolicy → EventRule"
      description="标准字段和值域来自 CheckItem；Command Adapter 负责 A→B。ObservationContract 只作为历史资产展示，不再是直接投影运行链路或规则生成的前置条件。"
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
          title: '标准语义来源',
          width: 220,
          render: (_: unknown, row) => (
            <Space wrap>
              <Tag color="blue">CheckItem B</Tag>
              {row.activeContracts.length > 0 && <Tag>历史 Contract {row.activeContracts.length}</Tag>}
            </Space>
          ),
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
                ...row.policies.map((item) => item.source_job_id),
                ...row.rules.map((item) => item.source_job_id),
              ].filter(Boolean))).slice(0, 3).map((jobId) => (
                <Tag key={String(jobId)}>{String(jobId)}</Tag>
              ))}
            </Space>
          ),
        },
        {
          title: '下一步',
          width: 150,
          render: (_: unknown, row) => {
            if (row.status === 'ready') return <Typography.Text type="secondary">持续观测</Typography.Text>
            const seed = seedJobs.find((job) => seedObservationType(job) === row.observationType)
            return (
              <Tooltip title={seed ? '使用脱敏数据生成完整候选资产包' : '需要先激活对应 Parser 模板并形成生成种子'}>
                <Button
                  size="small"
                  icon={<PlayCircleOutlined />}
                  disabled={!seed || !onGenerate}
                  onClick={() => seed && onGenerate?.(seed)}
                >
                  生成或补齐
                </Button>
              </Tooltip>
            )
          },
        },
      ]}
      expandable={{
        expandedRowRender: (row) => (
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="标准语义">
              由 CheckItem 版本提供；与样本和 Parser 解耦
            </Descriptions.Item>
            <Descriptions.Item label="历史 Contract（仅审计）">
              {row.contracts.map((item) => `${item.id} v${item.version} (${item.status})`).join('；') || '无'}
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
      scroll={{ x: 1300 }}
    />
  </Space>
)

export default RuleAssetChainWorkspace
