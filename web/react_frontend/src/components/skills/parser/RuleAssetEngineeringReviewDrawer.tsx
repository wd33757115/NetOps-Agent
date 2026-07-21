import React, { useEffect, useState } from 'react'
import { Alert, Button, Descriptions, Drawer, Input, Progress, Space, Tabs, Tag, Typography, message } from 'antd'
import { CheckOutlined, ExperimentOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'
import { parserApi, type ParserRuleAssetBundle } from '../../../services/api'
import { shortDigest } from './parserUi'

const jsonText = (value: unknown) => JSON.stringify(value ?? {}, null, 2)

const apiErrorText = (error: any) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  return error?.message || '候选资产复核操作失败'
}

const parseObject = (value: string, label: string) => {
  const parsed = JSON.parse(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label}必须是 JSON 对象`)
  }
  return parsed as Record<string, unknown>
}

const parseObjectArray = (value: string, label: string) => {
  const parsed = JSON.parse(value)
  if (!Array.isArray(parsed) || !parsed.length || parsed.some((item) => !item || typeof item !== 'object' || Array.isArray(item))) {
    throw new Error(`${label}必须是非空 JSON 对象数组`)
  }
  return parsed as Array<Record<string, unknown>>
}

interface Props {
  bundle: ParserRuleAssetBundle | null
  open: boolean
  onClose: () => void
  onChanged: () => void
}

const RuleAssetEngineeringReviewDrawer: React.FC<Props> = ({ bundle, open, onClose, onChanged }) => {
  const [reason, setReason] = useState('')
  const [contract, setContract] = useState('{}')
  const [policies, setPolicies] = useState('[]')
  const [rules, setRules] = useState('[]')
  const [asyncTaskId, setAsyncTaskId] = useState<string | null>(null)
  const query = useQuery(
    ['rule-asset-engineering-draft', bundle?.id],
    () => parserApi.ruleAssetEngineeringDraft(bundle!.id),
    { enabled: open && Boolean(bundle?.id), staleTime: 0 },
  )
  useEffect(() => {
    if (!query.data) return
    setContract(jsonText(query.data.contract))
    setPolicies(jsonText(query.data.change_policies))
    setRules(jsonText(query.data.event_rules))
    setReason('')
    setAsyncTaskId(null)
  }, [query.data])

  const taskQuery = useQuery(
    ['engineering-revision-task', asyncTaskId],
    () => parserApi.engineeringRevisionTask(asyncTaskId!),
    {
      enabled: Boolean(asyncTaskId),
      refetchInterval: (data) => (data?.ready ? false : 1500),
      onSuccess: (data) => {
        if (!data.ready) return
        if (data.error || data.status === 'FAILURE') {
          message.error(data.error || '异步修订失败')
          return
        }
        message.success('修订候选已通过校验和回放，新的资产包已创建')
        setAsyncTaskId(null)
        onChanged()
        onClose()
      },
    },
  )

  const review = useMutation(
    (decision: 'accepted' | 'revision_requested') => parserApi.reviewRuleAssetBundle(
      bundle!.id,
      { decision, reason: reason.trim() },
    ),
    {
      onSuccess: (_result, decision) => {
        message.success(decision === 'accepted' ? '工程师复核已通过，可以进入审批' : '已登记修订要求')
        onChanged()
        onClose()
      },
      onError: (error: any) => message.error(apiErrorText(error)),
    },
  )
  const revise = useMutation(
    async () => {
      const candidateContract = parseObject(contract, 'Observation Contract')
      const candidatePolicies = parseObjectArray(policies, 'Change Policy')
      const candidateRules = parseObjectArray(rules, 'Event Rule')
      return parserApi.reviseRuleAssetBundle(
        bundle!.id,
        {
          contract: candidateContract,
          change_policies: candidatePolicies,
          event_rules: candidateRules,
          reason: reason.trim(),
          idempotency_key: crypto.randomUUID(),
        },
        { asyncMode: true },
      )
    },
    {
      onSuccess: (result: any) => {
        if (result?.task_id) {
          setAsyncTaskId(String(result.task_id))
          message.info('已提交异步校验与回放，请等待进度完成')
          return
        }
        message.success('修订候选已通过校验和回放，新的资产包已创建')
        onChanged()
        onClose()
      },
      onError: (error: any) => message.error(apiErrorText(error)),
    },
  )
  const draft = query.data
  const reasonReady = reason.trim().length >= 5
  const asyncProgress = asyncTaskId
    ? (taskQuery.data?.ready ? 100 : taskQuery.data?.status === 'STARTED' ? 60 : 30)
    : 0
  return (
    <Drawer
      title="工程师复核与候选修订"
      open={open}
      width="min(1040px, 100vw)"
      destroyOnClose
      onClose={onClose}
      extra={draft && (
        <Space wrap>
          <Button
            icon={<CheckOutlined />}
            disabled={!reasonReady || bundle?.status !== 'pending_approval' || Boolean(asyncTaskId)}
            loading={review.isLoading}
            onClick={() => review.mutate('accepted')}
          >
            接受候选
          </Button>
          <Button
            type="primary"
            icon={<ExperimentOutlined />}
            disabled={!reasonReady || !draft.editable || !draft.completeness.complete || Boolean(asyncTaskId)}
            loading={revise.isLoading || Boolean(asyncTaskId)}
            onClick={() => revise.mutate()}
          >
            保存为新版本并重新验证
          </Button>
        </Space>
      )}
    >
      {query.isError && <Alert type="error" showIcon message={apiErrorText(query.error)} />}
      {asyncTaskId && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`异步修订进行中：${taskQuery.data?.status || 'QUEUED'}`}
          description={(
            <Space direction="vertical" style={{ width: '100%' }}>
              <Progress percent={asyncProgress} status={taskQuery.data?.ready ? 'success' : 'active'} />
              <Typography.Text type="secondary">task_id: {asyncTaskId}</Typography.Text>
            </Space>
          )}
        />
      )}
      {draft && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="修订不会覆盖 LLM 候选或生产资产"
            description="平台会创建新的候选 Bundle，重新执行 Contract、Policy、EventRule 确定性校验和原离线 Run 历史回放；只有新版本通过复核与审批后才会生效。"
          />
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="基线 Bundle"><Typography.Text copyable>{draft.bundle_id}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="基线摘要">{shortDigest(draft.base_digest)}</Descriptions.Item>
            <Descriptions.Item label="不可变上下文"><Typography.Text copyable>{draft.context_id || '-'}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="Raw CLI"><Tag color="green">未进入编辑与 LLM 上下文</Tag></Descriptions.Item>
          </Descriptions>
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            <Typography.Text strong>复核或修订理由</Typography.Text>
            <Input.TextArea
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="说明接受依据，或指出字段语义、阈值、恢复条件及事件条件的修订原因（至少 5 个字符）"
            />
          </Space>
          <Tabs items={[
            {
              key: 'contract',
              label: 'Observation Contract',
              children: <Input.TextArea rows={20} value={contract} onChange={(event) => setContract(event.target.value)} style={{ fontFamily: 'Consolas, Monaco, monospace' }} />,
            },
            {
              key: 'policies',
              label: `Change Policy (${draft.change_policies.length})`,
              children: <Input.TextArea rows={20} value={policies} onChange={(event) => setPolicies(event.target.value)} style={{ fontFamily: 'Consolas, Monaco, monospace' }} />,
            },
            {
              key: 'rules',
              label: `Event Rule (${draft.event_rules.length})`,
              children: <Input.TextArea rows={20} value={rules} onChange={(event) => setRules(event.target.value)} style={{ fontFamily: 'Consolas, Monaco, monospace' }} />,
            },
          ]} />
          <Typography.Text type="secondary">
            这里是规则工程师高级编辑入口。保存前执行 JSON Schema 和领域校验；服务端仍以不可变离线上下文及历史回放结果作为发布门禁。
          </Typography.Text>
        </Space>
      )}
    </Drawer>
  )
}

export default RuleAssetEngineeringReviewDrawer
