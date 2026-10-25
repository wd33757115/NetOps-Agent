// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect } from 'react'
import { Drawer, Form, Input, Select, Slider, Space, Switch, Typography, message } from 'antd'
import { useMutation, useQueryClient } from 'react-query'
import {
  SkillItem,
  SkillRolloutStatus,
  SkillRolloutUpdate,
  skillCatalogApi,
} from '../../services/api'
import { GrokToolBtn } from '../ui/GrokUi'

const { Text, Paragraph } = Typography

const ROLLOUT_OPTIONS: { value: SkillRolloutStatus; label: string }[] = [
  { value: 'draft', label: '草稿 (draft)' },
  { value: 'canary', label: '灰度 (canary)' },
  { value: 'stable', label: '稳定 (stable)' },
  { value: 'deprecated', label: '废弃 (deprecated)' },
]

interface SkillRolloutDrawerProps {
  skill: SkillItem | null
  open: boolean
  onClose: () => void
}

const SkillRolloutDrawer: React.FC<SkillRolloutDrawerProps> = ({ skill, open, onClose }) => {
  const queryClient = useQueryClient()
  const [form] = Form.useForm<SkillRolloutUpdate & { enabled: boolean }>()

  useEffect(() => {
    if (!open || !skill) return
    form.setFieldsValue({
      rollout_status: (skill.rollout_status || 'stable') as SkillRolloutStatus,
      enabled_ratio: skill.enabled_ratio ?? 100,
      min_platform_version: skill.min_platform_version || '1.0.0',
      enabled: skill.enabled,
    })
  }, [open, skill, form])

  const saveMutation = useMutation(
    (values: SkillRolloutUpdate) => skillCatalogApi.updateRollout(skill!.name, values),
    {
      onSuccess: () => {
        message.success('灰度配置已保存')
        queryClient.invalidateQueries('skills')
        queryClient.invalidateQueries('skill-catalog-stats')
        onClose()
      },
      onError: (err: unknown) => {
        message.error(err instanceof Error ? err.message : '保存失败')
      },
    }
  )

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      saveMutation.mutate({
        rollout_status: values.rollout_status,
        enabled_ratio: values.enabled_ratio,
        min_platform_version: values.min_platform_version || null,
        enabled: values.enabled,
      })
    } catch {
      /* 表单校验失败 */
    }
  }

  return (
    <Drawer
      title={skill ? `灰度治理 — ${skill.name}` : '灰度治理'}
      open={open}
      onClose={onClose}
      width={480}
      destroyOnClose
      footer={
        <Space style={{ float: 'right' }}>
          <GrokToolBtn onClick={onClose}>取消</GrokToolBtn>
          <GrokToolBtn primary loading={saveMutation.isLoading} onClick={handleSubmit}>
            保存
          </GrokToolBtn>
        </Space>
      }
    >
      {!skill ? null : (
        <>
          <Paragraph type="secondary" style={{ marginBottom: 16 }}>
            {skill.description || '—'}
          </Paragraph>
          <Space direction="vertical" size={4} style={{ marginBottom: 20, width: '100%' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              域: {skill.domain || skill.category}
              {skill.celery_queue ? ` · 队列: ${skill.celery_queue}` : ''}
            </Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              向量索引: {skill.catalog_indexed ? '已建立' : '未索引'}
            </Text>
          </Space>
          <Form form={form} layout="vertical">
            <Form.Item
              name="rollout_status"
              label="发布阶段"
              rules={[{ required: true, message: '请选择发布阶段' }]}
            >
              <Select options={ROLLOUT_OPTIONS} />
            </Form.Item>
            <Form.Item
              name="enabled_ratio"
              label="灰度比例 (%)"
              tooltip="canary 阶段按 thread_id 哈希决定是否命中；stable 建议 100"
            >
              <Slider min={0} max={100} marks={{ 0: '0%', 50: '50%', 100: '100%' }} />
            </Form.Item>
            <Form.Item name="min_platform_version" label="最低平台版本">
              <Input placeholder="例如 1.0.0" />
            </Form.Item>
            <Form.Item name="enabled" label="Catalog 启用" valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="禁用" />
            </Form.Item>
          </Form>
        </>
      )}
    </Drawer>
  )
}

export default SkillRolloutDrawer
