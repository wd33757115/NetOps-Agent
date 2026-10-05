// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react'
import {
  Col,
  Collapse,
  Row,
  Spin,
  Table,
  message,
} from 'antd'
import { useQuery } from 'react-query'
import { ReloadOutlined, MedicineBoxOutlined } from '@ant-design/icons'
import GrokShellLayout from '../components/Layout/GrokShellLayout'
import { GrokChip, GrokToolBtn, statusChipTone } from '../components/ui/GrokUi'
import { chatApi, skillApi, type DiagnosticsResponse, type ServiceCheck } from '../services/api'
import { useIsMobile } from '../hooks/useIsMobile'

const StatusPage: React.FC = () => {
  const isMobile = useIsMobile()
  const [diagEnabled, setDiagEnabled] = useState(false)

  const { data: health, isLoading: healthLoading, refetch: refetchHealth } = useQuery(
    'health',
    chatApi.getHealth
  )
  const { data: stats, isLoading: statsLoading } = useQuery('skill-stats', skillApi.getStats)

  const {
    data: diagnostics,
    isLoading: diagLoading,
    isFetching: diagFetching,
    refetch: runDiagnostics,
  } = useQuery<DiagnosticsResponse>(
    'diagnostics',
    chatApi.getDiagnostics,
    { enabled: diagEnabled, retry: false }
  )

  const handleDiagnostics = async () => {
    setDiagEnabled(true)
    try {
      await runDiagnostics()
      message.success('诊断完成')
    } catch {
      message.error('诊断请求失败，请确认 FastAPI 已启动')
    }
  }

  const toolbar = (
    <>
      <GrokToolBtn icon={<ReloadOutlined />} onClick={() => refetchHealth()}>
        刷新健康
      </GrokToolBtn>
      <GrokToolBtn
        primary
        icon={<MedicineBoxOutlined />}
        disabled={diagLoading || diagFetching}
        onClick={handleDiagnostics}
      >
        {diagLoading || diagFetching ? '诊断中…' : '一键诊断'}
      </GrokToolBtn>
    </>
  )

  const diagColumns = [
    {
      title: '服务',
      dataIndex: 'name',
      key: 'name',
      width: 160,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => <GrokChip tone={statusChipTone(s)}>{s.toUpperCase()}</GrokChip>,
    },
    {
      title: '说明',
      dataIndex: 'message',
      key: 'message',
    },
    {
      title: '延迟 (ms)',
      dataIndex: 'latency_ms',
      key: 'latency_ms',
      width: 100,
      render: (v: number | null) => (v != null ? v : '—'),
    },
  ]

  return (
    <GrokShellLayout
      title="Status"
      subtitle="Django BFF 与 FastAPI 后端运行概况"
      toolbar={toolbar}
    >
      {healthLoading || statsLoading ? (
        <div className="grok-page-loading">
          <Spin size="large" />
        </div>
      ) : (
        <>
          {diagnostics && (
            <div className="grok-notice">
              <strong>诊断结果</strong>
              <GrokChip tone={statusChipTone(diagnostics.status)}>{diagnostics.status}</GrokChip>
              <span>{diagnostics.summary}</span>
            </div>
          )}

          <div className="grok-stat-grid">
            <div className="grok-stat-card">
              <div className="grok-stat-label">Skill 总数</div>
              <div className="grok-stat-value">{stats?.total_skills || 0}</div>
            </div>
            <div className="grok-stat-card">
              <div className="grok-stat-label">已启用</div>
              <div className="grok-stat-value">{stats?.enabled_skills || 0}</div>
            </div>
            <div className="grok-stat-card">
              <div className="grok-stat-label">已禁用</div>
              <div className="grok-stat-value">{stats?.disabled_skills || 0}</div>
            </div>
            <div className="grok-stat-card">
              <div className="grok-stat-label">服务状态</div>
              <div className="grok-stat-value grok-stat-value-sm">
                <GrokChip tone={health?.status === 'healthy' ? 'ok' : 'warn'}>
                  {health?.status || 'unknown'}
                </GrokChip>
              </div>
            </div>
          </div>

          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}>
              <section className="grok-panel grok-panel-bordered">
                <h2 className="grok-panel-title">服务健康（BFF → FastAPI /health）</h2>
                <div className="grok-chip-row">
                  {health?.services &&
                    Object.entries(health.services).map(([k, v]) => (
                      <GrokChip key={k} tone={v ? 'ok' : 'warn'}>
                        {k}: {v ? 'OK' : 'FAIL'}
                      </GrokChip>
                    ))}
                </div>
                <Collapse
                  ghost
                  size="small"
                  items={[{
                    key: 'health-payload',
                    label: '技术详情：健康检查响应',
                    children: (
                      <pre className="grok-code-block">{JSON.stringify(health, null, 2)}</pre>
                    ),
                  }]}
                />
              </section>
            </Col>

            <Col xs={24} lg={12}>
              <section className="grok-panel grok-panel-bordered">
                <h2 className="grok-panel-title">Skill 分类分布</h2>
                <div className="grok-chip-row">
                  {Object.entries(stats?.categories || {}).map(([cat, count]) => (
                    <GrokChip key={cat}>
                      {cat}: {count as number}
                    </GrokChip>
                  ))}
                  {Object.keys(stats?.categories || {}).length === 0 && (
                    <span className="grok-muted">暂无分类数据</span>
                  )}
                </div>
              </section>
            </Col>

            <Col xs={24}>
              <section className="grok-panel grok-panel-bordered">
                <div className="grok-panel-head">
                  <h2 className="grok-panel-title">全栈诊断</h2>
                  <span className="grok-muted">
                    PostgreSQL · Redis · Celery · MinIO
                  </span>
                </div>
                {!diagnostics && !diagLoading && (
                  <p className="grok-muted">点击「一键诊断」检查各中间件与 Worker 状态</p>
                )}
                {(diagLoading || diagFetching) && !diagnostics && (
                  <div className="grok-page-loading is-compact">
                    <Spin />
                  </div>
                )}
                {diagnostics && (
                  <Table<ServiceCheck>
                    className="grok-table"
                    rowKey="id"
                    size="small"
                    pagination={false}
                    columns={diagColumns}
                    dataSource={diagnostics.checks}
                    scroll={{ x: isMobile ? 640 : undefined }}
                  />
                )}
              </section>
            </Col>
          </Row>
        </>
      )}
    </GrokShellLayout>
  )
}

export default StatusPage
