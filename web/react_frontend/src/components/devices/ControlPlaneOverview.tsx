import React from 'react'
import { Col, Row, Statistic } from 'antd'

export interface ControlPlaneOverviewMetrics {
  total: number
  active: number
  credentials: number
  connected: number
  connectionFailed: number
  patrolReady: number
  patrolBlocked: number
  activeRuns: number
}

interface ControlPlaneOverviewProps {
  metrics: ControlPlaneOverviewMetrics
}

const ControlPlaneOverview: React.FC<ControlPlaneOverviewProps> = ({ metrics }) => (
  <Row gutter={[12, 12]}>
    <Col xs={12} md={6} xl={3}><Statistic title="设备总数" value={metrics.total} /></Col>
    <Col xs={12} md={6} xl={3}><Statistic title="启用设备" value={metrics.active} /></Col>
    <Col xs={12} md={6} xl={3}><Statistic title="凭据已绑定" value={metrics.credentials} /></Col>
    <Col xs={12} md={6} xl={3}><Statistic title="连接正常" value={metrics.connected} /></Col>
    <Col xs={12} md={6} xl={3}><Statistic title="连接异常" value={metrics.connectionFailed} /></Col>
    <Col xs={12} md={6} xl={3}><Statistic title="巡检就绪" value={metrics.patrolReady} /></Col>
    <Col xs={12} md={6} xl={3}><Statistic title="巡检阻塞" value={metrics.patrolBlocked} /></Col>
    <Col xs={12} md={6} xl={3}><Statistic title="运行中任务" value={metrics.activeRuns} /></Col>
  </Row>
)

export default ControlPlaneOverview
