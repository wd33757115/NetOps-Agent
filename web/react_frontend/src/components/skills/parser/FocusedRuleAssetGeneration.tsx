import React, { useState } from 'react'
import { Alert, Button, Descriptions, Space, Tag, Typography, message } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'

import {
  parserApi,
  type CanonicalRuleCandidates,
} from '../../../services/api'
import { asRecord } from './parserWorkspaceModel'

interface FocusedRuleAssetGenerationProps {
  seedJobId?: string
  datasetIds: string[]
}

const FocusedRuleAssetGeneration: React.FC<FocusedRuleAssetGenerationProps> = ({
  seedJobId,
  datasetIds,
}) => {
  const [candidates, setCandidates] = useState<CanonicalRuleCandidates | null>(null)
  const seedQuery = useQuery(
    ['focused-rule-generation-seed', seedJobId],
    () => parserApi.ruleGenerationJobs({ search: seedJobId, limit: 50, offset: 0 }),
    { enabled: Boolean(seedJobId) },
  )
  const seedJob = seedQuery.data?.items.find((item) => item.id === seedJobId)
  const payload = asRecord(seedJob?.payload_json)
  const defaults = asRecord(payload?.workflow_context_defaults)
  const checkItemVersionId = String(
    defaults?.check_item_version_id
      || payload?.check_item_version_id
      || '',
  )
  const contextQuery = useQuery(
    ['canonical-rule-context', checkItemVersionId],
    () => parserApi.checkItemRuleContext(checkItemVersionId),
    { enabled: Boolean(checkItemVersionId) },
  )
  const generationMutation = useMutation(
    () => parserApi.generateCheckItemRuleCandidates(checkItemVersionId),
    {
      onSuccess: (result) => {
        setCandidates(result)
        if (result.success) message.success('已从检查项标准语义生成规则候选')
        else message.warning('标准语义仍有缺项')
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '规则候选生成失败'),
    },
  )
  const draftMutation = useMutation(
    () => parserApi.createCheckItemRuleCandidateDrafts(checkItemVersionId),
    {
      onSuccess: (result) => {
        setCandidates(result)
        message.success('变化策略与事件规则草稿已创建')
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '规则草稿创建失败'),
    },
  )

  if (!seedJobId) {
    return <Alert type="warning" showIcon message="缺少关联检查项" />
  }
  if (seedQuery.isError) {
    return <Alert type="error" showIcon message="关联检查项加载失败" />
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="规则生成已与样本和 TextFSM 解耦"
        description="离线样本只验证 Parser 与 A→B 投影。变化策略和事件规则只读取已发布 CheckItem 的标准语义、状态分组、阈值约束和事件约束。"
      />
      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="关联检查项版本">
          {checkItemVersionId
            ? <Typography.Text copyable>{checkItemVersionId}</Typography.Text>
            : <Tag color="red">种子任务未绑定 CheckItem 版本</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="标准语义状态">
          <Tag color={contextQuery.data ? 'green' : contextQuery.isError ? 'red' : 'default'}>
            {contextQuery.data ? '已就绪' : contextQuery.isLoading ? '加载中' : '未就绪'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="本次规则输入">
          CheckItem 标准语义 B；0 条样本；0 个 Parser 模板
        </Descriptions.Item>
        <Descriptions.Item label="当前离线数据集">
          {datasetIds.length
            ? <>{datasetIds.map((id) => <Tag key={id}>{id}</Tag>)}（仅用于映射测试）</>
            : '未选择，不影响规则生成'}
        </Descriptions.Item>
      </Descriptions>
      {contextQuery.data?.targets.some((target) => (
        Object.keys(target.threshold_references || {}).length > 0
        || Object.keys(target.baseline_comparison || {}).length > 0
      )) && (
        <Alert
          type="info"
          showIcon
          message="动态健康判定"
          description={contextQuery.data.targets
            .filter((target) => Object.keys(target.threshold_references || {}).length > 0)
            .map((target) => {
              const refs = Object.entries(target.threshold_references || {})
                .map(([role, reference]) => `${role}→${String(reference.field_name || '')}`)
                .join('/')
              const ratio = Number(target.baseline_comparison?.max_loss_ratio || 0)
              return `${target.observation_type}：${refs}${ratio ? `；线性功率基线衰减≤${ratio * 100}%` : ''}`
            })
            .join('；')}
        />
      )}
      <Button
        type="primary"
        icon={<PlayCircleOutlined />}
        loading={generationMutation.isLoading}
        disabled={!contextQuery.data || !checkItemVersionId}
        onClick={() => generationMutation.mutate()}
      >
        从标准语义生成规则候选
      </Button>
      {contextQuery.isError && (
        <Alert type="error" showIcon message="无法读取检查项标准语义" />
      )}
      {candidates && (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Alert
            type={candidates.success ? 'success' : 'warning'}
            showIcon
            message={candidates.success
              ? `生成完成：${candidates.change_policies.length} 个变化策略，${candidates.event_rules.length} 条事件规则`
              : '生成被标准语义缺项阻塞'}
            description={candidates.success
              ? `上下文摘要 ${candidates.context_digest.slice(0, 12)}，候选摘要 ${candidates.digest.slice(0, 12)}`
              : candidates.blockers.map((item) => `${item.observation_type}：${item.message}`).join('；')}
          />
          {candidates.success && !candidates.drafts_created && (
            <Button loading={draftMutation.isLoading} onClick={() => draftMutation.mutate()}>
              创建规则草稿
            </Button>
          )}
          {candidates.drafts_created && (
            <Typography.Text type="success">规则草稿已创建，可继续提交审批。</Typography.Text>
          )}
        </Space>
      )}
    </Space>
  )
}

export default FocusedRuleAssetGeneration
