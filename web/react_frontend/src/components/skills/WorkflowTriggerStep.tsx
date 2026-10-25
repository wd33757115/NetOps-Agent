// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react'
import { Alert, Input, Tabs, Typography, message } from 'antd'
import { BulbOutlined } from '@ant-design/icons'
import { GrokToolBtn } from '../ui/GrokUi'
import { workflowApi } from '../../services/api'
import ChatIntentEditor from './ChatIntentEditor'
import WebhookTriggerEditor from './WebhookTriggerEditor'
import type { ItsmWebhookDSL } from '../../types/workflowDsl'

const { Text } = Typography

interface WorkflowTriggerStepProps {
  chatYaml: string
  onChatYamlChange: (v: string) => void
  webhook: ItsmWebhookDSL
  onWebhookChange: (v: ItsmWebhookDSL) => void
  workflowName: string
}

const WorkflowTriggerStep: React.FC<WorkflowTriggerStepProps> = ({
  chatYaml,
  onChatYamlChange,
  webhook,
  onWebhookChange,
  workflowName,
}) => {
  const [nlDescription, setNlDescription] = useState('')
  const [suggesting, setSuggesting] = useState(false)

  const handleSuggestNl = async () => {
    if (!nlDescription.trim()) {
      message.warning('请先描述触发场景')
      return
    }
    setSuggesting(true)
    try {
      let res = await workflowApi.suggestChatIntentFromNl({
        description: nlDescription,
        workflow_name: workflowName,
        use_llm: false,
      })
      if (!res.chat_intent_yaml) {
        res = await workflowApi.suggestChatIntentFromNl({
          description: nlDescription,
          workflow_name: workflowName,
          use_llm: true,
        })
      }
      if (res.chat_intent_yaml) {
        onChatYamlChange(res.chat_intent_yaml)
        message.success(`已生成 CHAT.intent 草稿（${res.source === 'llm' ? 'LLM' : '规则'}）`)
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : '生成失败')
    } finally {
      setSuggesting(false)
    }
  }

  const tabItems = [
    {
      key: 'chat',
      label: '聊天触发',
      children: (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="自然语言助手"
            description={
              <div>
                <Input.TextArea
                  rows={2}
                  placeholder="例如：用户提到防火墙策略变更并需要编写变更工单时触发"
                  value={nlDescription}
                  onChange={(e) => setNlDescription(e.target.value)}
                  style={{ marginBottom: 8 }}
                />
                <GrokToolBtn icon={<BulbOutlined />} loading={suggesting} onClick={handleSuggestNl}>
                  从描述生成关键词
                </GrokToolBtn>
              </div>
            }
          />
          <ChatIntentEditor
            value={chatYaml}
            onChange={onChatYamlChange}
            workflowName={workflowName}
          />
        </>
      ),
    },
    {
      key: 'webhook',
      label: 'ITSM Webhook',
      children: (
        <WebhookTriggerEditor
          value={webhook}
          workflowName={workflowName}
          onChange={onWebhookChange}
        />
      ),
    },
  ]

  return (
    <div>
      <Text type="secondary">配置 Workflow 的聊天关键词或 ITSM Webhook 入口（可只启用其一）。</Text>
      <Tabs items={tabItems} style={{ marginTop: 12 }} />
    </div>
  )
}

export default WorkflowTriggerStep
