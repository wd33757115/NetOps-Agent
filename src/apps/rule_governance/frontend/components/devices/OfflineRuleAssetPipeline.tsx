import { useRuleTaskNavigate } from '@netops/hooks/useRuleTaskNavigate'
import React from 'react'
import { Alert, Button, Progress, Space, Tooltip, Typography } from 'antd'
import { ArrowRightOutlined, CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { ruleAssetSourceApi } from '@netops/services/api'
import { useIsMobile } from '@netops/hooks/useBreakpoint'

const statusLabel: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  ready: '待处理',
  available: '可选',
  not_available: '暂不可用',
  blocked: '已阻断',
  completed: '已完成',
}

const OfflineRuleAssetPipeline: React.FC<{ jobId: string }> = ({ jobId }) => {
  const navigate = useRuleTaskNavigate()
  const isMobile = useIsMobile()
  const query = useQuery(
    ['offline-rule-asset-pipeline', jobId],
    () => ruleAssetSourceApi.assetPipeline(jobId),
    {
      refetchInterval: (data) => data?.status === 'completed' ? false : 5000,
      keepPreviousData: true,
    },
  )
  const pipeline = query.data
  if (query.isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="样本导入进度加载失败"
        action={<Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>重试</Button>}
      />
    )
  }
  if (!pipeline) return <Progress percent={0} status="active" />
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {isMobile && (
        <Alert
          className="offline-asset-pipeline__notice-bar"
          type="info"
          showIcon
          message={(
            <div className="offline-asset-pipeline__notice">
              <Typography.Text strong>只使用已确认的离线样本</Typography.Text>
              <Typography.Text type="secondary">
                导入完成后，命令会进入检查项目录；Parser、适配器和规则在各自的目录流程中处理。
              </Typography.Text>
            </div>
          )}
        />
      )}
      <section className="offline-asset-pipeline" aria-label="样本导入进度">
        <header className={`offline-asset-pipeline__header${isMobile ? '' : ' is-compact'}`}>
          {isMobile && (
            <div className="offline-asset-pipeline__current">
              <Typography.Text strong>样本导入进度</Typography.Text>
              <Typography.Text type="secondary">
                当前：{pipeline.next_action.label} · {pipeline.next_action.description}
              </Typography.Text>
            </div>
          )}
          <div className="offline-asset-pipeline__progress">
            <div>
              <Typography.Text type="secondary">{isMobile ? '总体进度' : '导入进度'}</Typography.Text>
              <Typography.Text strong>{pipeline.progress_percent}%</Typography.Text>
            </div>
            <Progress percent={pipeline.progress_percent} showInfo={false} size="small" />
          </div>
        </header>
        <ol className="offline-asset-pipeline__stages">
          {pipeline.steps.map((item, index) => (
            <li
              key={`${item.key}-${index}`}
              className={`offline-asset-pipeline__stage is-${item.status}`}
              aria-current={['in_progress', 'ready'].includes(item.status) ? 'step' : undefined}
            >
              <span className="offline-asset-pipeline__stage-marker" aria-hidden="true">
                {item.status === 'completed'
                  ? <CheckOutlined />
                  : item.status === 'blocked'
                    ? <CloseOutlined />
                    : index + 1}
              </span>
              <div className="offline-asset-pipeline__stage-copy">
                <Tooltip title={item.detail} placement="top">
                  <span className="offline-asset-pipeline__stage-label">
                    {item.label}{item.optional ? '（可选）' : ''}
                  </span>
                </Tooltip>
                <span className={`offline-asset-pipeline__step-status is-${item.status}`}>
                  {statusLabel[item.status] || item.status}
                  {isMobile ? ` · ${item.detail}` : ''}
                </span>
              </div>
            </li>
          ))}
        </ol>
        <div className="offline-asset-pipeline__actions">
          {pipeline.next_action.code !== 'completed' && (
            <Button
              type="primary"
              className="offline-asset-pipeline__primary-action"
              icon={<ArrowRightOutlined />}
              onClick={() => navigate(pipeline.next_action.target_url)}
            >
              {pipeline.next_action.label}
            </Button>
          )}
          <Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新进度</Button>
        </div>
      </section>
      <Alert
        showIcon
        type="info"
        message="下一步：关联检查项"
        description="先在待归类检查项中确认关联。TextFSM 与 Projection 使用该检查项的语义和字段要求，随后校对结果并验证发布。"
        action={pipeline.status === 'completed' ? <Button onClick={() => navigate(`/apps/patrol-rules/source-jobs/${encodeURIComponent(jobId)}?tab=classification`)}>关联检查项</Button> : undefined}
      />
    </Space>
  )
}

export default OfflineRuleAssetPipeline
