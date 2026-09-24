import React, { useState } from 'react'
import { Alert, Button, Collapse, Input, Space, Steps, Typography, message } from 'antd'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { commandAutopilotApi } from '@netops/services/commandAutopilot'
import { useRuleTaskNavigate } from '@netops/hooks/useRuleTaskNavigate'
import { useAuth } from '@netops/context/AuthContext'

const stages = [
  ['rules', '检查规则'], ['parser', '解析验证'], ['projection', '标准数据'],
  ['implementation', '设备适配'], ['review', '自动发布'],
]
const labels: Record<string, string> = {
  idle: '自动制作', queued: '等待后台执行', running: '自动制作中', paused: '已暂停，可人工接管',
  needs_attention: '需要处理', needs_authorization: '制作完成，等待确认发布', completed: '已发布，可用于巡检',
}

export default function CommandAutopilotPanel({ jobId, unitId, associated }: {
  jobId: string; unitId: string; associated: boolean
}) {
  const client = useQueryClient()
  const { user } = useAuth()
  const canUseRules = Boolean(user?.app_ids?.includes('rule-governance'))
  const navigate = useRuleTaskNavigate()
  const key = ['command-autopilot', jobId, unitId]
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const query = useQuery(key, () => commandAutopilotApi.get(jobId, unitId), {
    refetchInterval: (data) => data && ['queued', 'running', 'paused'].includes(data.status) ? 3000 : false,
    retry: false,
    onSuccess: () => {
      // The child may start after the control request returns. Keep its cached
      // detail in sync with the existing bounded autopilot polling lifecycle.
      void client.invalidateQueries(['offline-command-asset-unit', jobId, unitId])
      void client.invalidateQueries('parser-job-inline')
    },
  })
  const update = () => {
    void client.invalidateQueries(key)
    void client.invalidateQueries('offline-command-asset-unit')
  }
  const control = useMutation((action: 'start' | 'pause' | 'resume' | 'authorize') => commandAutopilotApi.control(jobId, unitId, action), {
    onSuccess: update,
    onError: (error: Error) => message.error(error.message || '操作失败'),
  })
  const edit = useMutation(async () => {
    const proposal = JSON.parse(text)
    if (!proposal || typeof proposal !== 'object' || Array.isArray(proposal)) throw new Error('字段转换必须是 JSON 对象')
    return commandAutopilotApi.editProjection(jobId, unitId, proposal)
  }, {
    onSuccess: () => { setEditing(false); update(); message.success('修改已保存，继续后重新验证') },
    onError: (error: Error) => message.error(error.message),
  })
  if (query.isError) return <Alert type="warning" message="自动制作状态暂不可用" action={<Button onClick={() => query.refetch()}>重试</Button>} />
  const data = query.data
  if (!data) return null
  const active = ['queued', 'running'].includes(data.status)
  return (
    <section aria-label="命令自动制作" style={{ width: '100%', padding: '12px 0' }}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Typography.Text strong>{labels[data.status]}</Typography.Text>
          <Space wrap>
            {active
              ? <Button loading={control.isLoading} onClick={() => control.mutate('pause')}>暂停并接管</Button>
              : !['completed', 'needs_authorization'].includes(data.status) && <Button type="primary" disabled={!associated || (data.status !== 'idle' && !data.can_edit)} loading={control.isLoading}
                onClick={() => control.mutate(data.status === 'idle' ? 'start' : 'resume')}>
                {data.status === 'idle' ? '开始自动制作' : '继续自动制作'}
              </Button>}
            {data.status === 'needs_authorization' && canUseRules && <Button type="primary" loading={control.isLoading} onClick={() => control.mutate('authorize')}>授权自动发布</Button>}
          </Space>
        </Space>
        <Typography.Text type="secondary">
          {associated ? '按人工确认的检查项制作；用户确认启动或授权后，验证通过即自动发布。可暂停接管。' : '先由人工定义检查项并确认命令关联。'}
        </Typography.Text>
        {data.status !== 'idle' && <Steps size="small" current={Math.max(0, stages.findIndex(([id]) => id === data.stage))}
          status={data.status === 'needs_attention' ? 'error' : data.status === 'completed' ? 'finish' : 'process'} items={stages.map(([, title]) => ({ title }))} />}
        {data.error && <Alert type="warning" showIcon message={data.error === "应用已停用"
          ? "上次 Parser 任务在应用停用或升级时被取消。点击继续自动制作，将保留已有产物并恢复处理。"
          : data.error} />}
        {data.status === 'paused' && !data.can_edit && <Typography.Text type="secondary">正在结束当前步骤；结束前请勿编辑，后台不会继续下一步。</Typography.Text>}
        {data.outputs.parser?.sample_count != null && <Typography.Text type="secondary">解析已验证 {data.outputs.parser.sample_count} 份正样本</Typography.Text>}
        {data.publish_grant && <Typography.Text type="secondary">自动发布授权：{data.publish_grant.by} · 有效至 {new Date(data.publish_grant.expires_at).toLocaleString()}</Typography.Text>}
        <Space wrap>
          {data.outputs.implementation && <Button onClick={() => navigate(`/apps/patrol-rules?workspace=production&subtab=catalog-management&change_id=${encodeURIComponent(data.outputs.implementation!.id)}`)}>查看设备适配草稿</Button>}
          {data.outputs.rules?.bundle_id && <Button onClick={() => navigate(`/apps/patrol-rules?workspace=production&subtab=asset-production-units&action=release&bundle_id=${encodeURIComponent(data.outputs.rules!.bundle_id!)}`)}>查看检查规则</Button>}
          {!data.same_name_values && data.outputs.parser?.template_id && data.status !== 'completed' && <Button disabled={!data.can_edit} onClick={() => {
            setText(JSON.stringify(data.outputs.projection?.proposal || {}, null, 2)); setEditing(true)
          }}>人工编辑字段转换</Button>}
        </Space>
        {editing && <Space direction="vertical" style={{ width: '100%' }}>
          <Input.TextArea aria-label="自动制作字段转换" value={text} onChange={(e) => setText(e.target.value)} rows={12} />
          <Space><Button disabled={!data.can_edit} loading={edit.isLoading} onClick={() => edit.mutate()}>保存修改</Button><Button onClick={() => setEditing(false)}>取消</Button></Space>
        </Space>}
        {data.history.length > 0 && <Collapse ghost items={[{ key: 'history', label: '执行与干预记录', children: (
          <Space direction="vertical">{data.history.slice(-12).map((entry, index) => <Typography.Text key={index} type="secondary">
            {new Date(entry.at).toLocaleString()} · {entry.actor} · {({ start: '启动', resume: '继续', pause: '暂停', authorize: '确认授权', manual_projection: '人工修改', step_completed: '步骤完成', blocked: '等待处理' } as Record<string, string>)[entry.action] || entry.action}
            {entry.error ? `：${entry.error}` : ''}
          </Typography.Text>)}</Space>
        ) }]} />}
      </Space>
    </section>
  )
}
