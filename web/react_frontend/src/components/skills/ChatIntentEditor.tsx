// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react'
import { Alert, Input, Typography, message } from 'antd'
import { workflowApi, ChatIntentPreviewResult } from '../../services/api'
import { GrokToolBtn } from '../ui/GrokUi'

const { TextArea } = Input
const { Text } = Typography

interface ChatIntentEditorProps {
  value: string
  onChange: (v: string) => void
  workflowName?: string
}

const ChatIntentEditor: React.FC<ChatIntentEditorProps> = ({ value, onChange, workflowName }) => {
  const [previewQuery, setPreviewQuery] = useState('根据工单 REQ2025001 生成防火墙策略并进行 LLM 分析')
  const [previewResult, setPreviewResult] = useState<ChatIntentPreviewResult | null>(null)
  const [loading, setLoading] = useState(false)

  const handlePreview = async () => {
    setLoading(true)
    try {
      const res = await workflowApi.previewChatIntent({
        query: previewQuery,
        workflow_name: workflowName,
        chat_intent_yaml: value,
      })
      setPreviewResult(res)
    } catch {
      message.error('预览失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="grok-chat-intent-editor">
      <Text strong>CHAT.intent.yaml</Text>
      <Alert
        type="warning"
        showIcon
        style={{ margin: '8px 0' }}
        message="聊天触发必须在话术中含工单号（如 REQ2025）"
      />
      <TextArea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={14}
        style={{ fontFamily: 'monospace', fontSize: 12 }}
      />

      <div style={{ marginTop: 16 }}>
        <Text strong>匹配预览</Text>
        <Input.TextArea
          value={previewQuery}
          onChange={(e) => setPreviewQuery(e.target.value)}
          rows={2}
          style={{ marginTop: 8 }}
          placeholder="输入测试话术…"
        />
        <GrokToolBtn style={{ marginTop: 8 }} loading={loading} onClick={handlePreview}>
          预览匹配
        </GrokToolBtn>
        {previewResult && (
          <Alert
            style={{ marginTop: 8 }}
            type={previewResult.matched ? 'success' : 'error'}
            message={previewResult.matched ? '匹配成功' : '未匹配'}
            description={
              previewResult.matched ? (
                <>
                  Workflow: {previewResult.workflow}<br />
                  工单: {previewResult.ticket_id}<br />
                  步骤: {previewResult.active_steps}
                  {previewResult.candidates && previewResult.candidates.length > 1 && (
                    <>
                      <br />
                      候选（按匹配度）: {previewResult.candidates.map((c) => c.workflow).join(' → ')}
                    </>
                  )}
                </>
              ) : (
                previewResult.reason
              )
            }
          />
        )}
      </div>
    </div>
  )
}

export default ChatIntentEditor
