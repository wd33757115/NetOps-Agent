// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useMemo, useState } from 'react'
import { Alert, Card, Empty, Input, Modal, Select, Spin, Tag, Typography, message } from 'antd'
import {
  EyeOutlined,
  EditOutlined,
  DeleteOutlined,
  HistoryOutlined,
  PlusOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { useQuery } from 'react-query'
import { GrokChip, GrokToolBtn } from '../ui/GrokUi'
import { workflowApi, WorkflowPluginStatus, WorkflowPluginSummary } from '../../services/api'
import { WorkflowDSL } from '../../types/workflowDsl'
import WorkflowRunMonitor from './WorkflowRunMonitor'
import WorkflowPluginGovernance from './WorkflowPluginGovernance'

const { Text, Paragraph } = Typography

const STATUS_LABEL: Record<WorkflowPluginStatus, { text: string; color: string }> = {
  draft: { text: '草稿', color: 'default' },
  review: { text: '待审核', color: 'processing' },
  published: { text: '已发布', color: 'success' },
  archived: { text: '已归档', color: 'warning' },
}

interface WorkflowPluginListProps {
  onCreateWizard: () => void
  onEditWizard: (initial: {
    initialDsl: WorkflowDSL
    chatIntentYaml: string
    workflowYaml: string
  }) => void
}

const WorkflowPluginList: React.FC<WorkflowPluginListProps> = ({ onCreateWizard, onEditWizard }) => {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<WorkflowPluginStatus | 'all'>('all')
  const [viewName, setViewName] = useState<string | null>(null)
  const [yamlContent, setYamlContent] = useState<string | null>(null)
  const [monitorRunId, setMonitorRunId] = useState<string | null>(null)
  const [governancePlugin, setGovernancePlugin] = useState<WorkflowPluginSummary | null>(null)
  const [testPlugin, setTestPlugin] = useState<WorkflowPluginSummary | null>(null)
  const [testContextText, setTestContextText] = useState('{}')
  const [testContextError, setTestContextError] = useState<string | null>(null)
  const [testRunLoading, setTestRunLoading] = useState<string | null>(null)
  const [historyLoading, setHistoryLoading] = useState<string | null>(null)

  const { data: plugins = [], isLoading, isFetching, refetch } = useQuery(
    'workflow-plugins',
    workflowApi.listPlugins,
    { refetchOnWindowFocus: false, retry: 2, staleTime: 30_000 },
  )

  const listLoading = isLoading || (isFetching && plugins.length === 0)

  const filtered = useMemo(() => plugins.filter((t: WorkflowPluginSummary) => {
    const q = search.trim().toLowerCase()
    if (statusFilter !== 'all' && t.status !== statusFilter) return false
    if (!q) return true
    return t.name.includes(q) || (t.description || '').toLowerCase().includes(q)
  }), [plugins, search, statusFilter])

  const statusOptions = [
    { value: 'all', label: '全部状态' },
    { value: 'published', label: '已发布' },
    { value: 'draft', label: '草稿' },
    { value: 'review', label: '待审核' },
    { value: 'archived', label: '已归档' },
  ]

  const handleEdit = async (name: string) => {
    try {
      const result = await workflowApi.getTemplateDsl(name)
      const detail = await workflowApi.getTemplate(name)
      onEditWizard({
        initialDsl: result.dsl,
        chatIntentYaml: result.chat_intent_yaml,
        workflowYaml: detail.files['WORKFLOW.yaml'] || '',
      })
    } catch {
      message.error('加载 Workflow 失败')
    }
  }

  const handleView = async (name: string) => {
    try {
      const detail = await workflowApi.getTemplate(name)
      setViewName(name)
      setYamlContent(detail.files['WORKFLOW.yaml'] || '')
    } catch {
      message.error('读取 Workflow 失败')
    }
  }

  const handleReload = async () => {
    try {
      await workflowApi.reload()
      message.success('Workflow 插件已重载')
      refetch()
    } catch {
      message.error('重载失败')
    }
  }

  const openTestRun = (plugin: WorkflowPluginSummary) => {
    setTestPlugin(plugin)
    setTestContextText(JSON.stringify(plugin.test_context || {}, null, 2))
    setTestContextError(null)
  }

  const handleTestRun = async () => {
    if (!testPlugin) return
    let context: Record<string, unknown>
    try {
      const parsed = JSON.parse(testContextText)
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
        throw new Error('试跑上下文必须是 JSON 对象')
      }
      context = parsed as Record<string, unknown>
    } catch (err) {
      setTestContextError(err instanceof Error ? err.message : 'JSON 格式错误')
      return
    }
    const missing = (testPlugin.test_required_context || []).filter((field) => {
      const value = context[field]
      return value === null || value === undefined || (typeof value === 'string' && !value.trim())
    })
    if (missing.length > 0) {
      setTestContextError(`请填写必填参数：${missing.join(', ')}`)
      return
    }

    setTestRunLoading(testPlugin.name)
    setTestContextError(null)
    try {
      const res = await workflowApi.testRun({
        template_name: testPlugin.name,
        context,
      })
      setMonitorRunId(res.run_id)
      setTestPlugin(null)
      message.success('试跑已启动')
    } catch (err) {
      setTestContextError(err instanceof Error ? err.message : '试跑失败')
    } finally {
      setTestRunLoading(null)
    }
  }

  const handleViewLastRun = async (name: string) => {
    setHistoryLoading(name)
    try {
      const runs = await workflowApi.listRuns({ template_name: name, limit: 1 })
      if (runs.length === 0) {
        message.info('暂无运行记录，可先试跑')
        return
      }
      setMonitorRunId(runs[0].run_id)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载运行记录失败')
    } finally {
      setHistoryLoading(null)
    }
  }

  const handleDelete = (tpl: WorkflowPluginSummary) => {
    const isPublished = tpl.status === 'published'
    Modal.confirm({
      title: `删除 Workflow 插件「${tpl.name}」？`,
      content: isPublished
        ? '该插件已发布，删除后将移除磁盘文件、元数据及市场关联，聊天触发也会失效。此操作不可恢复。'
        : '将永久删除插件目录与元数据，不可恢复。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await workflowApi.deletePlugin(tpl.name)
          message.success('插件已删除')
          refetch()
        } catch (err) {
          message.error(err instanceof Error ? err.message : '删除失败（需 admin 或 operator 权限）')
        }
      },
    })
  }

  return (
    <>
      <div className="grok-page-toolbar grok-page-toolbar-inline">
        <Input
          className="grok-search-input"
          placeholder="搜索 Workflow 插件…"
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Select
          style={{ width: 120, flex: 'none' }}
          value={statusFilter}
          onChange={(v) => setStatusFilter(v as WorkflowPluginStatus | 'all')}
          options={statusOptions}
        />
        <GrokToolBtn icon={<ReloadOutlined />} onClick={handleReload} title="通知后端重新扫描插件目录">
          热重载
        </GrokToolBtn>
        <GrokToolBtn icon={<ReloadOutlined />} onClick={() => refetch()} title="仅刷新当前列表">
          刷新列表
        </GrokToolBtn>
        <GrokToolBtn primary icon={<PlusOutlined />} onClick={onCreateWizard}>创建 Workflow</GrokToolBtn>
      </div>

      {listLoading ? (
        <div className="grok-page-loading"><Spin size="large" /></div>
      ) : filtered.length === 0 ? (
        <Empty
          description={plugins.length === 0 ? '暂无 Workflow 插件' : '没有符合筛选条件的插件'}
          className="grok-empty"
        />
      ) : (
        <div className="grok-skill-grid">
          {filtered.map((tpl) => {
            const statusMeta = STATUS_LABEL[tpl.status] ?? STATUS_LABEL.published
            return (
              <Card key={tpl.name} className="grok-skill-card" bordered={false}>
                <div className="grok-skill-card-inner">
                  <div className="grok-skill-card-main">
                    <div className="grok-skill-card-title">{tpl.name}</div>
                    <Paragraph type="secondary" className="grok-skill-card-desc" ellipsis={{ rows: 2 }}>
                      {tpl.description || '—'}
                    </Paragraph>
                    <div className="grok-chip-row grok-skill-card-tags">
                      <Tag color={statusMeta.color}>{statusMeta.text}</Tag>
                      <GrokChip tone="ok">v{tpl.current_version || tpl.version}</GrokChip>
                      <GrokChip>{tpl.step_count} 步</GrokChip>
                      {tpl.has_chat_intent && tpl.status === 'published' && <Tag color="blue">聊天触发</Tag>}
                      {tpl.has_chat_intent && tpl.status !== 'published' && (
                        <Tag color="default">聊天未激活</Tag>
                      )}
                      {tpl.has_webhook && <Tag color="purple">Webhook</Tag>}
                    </div>
                    <Text type="secondary" className="grok-skill-card-path">{tpl.plugin_dir}</Text>
                  </div>
                  <div className="grok-skill-card-footer">
                    <div className="grok-skill-card-btns">
                      <GrokToolBtn icon={<EditOutlined />} onClick={() => handleEdit(tpl.name)}>编辑</GrokToolBtn>
                      <GrokToolBtn icon={<EyeOutlined />} onClick={() => handleView(tpl.name)}>查看 YAML</GrokToolBtn>
                      <GrokToolBtn
                        icon={<PlayCircleOutlined />}
                        loading={testRunLoading === tpl.name}
                        onClick={() => openTestRun(tpl)}
                      >
                        试跑
                      </GrokToolBtn>
                      <GrokToolBtn
                        icon={<HistoryOutlined />}
                        loading={historyLoading === tpl.name}
                        onClick={() => handleViewLastRun(tpl.name)}
                      >
                        最近运行
                      </GrokToolBtn>
                      <GrokToolBtn icon={<SettingOutlined />} onClick={() => setGovernancePlugin(tpl)}>
                        治理
                      </GrokToolBtn>
                      <GrokToolBtn icon={<DeleteOutlined />} onClick={() => handleDelete(tpl)}>
                        删除
                      </GrokToolBtn>
                    </div>
                  </div>
                </div>
              </Card>
            )
          })}
        </div>
      )}

      <Modal
        title={viewName ? `WORKFLOW.yaml — ${viewName}` : 'Workflow'}
        open={!!yamlContent}
        onCancel={() => { setYamlContent(null); setViewName(null) }}
        footer={null}
        width={860}
      >
        <Input.TextArea value={yamlContent || ''} readOnly rows={24} style={{ fontFamily: 'monospace', fontSize: 12 }} />
      </Modal>

      <WorkflowPluginGovernance
        plugin={governancePlugin}
        open={!!governancePlugin}
        onClose={() => setGovernancePlugin(null)}
      />

      <Modal
        title={testPlugin ? `试跑 Workflow - ${testPlugin.name}` : '试跑 Workflow'}
        open={!!testPlugin}
        onCancel={() => {
          if (!testRunLoading) {
            setTestPlugin(null)
            setTestContextError(null)
          }
        }}
        onOk={handleTestRun}
        okText="启动试跑"
        cancelText="取消"
        confirmLoading={!!testRunLoading}
        width={720}
      >
        <Paragraph type="secondary">
          参数由当前 Workflow 声明。平台只负责校验并传递上下文，不附加工单或其他业务字段。
        </Paragraph>
        {(testPlugin?.test_required_context || []).length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Text type="secondary">必填：</Text>
            {testPlugin?.test_required_context?.map((field) => (
              <Tag key={field} color="processing">{field}</Tag>
            ))}
          </div>
        )}
        {testContextError && (
          <Alert type="error" showIcon message={testContextError} style={{ marginBottom: 12 }} />
        )}
        <Input.TextArea
          value={testContextText}
          onChange={(event) => setTestContextText(event.target.value)}
          rows={16}
          spellCheck={false}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
        />
      </Modal>

      <WorkflowRunMonitor runId={monitorRunId} onClose={() => setMonitorRunId(null)} />
    </>
  )
}

export default WorkflowPluginList
