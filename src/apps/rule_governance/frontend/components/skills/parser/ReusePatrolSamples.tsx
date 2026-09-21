import React from 'react'
import { Alert, Button, Space, Typography } from 'antd'
import { useMutation, useQuery } from 'react-query'
import { offlinePatrolApi } from '@netops/services/api'
import { reusePatrolRuleSource } from '@netops/services/patrolRuleSource'
import { useRuleTaskNavigate } from '@netops/hooks/useRuleTaskNavigate'

export default function ReusePatrolSamples({ patrolId }: { patrolId: string }) {
  const navigate = useRuleTaskNavigate()
  const original = useQuery(['rule-source-origin', patrolId], () => offlinePatrolApi.get(patrolId))
  const reuse = useMutation(() => reusePatrolRuleSource(patrolId), {
    onSuccess: id => navigate(`/apps/patrol-rules/source-jobs/${encodeURIComponent(id)}?tab=overview`),
  })
  return <section>
    <Typography.Title level={3}>复用巡检样本</Typography.Title>
    <Typography.Paragraph>保留原文件、采集周期和设备线索；在制作任务中重新确认检查项、验证候选并审批发布。</Typography.Paragraph>
    {original.data && <Space direction="vertical">
      <Typography.Text strong>{original.data.name}</Typography.Text>
      <Typography.Text>{original.data.source_files?.length || 0} 份文件 · {original.data.periods?.map(period => `${period.label} ${new Date(period.observed_at).toLocaleString()}`).join(' / ')}</Typography.Text>
      <Button type="primary" loading={reuse.isLoading} disabled={!original.data.source_files?.length || original.data.status !== 'completed'} onClick={() => reuse.mutate()}>创建或继续制作任务</Button>
    </Space>}
    {original.isLoading && <Typography.Text>读取原巡检范围…</Typography.Text>}
    {(original.isError || reuse.isError) && <Alert type="error" message="样本复用未完成"
      description={(reuse.error as Error)?.message || '无法读取原巡检，请重试'}
      action={<Button onClick={() => original.isError ? original.refetch() : reuse.mutate()}>重试</Button>} />}
  </section>
}
