import React from 'react'
import { Alert, Col, Descriptions, Row, Space, Spin, Statistic, Typography } from 'antd'
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

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type={assets?.ready ? 'success' : 'warning'}
        showIcon
        message={assets?.ready ? '运行时规则资产已就绪' : '运行时规则资产存在缺口'}
        description="生产巡检、状态归一化、变化检测和事件构建只应读取 active 资产；这里展示当前 active 数据契约、变化策略、事件规则及缺失项。"
      />
      <Spin spinning={loading}>
        <Row gutter={[12, 12]}>
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
            <Statistic title="缺口数量" value={missingContracts.length + missingPolicies.length} />
          </Col>
        </Row>
      </Spin>
      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="检查的数据契约">
          {schemas.length ? schemas.map((item) => item.join(':')).join(' | ') : '暂无 active 数据契约'}
        </Descriptions.Item>
        <Descriptions.Item label="检查的观察类型">
          {observationTypes.length ? observationTypes.join(' | ') : '暂无可推导观察类型'}
        </Descriptions.Item>
      </Descriptions>
      <Row gutter={[12, 12]}>
        <Col span={isMobile ? 24 : 12}>
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
        <Col span={isMobile ? 24 : 12}>
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
      </Row>
    </Space>
  )
}

export default RuntimeAssetsWorkspace
