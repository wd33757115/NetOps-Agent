import React, { useMemo } from 'react'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
} from '@ant-design/icons'
import { Alert, Progress, Tag, Typography } from 'antd'
import type { PatrolBatchResultSummary, PatrolBatchSummary } from '@netops/services/api'

interface BatchCounts {
  total: number
  completed: number
  running: number
  pending: number
  failed: number
  cancelled: number
}

interface PatrolBatchPipelineProps {
  batch?: PatrolBatchSummary
  counts: BatchCounts
  summary?: PatrolBatchResultSummary
}

type DisplayStage = NonNullable<PatrolBatchResultSummary['pipeline']>['stages'][number] & {
  synthetic?: boolean
}

const statusMeta = (status: string) => {
  if (status === 'completed') return { color: 'success', label: '完成', icon: <CheckCircleOutlined /> }
  if (status === 'running') return { color: 'processing', label: '运行中', icon: <LoadingOutlined /> }
  if (status === 'failed') return { color: 'error', label: '失败', icon: <ExclamationCircleOutlined /> }
  if (status === 'degraded') return { color: 'warning', label: '部分失败', icon: <ExclamationCircleOutlined /> }
  if (status === 'cancelled') return { color: 'default', label: '已取消', icon: <ClockCircleOutlined /> }
  if (status === 'not_started') return { color: 'default', label: '未开始', icon: <ClockCircleOutlined /> }
  return { color: 'default', label: '等待中', icon: <ClockCircleOutlined /> }
}

const durationText = (value?: number | null) => {
  if (value === null || value === undefined) return '-'
  if (value < 1) return `${Math.round(value * 1000)}ms`
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)}s`
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`
}

const PatrolBatchPipeline: React.FC<PatrolBatchPipelineProps> = ({ batch, counts, summary }) => {
  const stages = useMemo<DisplayStage[]>(() => {
    const target = batch?.target_count ?? counts.total
    const launched = batch?.launched_count ?? counts.total
    const skipped = batch?.skipped_count ?? 0
    const preparationTerminal = launched + skipped
    const preparation: DisplayStage = {
      key: 'batch:preparation',
      step_index: -1,
      step_name: 'batch_preparation',
      skill_name: 'batch-coordinator',
      label: '范围确认与分发',
      description: '冻结目标设备、完成预检并分发设备任务',
      status: preparationTerminal < target ? 'running' : skipped ? 'degraded' : 'completed',
      total: target,
      status_counts: { launched, skipped },
      completed_count: launched,
      failed_count: skipped,
      active_count: 0,
      pending_count: Math.max(target - preparationTerminal, 0),
      completion_rate: target ? preparationTerminal / target : 0,
      duration: { sample_count: 0 },
      error_examples: [],
      synthetic: true,
    }
    const runtime = summary?.pipeline?.stages || []
    const terminal = counts.completed + counts.failed + counts.cancelled
    const delivery: DisplayStage = {
      key: 'batch:delivery',
      step_index: runtime.length,
      step_name: 'batch_delivery',
      skill_name: 'batch-coordinator',
      label: '汇总与交付',
      description: '汇总设备结果、事件和最终报告',
      status: counts.total === 0 || terminal === 0
        ? 'not_started'
        : counts.running || counts.pending
          ? 'running'
          : counts.failed || counts.cancelled
            ? 'degraded'
            : terminal === counts.total
              ? 'completed'
              : 'pending',
      total: counts.total,
      status_counts: {},
      completed_count: counts.completed,
      failed_count: counts.failed + counts.cancelled,
      active_count: counts.running,
      pending_count: counts.pending,
      completion_rate: counts.total ? terminal / counts.total : 0,
      duration: { sample_count: 0 },
      error_examples: [],
      synthetic: true,
    }
    return [preparation, ...runtime, delivery]
  }, [batch, counts, summary?.pipeline?.stages])

  return (
    <section className="patrol-pipeline" aria-label="巡检任务流程">
      <div className="patrol-pipeline__header">
        <div>
          <Typography.Text strong>巡检任务流程</Typography.Text>
          <Typography.Paragraph type="secondary">
            批次内设备并行执行，每台设备依次完成下列阶段；耗时数据用于定位流程瓶颈。
          </Typography.Paragraph>
        </div>
        {summary?.pipeline?.bottleneck_stage_label && (
          <Tag color="warning">当前耗时瓶颈：{summary.pipeline.bottleneck_stage_label}</Tag>
        )}
      </div>

      <div
        className="patrol-pipeline__rail"
        style={{ '--pipeline-stage-count': stages.length } as React.CSSProperties}
      >
        {stages.map((stage, index) => {
          const meta = statusMeta(stage.status)
          const percent = Math.round(Math.max(0, Math.min(1, stage.completion_rate || 0)) * 100)
          const isBottleneck = stage.key === summary?.pipeline?.bottleneck_stage_key
          return (
            <div
              className={`patrol-pipeline__stage${isBottleneck ? ' is-bottleneck' : ''}`}
              key={stage.key}
            >
              <div className="patrol-pipeline__stage-head">
                <span className={`patrol-pipeline__stage-icon is-${stage.status}`}>{meta.icon}</span>
                <span className="patrol-pipeline__stage-index">{index + 1}</span>
                <Typography.Text strong>{stage.label}</Typography.Text>
              </div>
              <Typography.Text type="secondary" className="patrol-pipeline__stage-desc">
                {stage.description}
              </Typography.Text>
              <div className="patrol-pipeline__progress-row">
                <Progress
                  percent={percent}
                  showInfo={false}
                  size="small"
                  status={stage.status === 'failed' || stage.status === 'degraded' ? 'exception' : 'normal'}
                />
                <Tag color={meta.color}>{meta.label}</Tag>
              </div>
              <div className="patrol-pipeline__counts">
                <span>完成 <strong>{stage.completed_count}</strong></span>
                <span>运行 <strong>{stage.active_count}</strong></span>
                <span>等待 <strong>{stage.pending_count}</strong></span>
                {Boolean(stage.cancelled_count) && <span>取消 <strong>{stage.cancelled_count}</strong></span>}
                <span className={stage.failed_count ? 'is-error' : ''}>失败 <strong>{stage.failed_count}</strong></span>
              </div>
              {!stage.synthetic && (
                <div className="patrol-pipeline__latency">
                  <span>平均 {durationText(stage.duration.average_seconds)}</span>
                  <span>P95 {durationText(stage.duration.p95_seconds)}</span>
                  <span>最大 {durationText(stage.duration.max_seconds)}</span>
                </div>
              )}
              {stage.error_examples[0] && (
                <Alert
                  type="error"
                  showIcon={false}
                  message={`${stage.error_examples[0].device_name}：${stage.error_examples[0].message}`}
                />
              )}
            </div>
          )
        })}
      </div>

      {!summary?.pipeline?.stages?.length && (
        <Alert
          type={summary?.run_count === 0 ? 'warning' : 'info'}
          showIcon
          message={summary?.run_count === 0
            ? '批次已建立，但未产生设备巡检执行记录；请在设备执行明细中检查预检、分发状态和失败原因。'
            : '该批次暂无步骤级统计，刷新后将显示各阶段设备数与耗时。'}
        />
      )}
    </section>
  )
}

export default PatrolBatchPipeline
