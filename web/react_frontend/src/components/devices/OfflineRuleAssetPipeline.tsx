import React from 'react'
import { Alert, Button, Divider, Progress, Space, Steps, Tag, Typography } from 'antd'
import { ArrowRightOutlined, DatabaseOutlined, ReloadOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { useNavigate } from 'react-router-dom'
import { ruleAssetSourceApi, type OfflineRuleAssetPipelineStep } from '../../services/api'
import RuleAssetProductionWorkbench from '../skills/parser/RuleAssetProductionWorkbench'

const statusLabel: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  ready: '待处理',
  available: '可选',
  not_available: '暂不可用',
  blocked: '已阻断',
  completed: '已完成',
}

const statusColor: Record<string, string> = {
  in_progress: 'processing',
  ready: 'blue',
  available: 'cyan',
  blocked: 'red',
  completed: 'green',
}

const antStepStatus = (item: OfflineRuleAssetPipelineStep) => {
  if (item.status === 'completed') return 'finish' as const
  if (item.status === 'blocked') return 'error' as const
  if (['in_progress', 'ready'].includes(item.status)) return 'process' as const
  return 'wait' as const
}

const OfflineRuleAssetPipeline: React.FC<{ jobId: string }> = ({ jobId }) => {
  const navigate = useNavigate()
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
        message="资产生产进度加载失败"
        action={<Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>重试</Button>}
      />
    )
  }
  if (!pipeline) return <Progress percent={0} status="active" />
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="规则资产只使用本次样本任务形成的结构化数据"
        description="历史未结构化数据可以另行选择回填；回填必须先 dry-run、人工审批，再写入 Observation，不会直接进入 LLM 上下文。"
      />
      <div className="patrol-history__header">
        <Space direction="vertical" size={2}>
          <Typography.Text strong>任务级资产生产总览</Typography.Text>
          <Typography.Text type="secondary">
            当前：{pipeline.next_action.label} · {pipeline.next_action.description}
          </Typography.Text>
        </Space>
        <Progress type="circle" size={64} percent={pipeline.progress_percent} />
      </div>
      <Steps
        direction="vertical"
        size="small"
        items={pipeline.steps.map((item) => ({
          title: (
            <Space size={6} wrap>
              <span>{item.label}</span>
              {item.optional && <Tag>可选</Tag>}
              <Tag color={statusColor[item.status]}>{statusLabel[item.status] || item.status}</Tag>
            </Space>
          ),
          description: item.detail,
          status: antStepStatus(item),
        }))}
      />
      <Space wrap>
        {pipeline.next_action.code !== 'completed' && (
          <Button
            type="primary"
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
      </Space>
      <Divider style={{ marginBlock: 4 }} />
      <Space direction="vertical" size={2}>
        <Typography.Text strong>按巡检检查项生产规则资产</Typography.Text>
        <Typography.Text type="secondary">
          规则需求由检查项的 AssetRequirementProfile 决定；命令、厂家和适配 Profile 作为兼容实现展开查看。
        </Typography.Text>
      </Space>
      <RuleAssetProductionWorkbench
        embedded
        view="commands"
        onViewChange={() => undefined}
        focusOfflineJobId={jobId}
      />
    </Space>
  )
}

export default OfflineRuleAssetPipeline
