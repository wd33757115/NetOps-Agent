import React, { useState } from 'react'
import { Alert, Button, Descriptions, Space, Tag, Typography, message } from 'antd'
import { EyeOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'

import {
  parserApi,
  type ParserRuleGenerationContextPreview,
} from '../../../services/api'
import WorkflowRunMonitor from '../WorkflowRunMonitor'

interface FocusedRuleAssetGenerationProps {
  seedJobId?: string
  datasetIds: string[]
}

const FocusedRuleAssetGeneration: React.FC<FocusedRuleAssetGenerationProps> = ({
  seedJobId,
  datasetIds,
}) => {
  const [contextPreview, setContextPreview] = useState<ParserRuleGenerationContextPreview | null>(null)
  const [workflowRunId, setWorkflowRunId] = useState<string>()
  const seedQuery = useQuery(
    ['focused-rule-generation-seed', seedJobId],
    () => parserApi.ruleGenerationJobs({ search: seedJobId, limit: 50, offset: 0 }),
    { enabled: Boolean(seedJobId) },
  )
  const seedJob = seedQuery.data?.items.find((item) => item.id === seedJobId)
  const previewMutation = useMutation(
    () => parserApi.previewRuleGenerationContext(seedJobId || '', { dataset_ids: datasetIds }),
    {
      onSuccess: setContextPreview,
      onError: (error: any) => message.error(error?.response?.data?.detail || '生成上下文预览失败'),
    },
  )
  const generationMutation = useMutation(
    () => parserApi.startRuleAssetGeneration(seedJobId || '', {
      context_id: contextPreview?.context_id,
    }),
    {
      onSuccess: (result: any) => {
        const runId = String(result?.run_id || '')
        setWorkflowRunId(runId || undefined)
        message.success(runId ? `已启动规则资产生成：${runId}` : '已启动规则资产生成')
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '启动规则资产生成失败'),
    },
  )

  if (!seedJobId) {
    return <Alert type="warning" showIcon message="缺少规则资产生成种子" />
  }
  if (seedQuery.isError) {
    return <Alert type="error" showIcon message="规则资产生成种子加载失败" />
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="从当前离线样本生成完整规则资产"
        description="先构建不包含原始 CLI 的脱敏上下文，再启动 Contract、Policy、EventRule、验证与回放流程。"
      />
      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="种子任务">
          <Typography.Text copyable>{seedJobId}</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="种子状态">
          <Tag>{seedJob?.status || (seedQuery.isLoading ? '加载中' : '未找到')}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="离线数据集">
          {datasetIds.length ? datasetIds.map((id) => <Tag key={id}>{id}</Tag>) : '未选择'}
        </Descriptions.Item>
      </Descriptions>
      <Space wrap>
        <Button
          icon={<EyeOutlined />}
          loading={previewMutation.isLoading}
          disabled={!seedJob || datasetIds.length === 0}
          onClick={() => previewMutation.mutate()}
        >
          预览脱敏上下文
        </Button>
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          loading={generationMutation.isLoading}
          disabled={!contextPreview?.ready || !contextPreview.context_id}
          onClick={() => generationMutation.mutate()}
        >
          启动完整规则资产生成
        </Button>
      </Space>
      {contextPreview && (
        <Alert
          type={contextPreview.ready ? 'success' : 'warning'}
          showIcon
          message={contextPreview.ready ? '脱敏上下文已就绪' : '生成上下文仍不完整'}
          description={contextPreview.ready
            ? `上下文 ${contextPreview.context_id} 已冻结，Raw CLI 未进入生成输入。`
            : `缺失：${contextPreview.missing_context.join('、') || '未知上下文'}`}
        />
      )}
      {workflowRunId && (
        <div>
          <Typography.Title level={5}>规则资产生成进度</Typography.Title>
          <WorkflowRunMonitor runId={workflowRunId} plain subjectLabel="规则资产生成" />
        </div>
      )}
    </Space>
  )
}

export default FocusedRuleAssetGeneration
