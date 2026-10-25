// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { Form, Input, Switch, Typography } from 'antd'
import type { ItsmWebhookDSL } from '../../types/workflowDsl'

const { Text } = Typography

interface WebhookTriggerEditorProps {
  value: ItsmWebhookDSL
  workflowName: string
  onChange: (value: ItsmWebhookDSL) => void
}

const WebhookTriggerEditor: React.FC<WebhookTriggerEditorProps> = ({
  value,
  workflowName,
  onChange,
}) => {
  const enabled = value.enabled ?? false

  return (
    <div className="grok-webhook-trigger-editor">
      <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
        ITSM Webhook 触发：工单系统回调时自动启动 Workflow「{workflowName}」。
      </Text>
      <Form layout="vertical" size="small">
        <Form.Item label="启用 Webhook 触发">
          <Switch
            checked={enabled}
            onChange={(checked) => onChange({ ...value, enabled: checked })}
          />
        </Form.Item>
        {enabled && (
          <>
            <Form.Item label="route_key" required>
              <Input
                placeholder="firewall-policy"
                value={value.route_key}
                onChange={(e) => onChange({ ...value, route_key: e.target.value })}
              />
            </Form.Item>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
              回调地址：<code>POST /api/v1/itsm/webhook/{value.route_key || '{route_key}'}</code>
            </Text>
            <Form.Item label="受理提示">
              <Input
                value={value.accepted_message}
                placeholder="已受理，正在处理"
                onChange={(e) => onChange({ ...value, accepted_message: e.target.value })}
              />
            </Form.Item>
            <Form.Item label="context 映射（JSON，可选）">
              <Input.TextArea
                rows={4}
                placeholder='{"ticket_id": "body.ticket_id", "policy_file_url": "body.policy_url"}'
                value={
                  value.context_mapping
                    ? JSON.stringify(value.context_mapping, null, 2)
                    : ''
                }
                onChange={(e) => {
                  try {
                    const parsed = e.target.value.trim()
                      ? (JSON.parse(e.target.value) as Record<string, string>)
                      : undefined
                    onChange({ ...value, context_mapping: parsed })
                  } catch {
                    /* 编辑中允许无效 JSON */
                  }
                }}
                style={{ fontFamily: 'monospace', fontSize: 12 }}
              />
            </Form.Item>
          </>
        )}
      </Form>
    </div>
  )
}

export default WebhookTriggerEditor
