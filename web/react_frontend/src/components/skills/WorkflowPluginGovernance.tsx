// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react'
import {
  Drawer,
  Input,
  List,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import {
  DownloadOutlined,
  RocketOutlined,
  RollbackOutlined,
  ShopOutlined,
} from '@ant-design/icons'
import { useAuth } from '../../context/AuthContext'
import { GrokToolBtn } from '../ui/GrokUi'
import {
  workflowApi,
  WorkflowPluginStatus,
  WorkflowPluginSummary,
} from '../../services/api'

const { Text, Paragraph } = Typography

const STATUS_LABEL: Record<WorkflowPluginStatus, { text: string; color: string }> = {
  draft: { text: '草稿', color: 'default' },
  review: { text: '待审核', color: 'processing' },
  published: { text: '已发布', color: 'success' },
  archived: { text: '已归档', color: 'warning' },
}

interface WorkflowPluginGovernanceProps {
  plugin: WorkflowPluginSummary | null
  open: boolean
  onClose: () => void
}

const WorkflowPluginGovernance: React.FC<WorkflowPluginGovernanceProps> = ({
  plugin,
  open,
  onClose,
}) => {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const isAdmin = user?.role === 'admin'
  const name = plugin?.name ?? ''

  const [diffV1, setDiffV1] = useState<number | null>(null)
  const [diffV2, setDiffV2] = useState<number | null>(null)
  const [changeSummary, setChangeSummary] = useState('')

  const { data: versions = [], isLoading: versionsLoading, refetch: refetchVersions } = useQuery(
    ['plugin-versions', name],
    () => workflowApi.listPluginVersions(name),
    { enabled: open && !!name, refetchOnWindowFocus: false },
  )

  const { data: diffResult, isLoading: diffLoading } = useQuery(
    ['plugin-diff', name, diffV1, diffV2],
    () => workflowApi.diffPluginVersions(name, diffV1!, diffV2!),
    { enabled: open && !!name && diffV1 != null && diffV2 != null && diffV1 !== diffV2 },
  )

  const invalidate = () => {
    queryClient.invalidateQueries('workflow-plugins')
    queryClient.invalidateQueries('workflow-templates')
    refetchVersions()
  }

  const submitReviewMutation = useMutation(() => workflowApi.submitPluginReview(name), {
    onSuccess: () => {
      message.success('已提交审核')
      invalidate()
    },
    onError: (err: unknown) => message.error(err instanceof Error ? err.message : '提交失败'),
  })

  const publishMutation = useMutation(
    () => workflowApi.publishPlugin(name, changeSummary || undefined),
    {
      onSuccess: () => {
        message.success('插件已发布')
        setChangeSummary('')
        invalidate()
      },
      onError: (err: unknown) => message.error(err instanceof Error ? err.message : '发布失败'),
    },
  )

  const rejectMutation = useMutation(() => workflowApi.rejectPlugin(name), {
    onSuccess: () => {
      message.success('已驳回为草稿')
      invalidate()
    },
    onError: (err: unknown) => message.error(err instanceof Error ? err.message : '驳回失败'),
  })

  const marketMutation = useMutation(() => workflowApi.publishPluginToMarket(name, plugin?.description), {
    onSuccess: () => message.success('已发布到模板市场'),
    onError: (err: unknown) => message.error(err instanceof Error ? err.message : '发布到市场失败'),
  })

  const handleExport = async () => {
    try {
      const bundle = await workflowApi.exportPlugin(name)
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${name}-workflow-bundle.json`
      a.click()
      URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch {
      message.error('导出失败')
    }
  }

  const status = plugin?.status ?? 'published'
  const statusMeta = STATUS_LABEL[status] ?? STATUS_LABEL.published

  return (
    <Drawer
      title={name ? `插件治理 — ${name}` : '插件治理'}
      open={open}
      onClose={onClose}
      width={640}
      destroyOnClose
    >
      {!plugin ? null : (
        <>
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <div>
              <Tag color={statusMeta.color}>{statusMeta.text}</Tag>
              <GrokChipInline>v{plugin.current_version || plugin.version}</GrokChipInline>
              {plugin.has_chat_intent && <Tag color="blue">聊天触发</Tag>}
            </div>
            <Paragraph type="secondary">{plugin.description || '—'}</Paragraph>
            <Text type="secondary" style={{ fontSize: 12 }}>{plugin.plugin_dir}</Text>

            <Space wrap>
              <GrokToolBtn icon={<DownloadOutlined />} onClick={handleExport}>
                导出 JSON
              </GrokToolBtn>
              {(status === 'draft' || status === 'archived') && (
                <GrokToolBtn
                  icon={<RocketOutlined />}
                  disabled={submitReviewMutation.isLoading}
                  onClick={() => submitReviewMutation.mutate()}
                >
                  提交审核
                </GrokToolBtn>
              )}
              {isAdmin && status === 'review' && (
                <>
                  <GrokToolBtn
                    primary
                    icon={<RocketOutlined />}
                    disabled={publishMutation.isLoading}
                    onClick={() => publishMutation.mutate()}
                  >
                    批准发布
                  </GrokToolBtn>
                  <GrokToolBtn
                    icon={<RollbackOutlined />}
                    disabled={rejectMutation.isLoading}
                    onClick={() => rejectMutation.mutate()}
                  >
                    驳回
                  </GrokToolBtn>
                </>
              )}
              {isAdmin && status === 'published' && (
                <GrokToolBtn
                  icon={<ShopOutlined />}
                  disabled={marketMutation.isLoading}
                  onClick={() => marketMutation.mutate()}
                >
                  发布到市场
                </GrokToolBtn>
              )}
            </Space>

            {isAdmin && status === 'review' && (
              <Input.TextArea
                rows={2}
                placeholder="发布说明（可选）"
                value={changeSummary}
                onChange={(e) => setChangeSummary(e.target.value)}
              />
            )}
          </Space>

          <Typography.Title level={5} style={{ marginTop: 24 }}>
            版本历史
          </Typography.Title>
          {versionsLoading ? (
            <Spin />
          ) : versions.length === 0 ? (
            <Text type="secondary">暂无版本快照（发布后自动创建）</Text>
          ) : (
            <List
              size="small"
              dataSource={versions}
              renderItem={(v) => (
                <List.Item>
                  <Space direction="vertical" size={0} style={{ width: '100%' }}>
                    <Space>
                      <Text strong>v{v.version}</Text>
                      <Tag>{v.status}</Tag>
                      {v.created_at && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {new Date(v.created_at).toLocaleString()}
                        </Text>
                      )}
                    </Space>
                    {v.change_summary && (
                      <Text type="secondary" style={{ fontSize: 12 }}>{v.change_summary}</Text>
                    )}
                  </Space>
                </List.Item>
              )}
            />
          )}

          {versions.length >= 2 && (
            <>
              <Typography.Title level={5} style={{ marginTop: 24 }}>
                版本对比
              </Typography.Title>
              <Space style={{ marginBottom: 12 }}>
                <Select
                  placeholder="版本 A"
                  style={{ width: 120 }}
                  value={diffV1 ?? undefined}
                  onChange={setDiffV1}
                  options={versions.map((v) => ({ value: v.version, label: `v${v.version}` }))}
                />
                <Text>→</Text>
                <Select
                  placeholder="版本 B"
                  style={{ width: 120 }}
                  value={diffV2 ?? undefined}
                  onChange={setDiffV2}
                  options={versions.map((v) => ({ value: v.version, label: `v${v.version}` }))}
                />
              </Space>
              {diffLoading ? (
                <Spin />
              ) : diffResult ? (
                diffResult.has_diff ? (
                  <Input.TextArea
                    readOnly
                    value={diffResult.diff}
                    rows={14}
                    style={{ fontFamily: 'monospace', fontSize: 11 }}
                  />
                ) : (
                  <Text type="secondary">两个版本 WORKFLOW.yaml 无差异</Text>
                )
              ) : null}
            </>
          )}
        </>
      )}
    </Drawer>
  )
}

/** 轻量 chip，避免循环依赖 GrokChip */
const GrokChipInline: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span
    style={{
      display: 'inline-block',
      padding: '0 8px',
      borderRadius: 6,
      fontSize: 12,
      background: 'rgba(255,255,255,0.06)',
      marginRight: 8,
    }}
  >
    {children}
  </span>
)

export default WorkflowPluginGovernance
