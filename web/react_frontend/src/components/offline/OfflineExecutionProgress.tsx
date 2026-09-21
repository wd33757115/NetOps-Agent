import React from 'react'
import { Alert, List, Progress, Space, Tag, Typography } from 'antd'
import type { OfflinePatrolJob } from '@netops/services/api'

const phases: Record<string, string> = {
  queued: '等待执行', quality_gate: '执行前质量检查',
  baseline_ingest: '导入基线周期', baseline_normalization: '归一化基线状态',
  current_ingest: '导入当前周期', current_normalization: '归一化当前状态',
  change_detection: '检测变化', event_building: '构建事件',
  report_generation: '生成报告', report_storage: '保存报告', finishing: '保存执行结果',
  completed: '已完成', failed: '执行失败', cancelled: '已取消', interrupted: '执行中断',
  'import:baseline': '导入基线样本', 'import:current': '导入当前样本', 'import:sample': '导入样本',
}

export default function OfflineExecutionProgress({ job }: { job: OfflinePatrolJob }): React.ReactElement {
  const steps = (job.result?.steps || {}) as Record<string, { success?: boolean }>
  const phase = job.progress.phase || ''
  const failed = job.status === 'failed'
  const active = job.status === 'running' || job.status === 'queued'
  const title = phases[phase] || (active ? '任务处理中' : '尚未开始执行')
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Alert
        type={failed ? 'error' : job.status === 'completed' ? 'success' : 'info'}
        showIcon message={title}
        description={job.error_message || (job.status === 'cancelled'
          ? '已停止后续阶段；当前阶段退出后可以重试，已有证据保留。'
          : active ? '执行进度会自动刷新，关闭页面后可继续查看。' : undefined)}
      />
      <Progress percent={job.progress.percent || 0} status={failed ? 'exception' : active ? 'active' : 'normal'} />
      {job.progress.failed_phase && <Typography.Text type="danger">
        失败阶段：{phases[job.progress.failed_phase] || job.progress.failed_phase}
      </Typography.Text>}
      <List size="small" dataSource={Object.entries(steps)} renderItem={([name, result]) => (
        <List.Item>{phases[name] || name}<Tag color={result.success ? 'green' : 'red'}>
          {result.success ? '完成' : '失败'}
        </Tag></List.Item>
      )} />
    </Space>
  )
}
