// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react'
import { Alert, Form, Input, Modal, Select, Steps, message, Typography } from 'antd'
import { useMutation } from 'react-query'
import { skillApi } from '../../services/api'

const { TextArea } = Input
const { Text } = Typography

const GOVERNANCE_BLOCK = `domain: general
celery_queue: netops.default
min_permission_level: user
rollout_status: draft
enabled_ratio: 0
min_platform_version: "1.0.0"`

const ANALYSIS_TEMPLATE = `---
name: my-analyzer
version: 1.0.0
description: 自定义 LLM 分析 Skill
category: analysis
tags:
- analysis
${GOVERNANCE_BLOCK}
entry_script: scripts/run.py
entry_output: file
triggers:
- 分析
enabled: true
fallback_to_rag: false
inputs:
- name: ticket_id
  type: string
  required: false
- name: prev_result
  type: object
  required: false
- name: analysis_prompt
  type: string
  required: false
outputs:
- name: analysis
  type: text
- name: analysis_json
  type: object
---

# 自定义分析 Skill

读取上游结果并生成报告。
`

const GENERIC_TEMPLATE = `---
name: my-skill
version: 1.0.0
description: 自定义 Skill
category: general
tags: []
${GOVERNANCE_BLOCK}
entry_script: scripts/run.py
entry_output: none
triggers: []
enabled: true
fallback_to_rag: false
inputs: []
outputs: []
---

# 自定义 Skill
`

interface SkillCreateWizardProps {
  open: boolean
  onClose: () => void
  onCreated?: () => void
}

const SkillCreateWizard: React.FC<SkillCreateWizardProps> = ({ open, onClose, onCreated }) => {
  const [step, setStep] = useState(0)
  const [form] = Form.useForm()
  const [testParams, setTestParams] = useState('{}')
  const [testResult, setTestResult] = useState<string | null>(null)
  const [createdName, setCreatedName] = useState<string | null>(null)

  const createMutation = useMutation(
    (values: Record<string, unknown>) => skillApi.create(values),
    {
      onSuccess: (_, values) => {
        message.success('Skill 创建成功（默认 draft，需在灰度治理中发布）')
        setCreatedName(String(values.name))
        setStep(3)
        onCreated?.()
      },
      onError: () => message.error('创建失败'),
    }
  )

  const testMutation = useMutation(
    async () => {
      const name = createdName || form.getFieldValue('name')
      if (!name) throw new Error('请先填写 Skill 名称')
      let params: Record<string, unknown> = {}
      try {
        params = JSON.parse(testParams || '{}')
      } catch {
        throw new Error('params JSON 格式错误')
      }
      return skillApi.testRun(name, params)
    },
    {
      onSuccess: (res) => {
        setTestResult(JSON.stringify(res, null, 2))
        message.success('试跑完成')
      },
      onError: (err: Error) => message.error(err.message || '试跑失败'),
    }
  )

  const handleClose = () => {
    setStep(0)
    form.resetFields()
    setTestParams('{}')
    setTestResult(null)
    setCreatedName(null)
    onClose()
  }

  const handleFinishBasic = () => {
    form.validateFields().then((values) => {
      const tags = values.tags
        ? String(values.tags).split(',').map((t: string) => t.trim()).filter(Boolean)
        : []
      const triggers = values.triggers
        ? String(values.triggers).split('\n').map((t: string) => t.trim()).filter(Boolean)
        : []
      createMutation.mutate({
        ...values,
        tags,
        triggers,
        template_type: values.template_type || 'generic',
      })
    })
  }

  const templateType = Form.useWatch('template_type', form)

  return (
    <Modal
      title="新建 Skill 向导"
      open={open}
      onCancel={handleClose}
      width={720}
      footer={null}
      destroyOnClose
      maskClosable={false}
    >
      <Steps
        current={step}
        size="small"
        style={{ marginBottom: 24 }}
        items={[
          { title: '基本信息' },
          { title: '模板选择' },
          { title: '确认创建' },
          { title: '试跑测试' },
        ]}
      />

      <Form form={form} layout="vertical" initialValues={{ category: 'general', template_type: 'generic' }}>
        {step === 0 && (
          <>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="新建 Skill 默认 rollout_status=draft，创建后需在卡片「灰度」中发布为 canary/stable 方可被路由。"
            />
            <Form.Item name="name" label="名称" rules={[{ required: true, pattern: /^[a-z0-9-]+$/, message: '小写字母、数字、连字符' }]}>
              <Input placeholder="my-skill" />
            </Form.Item>
            <Form.Item name="description" label="描述" rules={[{ required: true }]}>
              <TextArea rows={3} />
            </Form.Item>
            <Form.Item name="category" label="分类" tooltip="决定 domain / Celery 队列默认值">
              <Select options={[
                { value: 'network', label: 'network（netops.device）' },
                { value: 'security', label: 'security（netops.firewall）' },
                { value: 'itsm', label: 'itsm（netops.default）' },
                { value: 'analysis', label: 'analysis（netops.default）' },
                { value: 'general', label: 'general（netops.default）' },
              ]} />
            </Form.Item>
            <Form.Item name="triggers" label="触发词（每行一个）">
              <TextArea rows={2} />
            </Form.Item>
            <Form.Item name="tags" label="标签（逗号分隔）">
              <Input placeholder="network, workflow" />
            </Form.Item>
            <div style={{ textAlign: 'right' }}>
              <button type="button" className="grok-tool-btn is-primary" onClick={() => setStep(1)}>
                下一步
              </button>
            </div>
          </>
        )}

        {step === 1 && (
          <>
            <Form.Item name="template_type" label="脚手架模板">
              <Select options={[
                { value: 'generic', label: '通用 Skill' },
                { value: 'analysis', label: 'LLM 分析类' },
              ]} />
            </Form.Item>
            {templateType === 'analysis' && (
              <TextArea value={ANALYSIS_TEMPLATE} readOnly rows={14} style={{ fontFamily: 'monospace', fontSize: 12 }} />
            )}
            {templateType === 'generic' && (
              <TextArea value={GENERIC_TEMPLATE} readOnly rows={12} style={{ fontFamily: 'monospace', fontSize: 12 }} />
            )}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16 }}>
              <button type="button" className="grok-tool-btn" onClick={() => setStep(0)}>上一步</button>
              <button type="button" className="grok-tool-btn is-primary" onClick={() => setStep(2)}>下一步</button>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <Text>确认创建 Skill 并生成目录脚手架。创建后可在编辑器中完善 SKILL.md 与 scripts。</Text>
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message="Catalog 同步"
              description="创建后会自动 reload 并写入 Catalog；默认 draft 状态不会参与聊天路由，请通过「灰度」调整为 canary 或 stable。"
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
              <button type="button" className="grok-tool-btn" onClick={() => setStep(1)}>上一步</button>
              <button
                type="button"
                className="grok-tool-btn is-primary"
                disabled={createMutation.isLoading}
                onClick={handleFinishBasic}
              >
                {createMutation.isLoading ? '创建中…' : '创建 Skill'}
              </button>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <Text>
              Skill <strong>{createdName}</strong> 已创建（<Text code>draft</Text>）。
              试跑前请先在卡片「灰度」中设为 <Text code>stable</Text> 或本地关闭治理校验。
            </Text>
            <div style={{ marginTop: 12 }}>
              <TextArea
                value={testParams}
                onChange={(e) => setTestParams(e.target.value)}
                rows={6}
                placeholder='{"ticket_id": "REQ2025001", "prev_result": {...}}'
                style={{ fontFamily: 'monospace' }}
              />
            </div>
            {testResult && (
              <TextArea value={testResult} readOnly rows={8} style={{ marginTop: 12, fontFamily: 'monospace' }} />
            )}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16 }}>
              <button
                type="button"
                className="grok-tool-btn"
                disabled={testMutation.isLoading}
                onClick={() => testMutation.mutate()}
              >
                {testMutation.isLoading ? '试跑中…' : '试跑 Skill'}
              </button>
              <button type="button" className="grok-tool-btn is-primary" onClick={handleClose}>完成</button>
            </div>
          </>
        )}
      </Form>
    </Modal>
  )
}

export default SkillCreateWizard
