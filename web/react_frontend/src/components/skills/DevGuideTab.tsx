// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react'
import { Alert, Card, Collapse, Input, Table, Typography } from 'antd'
import { useQuery } from 'react-query'
import { workflowApi } from '../../services/api'
import WorkflowRunMonitor from './WorkflowRunMonitor'
import { GrokToolBtn } from '../ui/GrokUi'

const { Paragraph, Text } = Typography

const DEV_GUIDE_SECTIONS = [
  {
    key: 'structure',
    label: '1. 插件目录结构',
    children: (
      <>
        <pre style={{ fontSize: 12, background: '#f5f5f5', padding: 12, borderRadius: 8 }}>{`src/workflows/itsm/<plugin-name>/
├── WORKFLOW.yaml        # Skill 链 + on_complete + 表达式
├── CHAT.intent.yaml     # 聊天触发（可选）
└── ITSM.webhook.yaml    # Webhook 触发（可选）

对应 Skill：src/skills/<skill-name>/
├── SKILL.md             # entry_script / entry_output / celery_task
└── scripts/run.py       # CLI 入口，stdout 末行 JSON`}</pre>
        <Paragraph type="secondary">
          新增 ITSM 类流程时<strong>无需改</strong> engine.py / Celery 任务实现；保存插件后点「热重载」或重启 Gateway 即可生效。
        </Paragraph>
      </>
    ),
  },
  {
    key: 'expressions',
    label: '2. 表达式与产物',
    children: (
      <>
        <Table
          size="small"
          pagination={false}
          dataSource={[
            { key: '1', expr: '${context.ticket_id}', desc: '运行上下文（聊天/Webhook 注入）' },
            { key: '2', expr: '${run.id}', desc: '当前 Workflow Run ID' },
            { key: '3', expr: '${steps.x.result.*}', desc: '上游步骤 Skill 返回 JSON' },
            { key: '4', expr: '${steps.x.artifacts.config_zip.file_key}', desc: 'MinIO 对象键（下游 Skill 输入）' },
            { key: '5', expr: '${steps.x.artifacts.config_zip.download_url}', desc: 'MinIO 预签名下载 URL' },
          ]}
          columns={[
            { title: '表达式', dataIndex: 'expr', render: (v) => <Text code>{v}</Text> },
            { title: '含义', dataIndex: 'desc' },
          ]}
        />
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 12 }}
          message="Skill 产物契约"
          description={
            <>
              平台会将 Skill stdout JSON 中的 <Text code>artifacts.&lt;key&gt;.download_url</Text> 上传 MinIO 后写入步骤结果。
              常见 key：<Text code>config_zip</Text>（策略包）、<Text code>change_excel</Text>（变更工单）、
              <Text code>analysis_report</Text>（LLM 报告）。任意 artifact 只要带 <Text code>download_url</Text> 即可被下游引用。
            </>
          }
        />
      </>
    ),
  },
  {
    key: 'single-step',
    label: '3. 单步 Workflow（仅 firewall 等）',
    children: (
      <>
        <Paragraph>
          若 Workflow 只有一步（如仅 <Text code>firewall-policy-generator</Text>），聊天触发后不会在对话里等待并返回下载链接，
          产物通过<strong>站内通知</strong>交付。建议开启步骤通知：
        </Paragraph>
        <pre style={{ fontSize: 12, background: '#f5f5f5', padding: 12, borderRadius: 8 }}>{`on_complete:
  message: Workflow 已完成
  notify_each_step: true          # 单步流程建议 true
  notification:
    title: "流程已完成 (\${context.ticket_id})"
    body: "策略配置包已生成，请在步骤完成通知中下载。"
    level: success`}</pre>
        <Paragraph type="secondary">
          完成通知的 <Text code>body</Text> 仅作说明；可点击的下载链接由平台自动从步骤 <Text code>artifacts</Text> 收集，
          写入通知 <Text code>payload.downloads</Text>（见第 5 节），无需在 YAML 里硬编码 URL 字段名。
        </Paragraph>
      </>
    ),
  },
  {
    key: 'mode-a',
    label: '4. 多步链路与 LLM 分析',
    children: (
      <>
        <Paragraph>
          典型防火墙变更链：<Text code>firewall-policy-generator</Text> → <Text code>itsm-change-ticket-writer</Text> →（可选）
          <Text code>itsm-callback</Text>。需要 LLM 解读时<strong>显式增加</strong>第三步
          <Text code>llm-result-analyzer</Text>；向导<strong>不会</strong>默认插入 LLM 步骤。
        </Paragraph>
        <pre style={{ fontSize: 12, background: '#f5f5f5', padding: 12, borderRadius: 8 }}>{`  - name: llm_analysis
    label: LLM 结果分析
    skill: llm-result-analyzer
    inputs:
      prev_result: \${steps.change_ticket.result}
      analysis_prompt: \${context.analysis_prompt}
      analysis_focus: summary

on_complete:
  notify_each_step: true   # 多步长流程推荐`}</pre>
        <Paragraph type="secondary">
          含 LLM 步骤时，<Text code>CHAT.intent.yaml</Text> 可配置 <Text code>require_any_secondary</Text>（如「LLM」「分析」）以区分纯防火墙流程。
        </Paragraph>
      </>
    ),
  },
  {
    key: 'chat',
    label: '5. 聊天触发规则',
    children: (
      <>
        <Alert
          type="warning"
          showIcon
          message="聊天触发需同时满足以下条件"
          description={
            <ul style={{ margin: '8px 0 0', paddingLeft: 20 }}>
              <li>消息匹配 <Text code>CHAT.intent.yaml</Text> 的 <Text code>require_any</Text> / <Text code>require_all</Text> / <Text code>require_any_secondary</Text></li>
              <li>消息含<strong>可识别工单号</strong>（如 <Text code>工单号REQ2025001</Text>、<Text code>REQ001</Text>）</li>
              <li>插件在治理中状态为<strong>已发布</strong>（draft / review 不会在聊天中激活）</li>
            </ul>
          }
        />
        <pre style={{ fontSize: 12, background: '#f5f5f5', padding: 12, borderRadius: 8, marginTop: 12 }}>{`workflow: my-plugin
priority: 50
description: 聊天触发说明

match:
  require_any:
    - 防火墙
    - 策略
  require_any_secondary: []   # 可选；与 LLM 流程区分时使用

response_template: |
  [OK] 已启动 Workflow \`{run_id}\`
  - 工单: {ticket_id}`}</pre>
        <Paragraph type="secondary">
          同 priority 时按 secondary 关键词命中数 tie-break。Webhook 请用 <Text code>ITSM.webhook.yaml</Text>，勿与聊天 Intent 混用 <Text code>auto_if_source</Text>。
          保存向导后请重新生成/保存 <Text code>CHAT.intent.yaml</Text>，避免与 WORKFLOW 步骤不一致。
        </Paragraph>
      </>
    ),
  },
  {
    key: 'notify',
    label: '6. 通知与下载链接',
    children: (
      <>
        <Table
          size="small"
          pagination={false}
          dataSource={[
            { key: '1', field: 'notify_each_step: true', desc: '每步完成发站内通知（单步 firewall / 长流程推荐）' },
            { key: '2', field: 'notify_each_step: false', desc: '默认，仅流程结束或失败时通知' },
            { key: '3', field: 'notify_on_failure: true', desc: '失败时通知（默认开启）' },
            { key: '4', field: 'payload.downloads', desc: '平台自动收集所有 artifact / 结果中的 http(s) 链接' },
          ]}
          columns={[
            { title: '配置 / 字段', dataIndex: 'field', render: (v) => <Text code>{v}</Text> },
            { title: '说明', dataIndex: 'desc' },
          ]}
        />
        <pre style={{ fontSize: 12, background: '#f5f5f5', padding: 12, borderRadius: 8, marginTop: 12 }}>{`# 通知 payload 示例（平台自动生成，无需手写）
{
  "downloads": [
    {
      "key": "config_zip",
      "label": "firewall_policies_REQ001.zip",
      "url": "http://localhost:9000/netops-files/..."
    }
  ]
}`}</pre>
        <Paragraph type="secondary">
          铃铛通知会根据 <Text code>downloads</Text> 渲染可点击链接；正文中的 http(s) URL 也会自动 linkify。
          下载依赖 MinIO 可用（测试环境 Docker 栈中的 netops-minio）。
        </Paragraph>
      </>
    ),
  },
  {
    key: 'checklist',
    label: '7. 发布检查清单',
    children: (
      <ul>
        <li>WORKFLOW.yaml 中 Skill 已在「Skill 管理」页启用</li>
        <li>表达式 <Text code>${'${steps.*}'}</Text> 中的步骤 <Text code>name</Text> 与 YAML 一致</li>
        <li>CHAT.intent.yaml 关键词与业务话术、步骤链（是否含 LLM）对齐</li>
        <li>治理中提交审核并<strong>发布</strong>后，聊天触发才会激活</li>
        <li>单步产物类 Workflow 建议 <Text code>notify_each_step: true</Text></li>
        <li>保存后「热重载」或重启 Gateway；Celery Worker 需运行（防火墙/备份等 Skill）</li>
        <li>向导「试跑 Workflow」或 DevGuide 下方运行监控验证</li>
      </ul>
    ),
  },
]

const DevGuideTab: React.FC = () => {
  const [runIdInput, setRunIdInput] = useState('')
  const [monitorRunId, setMonitorRunId] = useState<string | null>(null)

  const { data: recentRuns = [] } = useQuery(
    'workflow-runs',
    () => workflowApi.listRuns({ limit: 10 }),
    { refetchOnWindowFocus: false },
  )

  return (
    <div className="grok-dev-guide">
      <Alert
        type="info"
        showIcon
        message="Workflow 插件开发指南"
        description="完整文档见 src/workflows/itsm/README.md 与 docs/README.md。以下为编排、聊天触发、通知与产物交付要点；下方可查看最近运行记录。"
        style={{ marginBottom: 16 }}
      />

      <Collapse items={DEV_GUIDE_SECTIONS} defaultActiveKey={['single-step', 'notify']} />

      <Card title="最近 Workflow 运行" style={{ marginTop: 24 }} size="small">
        <Table
          size="small"
          rowKey="run_id"
          pagination={false}
          dataSource={recentRuns}
          columns={[
            { title: 'Run ID', dataIndex: 'run_id', ellipsis: true, width: 200 },
            { title: '模板', dataIndex: 'template_name' },
            { title: '工单', dataIndex: 'ticket_id' },
            { title: '状态', dataIndex: 'status' },
            {
              title: '操作',
              render: (_, row) => (
                <GrokToolBtn onClick={() => setMonitorRunId(row.run_id)}>查看</GrokToolBtn>
              ),
            },
          ]}
        />
      </Card>

      <Card title="按 Run ID 查询" style={{ marginTop: 16 }} size="small">
        <Input.Search
          placeholder="输入 Workflow Run ID"
          value={runIdInput}
          onChange={(e) => setRunIdInput(e.target.value)}
          onSearch={(v) => setMonitorRunId(v.trim() || null)}
          enterButton="查询"
        />
        {monitorRunId && (
          <div style={{ marginTop: 16 }}>
            <WorkflowRunMonitor runId={monitorRunId} embedded onClose={() => setMonitorRunId(null)} />
          </div>
        )}
      </Card>
    </div>
  )
}

export default DevGuideTab
