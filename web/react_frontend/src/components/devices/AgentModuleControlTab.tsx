import React, { useState } from 'react'
import { Alert, Button, Descriptions, Form, Input, Modal, Select, Space, Table, Tag, Typography, message } from 'antd'
import { LinkOutlined, PauseCircleOutlined, PlayCircleOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'
import {
  type AgentModuleDefinition,
  agentModuleDefinitions,
  agentModuleStatusMeta,
} from './agentModules'
import { deviceApi, type DeviceAgentModuleHealth } from '../../services/api'

type AgentModuleRow = AgentModuleDefinition & {
  runtime?: DeviceAgentModuleHealth
}

type AgentModuleControlStatus = 'enabled' | 'disabled' | 'maintenance'

type ControlModalState = {
  moduleId: string
  title: string
  status: AgentModuleControlStatus
} | null

const runtimeStatusMeta: Record<string, { color: string; text: string }> = {
  healthy: { color: 'green', text: '健康' },
  degraded: { color: 'orange', text: '降级' },
  blocked: { color: 'red', text: '阻塞' },
  disabled: { color: 'default', text: '已停用' },
  maintenance: { color: 'blue', text: '维护中' },
}

const controlStatusMeta: Record<string, { color: string; text: string }> = {
  enabled: { color: 'green', text: '已启用' },
  disabled: { color: 'default', text: '已停用' },
  maintenance: { color: 'blue', text: '维护中' },
}

const checkStatusMeta: Record<string, { color: string; text: string }> = {
  ok: { color: 'green', text: '正常' },
  warning: { color: 'orange', text: '注意' },
  blocked: { color: 'red', text: '阻塞' },
  failed: { color: 'red', text: '失败' },
}

const renderTags = (items: string[]) => (
  <Space wrap size={[4, 4]}>
    {items.map((item) => <Tag key={item}>{item}</Tag>)}
  </Space>
)

const renderHealthChecks = (items: AgentModuleDefinition['healthChecks']) => (
  <Space direction="vertical" size={4}>
    {items.map((item) => (
      <Typography.Text key={item.name}>
        <Typography.Text strong>{item.name}</Typography.Text>
        {' · '}
        <Typography.Text type="secondary">{item.source}</Typography.Text>
        {' -> '}
        {item.target}
      </Typography.Text>
    ))}
  </Space>
)

const renderRuntimeStatus = (runtime?: DeviceAgentModuleHealth) => {
  if (!runtime) return <Tag>未上报</Tag>
  const status = runtime.effective_status || runtime.runtime_status
  const meta = runtimeStatusMeta[status] || { color: 'default', text: status }
  return <Tag color={meta.color}>{meta.text}</Tag>
}

const renderControlStatus = (runtime?: DeviceAgentModuleHealth) => {
  const control = runtime?.control
  const status = control?.status || 'enabled'
  const meta = controlStatusMeta[status] || { color: 'default', text: status }
  return (
    <Space direction="vertical" size={0}>
      <Tag color={meta.color}>{meta.text}</Tag>
      {control?.maintenance_reason && (
        <Typography.Text type="secondary" style={{ maxWidth: 220 }} ellipsis>
          {control.maintenance_reason}
        </Typography.Text>
      )}
      {(control?.maintenance_starts_at || control?.maintenance_ends_at) && (
        <Typography.Text type="secondary" style={{ maxWidth: 260 }} ellipsis>
          {control.maintenance_starts_at || '-'} 至 {control.maintenance_ends_at || '-'}
        </Typography.Text>
      )}
      {control?.updated_by && (
        <Typography.Text type="secondary">更新人：{control.updated_by}</Typography.Text>
      )}
    </Space>
  )
}

const renderMetrics = (runtime?: DeviceAgentModuleHealth) => {
  if (!runtime?.metrics?.length) return <Typography.Text type="secondary">暂无指标</Typography.Text>
  return (
    <Space wrap size={[4, 4]}>
      {runtime.metrics.map((metric) => {
        const color = metric.status === 'blocked' ? 'red' : metric.status === 'warning' ? 'orange' : 'blue'
        return <Tag key={metric.label} color={color}>{metric.label}: {String(metric.value)}</Tag>
      })}
    </Space>
  )
}

const renderRuntimeChecks = (runtime?: DeviceAgentModuleHealth) => {
  if (!runtime?.checks?.length) return <Typography.Text type="secondary">暂无运行态检查</Typography.Text>
  return (
    <Space direction="vertical" size={6}>
      {runtime.checks.map((check) => {
        const meta = checkStatusMeta[check.status] || { color: 'default', text: check.status }
        return (
          <Space key={check.name} direction="vertical" size={0}>
            <Space wrap size={6}>
              <Tag color={meta.color}>{meta.text}</Tag>
              <Typography.Text strong>{check.name}</Typography.Text>
              {check.value !== undefined && check.value !== null && <Typography.Text code>{String(check.value)}</Typography.Text>}
            </Space>
            <Typography.Text type="secondary">{check.message}</Typography.Text>
            {check.target && <Typography.Text type="secondary">目标：{check.target}</Typography.Text>}
          </Space>
        )
      })}
    </Space>
  )
}

const renderEvidence = (runtime?: DeviceAgentModuleHealth, compact = false) => {
  const evidence = runtime?.evidence || []
  if (!evidence.length) return <Typography.Text type="secondary">暂无证据</Typography.Text>
  const rows = compact ? evidence.slice(0, 2) : evidence
  return (
    <Space direction="vertical" size={compact ? 2 : 6}>
      {rows.map((item, index) => {
        const meta = checkStatusMeta[item.status] || { color: 'default', text: item.status }
        return (
          <Space key={`${item.kind}-${item.object_id || index}`} direction="vertical" size={0}>
            <Space wrap size={6}>
              <Tag color={meta.color}>{meta.text}</Tag>
              <Typography.Text strong>{item.title}</Typography.Text>
              {item.occurred_at && <Typography.Text type="secondary">{item.occurred_at}</Typography.Text>}
            </Space>
            <Space wrap size={6}>
              {item.object_id && <Typography.Text code copyable={!compact}>{item.object_id}</Typography.Text>}
              {item.summary && <Typography.Text type="secondary">{item.summary}</Typography.Text>}
              {item.target_url && (
                <Button
                  size="small"
                  type="link"
                  icon={<LinkOutlined />}
                  href={item.target_url}
                >
                  查看
                </Button>
              )}
            </Space>
            {!compact && item.detail && Object.keys(item.detail).length > 0 && (
              <Typography.Text type="secondary">
                {JSON.stringify(item.detail)}
              </Typography.Text>
            )}
          </Space>
        )
      })}
      {compact && evidence.length > rows.length && (
        <Typography.Text type="secondary">还有 {evidence.length - rows.length} 条证据</Typography.Text>
      )}
    </Space>
  )
}

const AgentModuleControlTab: React.FC = () => {
  const [controlModal, setControlModal] = useState<ControlModalState>(null)
  const [controlForm] = Form.useForm()
  const { data, isLoading, isError, refetch } = useQuery(
    'device-agent-module-health',
    deviceApi.agentModuleHealth,
    { refetchInterval: 10000 },
  )
  const controlMutation = useMutation(
    (values: {
      status: string
      maintenance_reason?: string | null
      maintenance_starts_at?: string | null
      maintenance_ends_at?: string | null
    }) => {
      if (!controlModal) throw new Error('缺少模块上下文')
      return deviceApi.updateAgentModuleControl(controlModal.moduleId, values)
    },
    {
      onSuccess: () => {
        message.success('模块治理状态已更新')
        setControlModal(null)
        controlForm.resetFields()
        refetch()
      },
      onError: (error: any) => {
        message.error(error?.response?.data?.detail || error?.message || '模块治理状态更新失败')
      },
    },
  )
  const healthByModule = new Map((data?.items || []).map((item) => [item.module_id, item]))
  const rows: AgentModuleRow[] = agentModuleDefinitions.map((item) => ({
    ...item,
    runtime: healthByModule.get(item.moduleId) || healthByModule.get(item.id),
  }))

  const openControlModal = (row: AgentModuleRow, status: AgentModuleControlStatus) => {
    setControlModal({ moduleId: row.moduleId, title: row.title, status })
    controlForm.setFieldsValue({
      status,
      maintenance_reason: status === 'enabled' ? undefined : row.runtime?.control?.maintenance_reason,
      maintenance_starts_at: status === 'enabled' ? undefined : row.runtime?.control?.maintenance_starts_at,
      maintenance_ends_at: status === 'enabled' ? undefined : row.runtime?.control?.maintenance_ends_at,
    })
  }

  const submitControlModal = async () => {
    const values = await controlForm.validateFields()
    controlMutation.mutate({
      ...values,
      maintenance_reason: values.maintenance_reason || null,
      maintenance_starts_at: values.maintenance_starts_at || null,
      maintenance_ends_at: values.maintenance_ends_at || null,
    })
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type={isError ? 'warning' : 'info'}
        showIcon
        message="Phase 1 到 Phase 6 按可插拔 Agent 模块治理：每个模块独立声明输入、输出、依赖、状态、健康信号和控制面入口，Workflow 只负责组合。"
        description={isError
          ? '运行态健康接口暂不可用，当前仅展示静态模块定义。'
          : `运行态健康每 10 秒刷新${data?.generated_at ? `，最近生成：${data.generated_at}` : ''}`}
        action={<Button size="small" icon={<ReloadOutlined />} loading={isLoading} onClick={() => refetch()}>刷新</Button>}
      />
      <Table<AgentModuleRow>
        rowKey="id"
        size="small"
        pagination={false}
        loading={isLoading}
        dataSource={rows}
        columns={[
          { title: '阶段', dataIndex: 'phase', width: 120 },
          { title: '模块', dataIndex: 'moduleId', width: 210,
            render: (value: string, row) => (
              <Space direction="vertical" size={0}>
                <Typography.Text code>{value}</Typography.Text>
                <Typography.Text type="secondary">{row.title}</Typography.Text>
              </Space>
            ) },
          { title: '职责边界', dataIndex: 'scope', width: 300 },
          { title: '归属', dataIndex: 'owner', width: 240 },
          { title: '设计状态', dataIndex: 'status', width: 110,
            render: (value: AgentModuleDefinition['status']) => {
              const meta = agentModuleStatusMeta[value]
              return <Tag color={meta.color}>{meta.text}</Tag>
            } },
          { title: '运行状态', dataIndex: 'runtime', width: 110,
            render: (_: unknown, row) => renderRuntimeStatus(row.runtime) },
          { title: '治理状态', dataIndex: 'runtime', width: 150,
            render: (_: unknown, row) => renderControlStatus(row.runtime) },
          { title: '运行指标', dataIndex: 'runtime', width: 320,
            render: (_: unknown, row) => renderMetrics(row.runtime) },
          { title: '最近证据', dataIndex: 'runtime', width: 360,
            render: (_: unknown, row) => renderEvidence(row.runtime, true) },
          { title: '健康信号', dataIndex: 'healthSignal', width: 260 },
          { title: '操作', dataIndex: 'actions', width: 210, fixed: 'right',
            render: (_: unknown, row) => (
              <Space wrap size={6}>
                <Button
                  size="small"
                  icon={<PlayCircleOutlined />}
                  disabled={(row.runtime?.control?.status || 'enabled') === 'enabled'}
                  onClick={() => openControlModal(row, 'enabled')}
                >
                  启用
                </Button>
                <Button
                  size="small"
                  icon={<PauseCircleOutlined />}
                  onClick={() => openControlModal(row, 'maintenance')}
                >
                  维护
                </Button>
                <Button
                  size="small"
                  danger
                  icon={<StopOutlined />}
                  onClick={() => openControlModal(row, 'disabled')}
                >
                  停用
                </Button>
              </Space>
            ) },
        ]}
        expandable={{
          expandedRowRender: (row) => (
            <Descriptions size="small" bordered column={1}>
              <Descriptions.Item label="输入">{renderTags(row.inputs)}</Descriptions.Item>
              <Descriptions.Item label="输出">{renderTags(row.outputs)}</Descriptions.Item>
              <Descriptions.Item label="依赖">{renderTags(row.dependencies)}</Descriptions.Item>
              <Descriptions.Item label="控制面入口">{row.controlPlane}</Descriptions.Item>
              <Descriptions.Item label="设计健康检查">{renderHealthChecks(row.healthChecks)}</Descriptions.Item>
              <Descriptions.Item label="治理状态">{renderControlStatus(row.runtime)}</Descriptions.Item>
              <Descriptions.Item label="运行态检查">{renderRuntimeChecks(row.runtime)}</Descriptions.Item>
              <Descriptions.Item label="运行证据">{renderEvidence(row.runtime)}</Descriptions.Item>
            </Descriptions>
          ),
        }}
        scroll={{ x: 2260 }}
      />
      <Modal
        title={controlModal ? `模块治理：${controlModal.title}` : '模块治理'}
        open={!!controlModal}
        confirmLoading={controlMutation.isLoading}
        onCancel={() => {
          setControlModal(null)
          controlForm.resetFields()
        }}
        onOk={submitControlModal}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={controlForm} layout="vertical" preserve={false}>
          <Form.Item name="status" label="治理状态" rules={[{ required: true, message: '请选择治理状态' }]}>
            <Select
              options={[
                { label: '已启用', value: 'enabled' },
                { label: '维护中', value: 'maintenance' },
                { label: '已停用', value: 'disabled' },
              ]}
            />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(previous, current) => previous.status !== current.status}
          >
            {({ getFieldValue }) => {
              const status = getFieldValue('status')
              return (
                <Form.Item
                  name="maintenance_reason"
                  label="原因"
                  rules={status === 'enabled' ? [] : [{ required: true, message: '停用或维护时必须填写原因' }]}
                >
                  <Input.TextArea
                    rows={3}
                    disabled={status === 'enabled'}
                    placeholder={status === 'enabled' ? '启用状态不需要原因' : '说明维护窗口、停用原因或恢复条件'}
                    maxLength={500}
                    showCount
                  />
                </Form.Item>
              )
            }}
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(previous, current) => previous.status !== current.status}
          >
            {({ getFieldValue }) => {
              const status = getFieldValue('status')
              return (
                <Space size={12} style={{ width: '100%' }} align="start">
                  <Form.Item
                    name="maintenance_starts_at"
                    label="窗口开始"
                    style={{ flex: 1 }}
                  >
                    <Input
                      disabled={status === 'enabled'}
                      placeholder="2026-06-23T22:00:00+08:00"
                    />
                  </Form.Item>
                  <Form.Item
                    name="maintenance_ends_at"
                    label="窗口结束"
                    style={{ flex: 1 }}
                  >
                    <Input
                      disabled={status === 'enabled'}
                      placeholder="2026-06-23T23:00:00+08:00"
                    />
                  </Form.Item>
                </Space>
              )
            }}
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}

export default AgentModuleControlTab
