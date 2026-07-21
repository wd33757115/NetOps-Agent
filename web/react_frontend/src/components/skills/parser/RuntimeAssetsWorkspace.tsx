import React from 'react'
import { Alert, Col, Descriptions, Row, Space, Spin, Statistic, Tag, Typography } from 'antd'
import type { ParserRuntimeRuleAssetBundle } from '../../../services/api'
import StandardTableShell from './StandardTableShell'

interface RuntimeAssetsWorkspaceProps {
  assets?: ParserRuntimeRuleAssetBundle
  loading: boolean
  activeCounts: {
    contracts: number
    policies: number
    eventRules: number
  }
  schemas: Array<[string, string]>
  observationTypes: string[]
  isMobile: boolean
  focusId?: string | null
}

const RuntimeAssetsWorkspace: React.FC<RuntimeAssetsWorkspaceProps> = ({
  assets,
  loading,
  activeCounts,
  schemas,
  observationTypes,
  isMobile,
  focusId,
}) => {
  const missingContracts = assets?.missing_observation_contracts || []
  const missingPolicies = assets?.missing_change_policies || []
  const missingEventRules = assets?.missing_event_rules || []
  const effectiveObservationTypes = assets?.observation_types || observationTypes
  const phaseRows = [
    {
      key: 'collection',
      phase: '1. 采集结构化',
      ready: Boolean(assets?.collection_ready),
      dependency: 'Observation Contract',
      gap: missingContracts.length
        ? `缺 ${missingContracts.length} 个数据契约`
        : assets?.collection_ready ? '数据契约完整' : '没有可用的 active 数据契约',
    },
    {
      key: 'analysis',
      phase: '2. 变化分析',
      ready: Boolean(assets?.analysis_ready),
      dependency: 'Change Policy',
      gap: missingPolicies.length
        ? `缺 ${missingPolicies.length} 个变化策略`
        : assets?.analysis_ready ? '变化策略完整' : '没有可分析的 observation type',
    },
    {
      key: 'event',
      phase: '3. 事件评估',
      ready: Boolean(assets?.event_evaluation_ready),
      dependency: 'Event Rule',
      gap: missingEventRules.length
        ? `缺 ${missingEventRules.length} 个事件规则覆盖`
        : assets?.event_evaluation_ready ? '事件规则完整' : '没有可用的 active 事件规则',
    },
  ]

  const overall = assets?.event_evaluation_ready
    ? {
      type: 'success' as const,
      message: '巡检规则运行时全链路已就绪',
      description: '结构化、变化分析和事件评估均有 active 资产覆盖。生产运行时不会调用 LLM。',
    }
    : assets?.analysis_ready
      ? {
        type: 'warning' as const,
        message: '变化分析可用，但事件评估尚未就绪',
        description: '系统可以形成 State 与 Change，但部分变化不会生成 Event；不能将其解释为“未发现事件”。',
      }
      : assets?.collection_ready
        ? {
          type: 'warning' as const,
          message: '结构化采集可用，但变化分析尚未就绪',
          description: '系统可以形成 Observation，但 Change Policy 覆盖不足，变化与事件结论均不完整。',
        }
        : {
          type: 'error' as const,
          message: '结构化采集未就绪',
          description: '缺少 active Observation Contract，后续 State、Change 与 Event 结论均不可视为完整。',
        }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert type={overall.type} showIcon message={overall.message} description={overall.description} />
      <Spin spinning={loading}>
        <StandardTableShell
          rowKey="key"
          pagination={false}
          dataSource={phaseRows}
          columns={[
            {
              title: '运行阶段',
              dataIndex: 'phase',
              width: 180,
              render: (value: string, row: typeof phaseRows[number]) => (
                <Space>
                  <Tag color={row.ready ? 'green' : 'red'}>{row.ready ? '就绪' : '阻断'}</Tag>
                  <Typography.Text strong>{value}</Typography.Text>
                </Space>
              ),
            },
            { title: '依赖资产', dataIndex: 'dependency', width: 200 },
            { title: '检查结果', dataIndex: 'gap' },
          ]}
          emptyText="运行时健康信息尚未加载"
          scroll={{ x: 680 }}
        />
        <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
          <Col xs={12} md={6}>
            <Statistic title="Active 数据契约" value={assets?.counts?.observation_contracts ?? activeCounts.contracts} />
          </Col>
          <Col xs={12} md={6}>
            <Statistic title="Active 变化策略" value={assets?.counts?.change_policies ?? activeCounts.policies} />
          </Col>
          <Col xs={12} md={6}>
            <Statistic title="Active 事件规则" value={assets?.counts?.event_rules ?? activeCounts.eventRules} />
          </Col>
          <Col xs={12} md={6}>
            <Statistic title="未覆盖项" value={missingContracts.length + missingPolicies.length + missingEventRules.length} />
          </Col>
        </Row>
      </Spin>
      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="检查的数据契约">
          {schemas.length ? schemas.map((item) => item.join(':')).join(' | ') : '暂无 active 数据契约'}
        </Descriptions.Item>
        <Descriptions.Item label="检查的观察类型">
          {effectiveObservationTypes.length ? effectiveObservationTypes.join(' | ') : '暂无可推导 observation type'}
        </Descriptions.Item>
        <Descriptions.Item label="运行时摘要">
          <Typography.Text copyable>{assets?.digest || '-'}</Typography.Text>
        </Descriptions.Item>
      </Descriptions>
      <Row gutter={[12, 12]}>
        <Col span={isMobile ? 24 : 8}>
          <Typography.Title level={5}>缺失数据契约</Typography.Title>
          <StandardTableShell
            rowKey="id"
            rowClassName={(row) => (focusId && String(row.id) === focusId ? 'ant-table-row-selected' : undefined)}
            pagination={false}
            dataSource={missingContracts.map((id) => ({ id }))}
            columns={[{ title: 'Schema', dataIndex: 'id' }]}
            emptyText="无缺失数据契约"
          />
        </Col>
        <Col span={isMobile ? 24 : 8}>
          <Typography.Title level={5}>缺失变化策略</Typography.Title>
          <StandardTableShell
            rowKey="id"
            rowClassName={(row) => (focusId && String(row.id) === focusId ? 'ant-table-row-selected' : undefined)}
            pagination={false}
            dataSource={missingPolicies.map((id) => ({ id }))}
            columns={[{ title: 'Observation Type', dataIndex: 'id' }]}
            emptyText="无缺失变化策略"
          />
        </Col>
        <Col span={isMobile ? 24 : 8}>
          <Typography.Title level={5}>缺失事件规则</Typography.Title>
          <StandardTableShell
            rowKey="id"
            rowClassName={(row) => (focusId && String(row.id) === focusId ? 'ant-table-row-selected' : undefined)}
            pagination={false}
            dataSource={missingEventRules.map((id) => ({ id }))}
            columns={[{ title: 'Observation Type', dataIndex: 'id' }]}
            emptyText="无缺失事件规则"
          />
        </Col>
      </Row>
    </Space>
  )
}

export default RuntimeAssetsWorkspace
