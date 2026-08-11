import React from 'react'
import { Alert, Button, Divider, Progress, Space, Tooltip, Typography } from 'antd'
import { ArrowRightOutlined, CheckOutlined, CloseOutlined, DatabaseOutlined, ReloadOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { useNavigate } from 'react-router-dom'
import { ruleAssetSourceApi } from '../../services/api'
import { useIsMobile } from '../../hooks/useBreakpoint'
import OfflineCommandAssetUnits from './OfflineCommandAssetUnits'

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
  const navigate = useNavigate()
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
        message="适配器生产进度加载失败"
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
                历史未结构化数据可另行回填；回填必须先预演和审批，不会直接影响生产巡检。
              </Typography.Text>
            </div>
          )}
        />
      )}
      <section className="offline-asset-pipeline" aria-label="样本任务生产进度">
        <header className={`offline-asset-pipeline__header${isMobile ? '' : ' is-compact'}`}>
          {isMobile && (
            <div className="offline-asset-pipeline__current">
              <Typography.Text strong>样本任务生产进度</Typography.Text>
              <Typography.Text type="secondary">
                当前：{pipeline.next_action.label} · {pipeline.next_action.description}
              </Typography.Text>
            </div>
          )}
          <div className="offline-asset-pipeline__progress">
            <div>
              <Typography.Text type="secondary">{isMobile ? '总体进度' : '生产进度'}</Typography.Text>
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
          {pipeline.secondary_actions.map((action) => (
            <Button
              key={action.code}
              icon={<DatabaseOutlined />}
              onClick={() => navigate(action.target_url)}
            >
              {action.label}
            </Button>
          ))}
          <Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新进度</Button>
        </div>
      </section>
      {isMobile && (
        <>
          <Divider style={{ marginBlock: 4 }} />
          <Space direction="vertical" size={2}>
            <Typography.Text strong>按命令处理样本资产</Typography.Text>
            <Typography.Text type="secondary">
              每条命令使用本任务的样本完成 Parser 生成与验证，再绑定检查项和命令适配器。
            </Typography.Text>
          </Space>
        </>
      )}
      <OfflineCommandAssetUnits jobId={jobId} />
    </Space>
  )
}

export default OfflineRuleAssetPipeline
