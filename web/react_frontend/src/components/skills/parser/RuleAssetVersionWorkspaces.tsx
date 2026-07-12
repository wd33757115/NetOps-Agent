import React from 'react'
import { Alert, Col, Row, Space, Tag, Typography } from 'antd'
import type { ParserChangePolicy, ParserObservationContract } from '../../../services/api'
import { hasFocusMatched } from './parserControlFocus'
import { asRecord } from './parserWorkspaceModel'
import StandardTableShell from './StandardTableShell'

const codePanelStyle: React.CSSProperties = {
  background: '#f5f5f5',
  border: '1px solid #e5e7eb',
  borderRadius: 6,
  fontFamily: 'Consolas, Monaco, monospace',
  margin: 0,
  overflow: 'auto',
  padding: 16,
  whiteSpace: 'pre-wrap',
}

const statusColor = (status: string) => {
  if (status === 'active') return 'green'
  if (status === 'rejected' || status === 'validation_failed') return 'red'
  if (status === 'deprecated') return 'default'
  return 'blue'
}

const statusLabel = (status?: string) => ({
  active: '已激活',
  deprecated: '已废弃',
  draft: '草稿',
  pending_approval: '待审批',
  rejected: '已拒绝',
  validating: '验证中',
  validation_failed: '验证失败',
}[status || ''] || status || '-')

const shortDigest = (value?: string | null) => value ? value.slice(0, 12) : '-'

interface ObservationContractsWorkspaceProps {
  items: ParserObservationContract[]
  loading: boolean
  focusId?: string | null
}

export const ObservationContractsWorkspace: React.FC<ObservationContractsWorkspaceProps> = ({
  items,
  loading,
  focusId,
}) => (
  <Space direction="vertical" size={12} style={{ width: '100%' }}>
    <Alert
      type="info"
      showIcon
      message="ObservationContract 固化结构化字段到标准观测语义的映射；生产状态链路只消费已治理的契约版本。"
    />
      <StandardTableShell<ParserObservationContract>
        rowKey="id"
        rowClassName={(record) => {
          if (!focusId) return undefined
          return hasFocusMatched(
            { kind: 'observation-contract', id: focusId, workspace: 'rule-assets', subWorkspace: 'observation-contracts' },
            {
              id: record.id,
              sourceJobId: record.source_job_id,
            },
          )
            ? 'ant-table-row-selected'
            : undefined
        }}
        loading={loading}
        dataSource={items}
        columns={[
        { title: 'Schema', dataIndex: 'schema_id', width: 280,
          render: (value: string, row) => (
            <Space direction="vertical" size={0}>
              <Typography.Text copyable>{value}</Typography.Text>
              <Typography.Text type="secondary">{row.source_type} v{row.version}</Typography.Text>
            </Space>
          ) },
        { title: '状态', dataIndex: 'status', width: 110,
          render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
        { title: '实体类型', dataIndex: 'entity_type', width: 160 },
        { title: '主键', dataIndex: 'primary_keys_json', width: 180,
          render: (value: string[] | null) => (value || []).join(', ') || '-' },
        { title: '必填字段', dataIndex: 'required_fields_json', width: 240,
          render: (value: string[] | null) => (value || []).join(', ') || '-' },
        { title: '语义字段', dataIndex: 'state_semantics_json', width: 220,
          render: (value: Record<string, unknown> | null) => Object.keys(value || {}).join(', ') || '-' },
        { title: '允许空结果', dataIndex: 'allow_empty', width: 110,
          render: (value: boolean) => <Tag color={value ? 'blue' : 'default'}>{value ? '是' : '否'}</Tag> },
        { title: '来源 Job', dataIndex: 'source_job_id', width: 220,
          render: (value: string | null) => value || '-' },
        { title: 'Digest', dataIndex: 'contract_sha256', width: 120,
          render: (value: string | null) => <Typography.Text copyable={Boolean(value)}>{shortDigest(value)}</Typography.Text> },
        { title: '更新时间', dataIndex: 'updated_at', width: 190 },
      ]}
      expandable={{
        expandedRowRender: (row) => (
          <pre style={{ ...codePanelStyle, maxHeight: 420 }}>
            {JSON.stringify({
              primary_keys: row.primary_keys_json,
              required_fields: row.required_fields_json,
              optional_fields: row.optional_fields_json,
              validators: row.validators_json,
              ignore_fields: row.ignore_fields_json,
              allow_empty: row.allow_empty,
              empty_patterns: row.empty_patterns_json,
              state_semantics: row.state_semantics_json,
              approval: row.approval_json,
            }, null, 2)}
          </pre>
        ),
      }}
      pagination={{ pageSize: 20, showSizeChanger: false }}
      emptyText="暂无 ObservationContract"
      scroll={{ x: 1500 }}
    />
  </Space>
)

interface ChangePoliciesWorkspaceProps {
  items: ParserChangePolicy[]
  loading: boolean
  isMobile: boolean
  focusId?: string | null
}

export const ChangePoliciesWorkspace: React.FC<ChangePoliciesWorkspaceProps> = ({
  items,
  loading,
  isMobile,
  focusId,
}) => (
  <Space direction="vertical" size={12} style={{ width: '100%' }}>
    <Alert
      type="info"
      showIcon
      message="ChangePolicy 定义状态、数值、计数器和原文哈希如何比较；Change Detector 运行时只读取 active 策略。"
    />
      <StandardTableShell<ParserChangePolicy>
        rowKey="version_id"
        rowClassName={(record) => {
          if (!focusId) return undefined
          return hasFocusMatched(
            { kind: 'change-policy', id: focusId, workspace: 'rule-assets', subWorkspace: 'change-policies' },
            {
              id: record.id,
              versionId: record.version_id,
              policyId: record.policy_id,
              sourceJobId: record.source_job_id,
            },
          )
            ? 'ant-table-row-selected'
            : undefined
        }}
        loading={loading}
        dataSource={items}
        columns={[
        { title: '策略', dataIndex: 'policy_name', width: 240,
          render: (value: string, row) => (
            <Space direction="vertical" size={0}>
              <Typography.Text strong>{value}</Typography.Text>
              <Typography.Text type="secondary">v{row.version} / {row.version_id}</Typography.Text>
            </Space>
          ) },
        { title: '状态', dataIndex: 'version_status', width: 110,
          render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
        { title: 'Observation Type', dataIndex: 'observation_type', width: 240 },
        { title: '语义类型', dataIndex: 'semantic_type', width: 120 },
        { title: '比较模式', dataIndex: 'compare_mode', width: 180 },
        { title: '阈值/策略', dataIndex: 'policy', width: 260,
          render: (value: Record<string, unknown>) => {
            const thresholds = asRecord(value?.thresholds)
            const parts = [
              thresholds ? `thresholds=${JSON.stringify(thresholds)}` : null,
              value?.deadband !== undefined ? `deadband=${String(value.deadband)}` : null,
              value?.recovery_threshold !== undefined ? `recovery=${String(value.recovery_threshold)}` : null,
            ].filter(Boolean)
            return parts.length ? <Typography.Text code>{parts.join(' | ')}</Typography.Text> : '-'
          } },
        { title: '来源 Job', dataIndex: 'source_job_id', width: 220,
          render: (value: string | null) => value || '-' },
        { title: 'Digest', dataIndex: 'digest', width: 120,
          render: (value: string | null) => <Typography.Text copyable={Boolean(value)}>{shortDigest(value)}</Typography.Text> },
        { title: '激活时间', dataIndex: 'activated_at', width: 190,
          render: (value: string | null) => value || '-' },
      ]}
      expandable={{
        expandedRowRender: (row) => (
          <Row gutter={[12, 12]}>
            <Col span={isMobile ? 24 : 12}>
              <Typography.Title level={5}>策略内容</Typography.Title>
              <pre style={{ ...codePanelStyle, maxHeight: 360 }}>{JSON.stringify(row.policy || {}, null, 2)}</pre>
            </Col>
            <Col span={isMobile ? 24 : 12}>
              <Typography.Title level={5}>验证报告</Typography.Title>
              <pre style={{ ...codePanelStyle, maxHeight: 360 }}>{JSON.stringify(row.validation_report || {}, null, 2)}</pre>
            </Col>
          </Row>
        ),
      }}
      pagination={{ pageSize: 20, showSizeChanger: false }}
      emptyText="暂无 ChangePolicy"
      scroll={{ x: 1500 }}
    />
  </Space>
)
