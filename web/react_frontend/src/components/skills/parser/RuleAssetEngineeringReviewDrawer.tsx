import React, { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Drawer,
  Input,
  Progress,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { CheckOutlined, ExperimentOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'
import { parserApi, type ParserRuleAssetBundle } from '../../../services/api'
import { codePanelStyle, shortDigest } from './parserUi'

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

const safeObject = (value: string) => {
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, any>
      : null
  } catch {
    return null
  }
}

const safeObjectArray = (value: string) => {
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed)
      ? parsed.filter((item) => item && typeof item === 'object' && !Array.isArray(item)) as Array<Record<string, any>>
      : []
  } catch {
    return []
  }
}

const stringValues = (value: unknown) => (
  Array.isArray(value) ? value.map(String).filter(Boolean) : []
)

const ruleObservationType = (rule: Record<string, any>) => (
  stringValues(rule.condition?.observation_types)[0] || '-'
)

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
  const replayBlocked = bundle?.replay_gate?.success === false
  const parsedContract = useMemo(() => safeObject(contract), [contract])
  const parsedPolicies = useMemo(() => safeObjectArray(policies), [policies])
  const parsedRules = useMemo(() => safeObjectArray(rules), [rules])
  const observationTypes = useMemo(() => [...new Set([
    ...parsedPolicies.map((item) => String(item.observation_type || '')).filter(Boolean),
    ...parsedRules.map(ruleObservationType).filter((item) => item !== '-'),
  ])].sort(), [parsedPolicies, parsedRules])
  const contractFields = useMemo(() => {
    const semantics = parsedContract?.state_semantics
    if (!semantics || typeof semantics !== 'object' || Array.isArray(semantics)) return []
    return Object.entries(semantics).map(([field, metadata]) => ({
      key: field,
      field,
      ...(metadata && typeof metadata === 'object' ? metadata as Record<string, unknown> : {}),
    }))
  }, [parsedContract])
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
            disabled={!reasonReady || replayBlocked || bundle?.status !== 'pending_approval' || Boolean(asyncTaskId)}
            loading={review.isLoading}
            onClick={() => review.mutate('accepted')}
          >
            接受候选
          </Button>
          <Button
            type="primary"
            icon={<ExperimentOutlined />}
            disabled={!reasonReady || replayBlocked || !draft.editable || Boolean(asyncTaskId)}
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
          {replayBlocked && (
            <Alert
              type="error"
              showIcon
              message="历史回放没有形成有效 Change 证据"
              description={(bundle?.replay_gate?.errors || bundle?.blockers || []).join('；') || '请导入至少两个可比较周期并完成 State/Change 回放后再复核。'}
            />
          )}
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
            <Descriptions.Item label="观测语义" span={2}>
              <Space wrap>{observationTypes.map((item) => <Tag key={item}>{item}</Tag>)}</Space>
            </Descriptions.Item>
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
              key: 'overview',
              label: '复核摘要',
              children: (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Descriptions bordered size="small" column={2}>
                    <Descriptions.Item label="实体类型">{String(parsedContract?.entity_type || '-')}</Descriptions.Item>
                    <Descriptions.Item label="主键">{stringValues(parsedContract?.primary_keys).join('、') || '-'}</Descriptions.Item>
                    <Descriptions.Item label="字段语义">{contractFields.length}</Descriptions.Item>
                    <Descriptions.Item label="变化策略">{parsedPolicies.length}</Descriptions.Item>
                    <Descriptions.Item label="事件规则">{parsedRules.length}</Descriptions.Item>
                    <Descriptions.Item label="规则目标">{observationTypes.length}</Descriptions.Item>
                  </Descriptions>
                  <Alert
                    type={parsedRules.length > Math.max(12, observationTypes.length * 6) ? 'warning' : 'info'}
                    showIcon
                    message={`本候选包含 ${parsedRules.length} 条事件规则，覆盖 ${observationTypes.length} 个观测语义`}
                    description="请重点核对告警/恢复是否成对、阈值引用是否来自结构化字段、光模块缺失等不适用条件是否已排除。"
                  />
                </Space>
              ),
            },
            {
              key: 'contract',
              label: 'Observation Contract',
              children: (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Table
                    rowKey="field"
                    size="small"
                    pagination={{ pageSize: 10, hideOnSinglePage: true }}
                    dataSource={contractFields}
                    columns={[
                      { title: '字段', dataIndex: 'field', width: 220 },
                      { title: '语义类型', dataIndex: 'semantic_type', width: 130 },
                      { title: 'Observation Type', dataIndex: 'observation_type', width: 250, render: (value) => value || '-' },
                      { title: '比较角色', dataIndex: 'comparison_role', width: 130, render: (value) => value || '-' },
                      { title: '监控', dataIndex: 'monitor', width: 80, render: (value) => value === true ? <Tag color="green">是</Tag> : <Tag>否</Tag> },
                    ]}
                    scroll={{ x: 900 }}
                  />
                  <Collapse items={[{
                    key: 'contract-json',
                    label: '高级 JSON 修订',
                    children: <Input.TextArea rows={18} value={contract} onChange={(event) => setContract(event.target.value)} style={{ fontFamily: 'Consolas, Monaco, monospace' }} />,
                  }]} />
                </Space>
              ),
            },
            {
              key: 'policies',
              label: `Change Policy (${draft.change_policies.length})`,
              children: (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Table
                    rowKey={(row) => String(row.observation_type)}
                    size="small"
                    pagination={false}
                    dataSource={parsedPolicies}
                    columns={[
                      { title: 'Observation Type', dataIndex: 'observation_type', width: 250 },
                      { title: '语义', dataIndex: 'semantic_type', width: 110 },
                      { title: '比较方式', dataIndex: 'compare_mode', width: 190 },
                      { title: '滞回/死区', dataIndex: 'deadband', width: 110, render: (value) => value ?? '-' },
                      { title: '设备阈值引用', render: (_, row) => Object.keys(row.threshold_references || {}).length },
                    ]}
                    expandable={{
                      expandedRowRender: (row) => <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 280 }}>{JSON.stringify(row, null, 2)}</pre>,
                    }}
                    scroll={{ x: 850 }}
                  />
                  <Collapse items={[{
                    key: 'policy-json',
                    label: '高级 JSON 修订',
                    children: <Input.TextArea rows={18} value={policies} onChange={(event) => setPolicies(event.target.value)} style={{ fontFamily: 'Consolas, Monaco, monospace' }} />,
                  }]} />
                </Space>
              ),
            },
            {
              key: 'rules',
              label: `Event Rule (${draft.event_rules.length})`,
              children: (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Table
                    rowKey={(row, index) => String(row.name || row.event_type || index)}
                    size="small"
                    pagination={{ pageSize: 10, showSizeChanger: false }}
                    dataSource={parsedRules}
                    columns={[
                      { title: 'Observation Type', width: 235, render: (_, row) => ruleObservationType(row) },
                      { title: '触发角色', dataIndex: 'trigger_role', width: 130, render: (value) => value || '-' },
                      { title: '事件类型', dataIndex: 'event_type', width: 260 },
                      { title: '级别', dataIndex: 'severity', width: 100, render: (value) => <Tag color={value === 'major' ? 'red' : value === 'warning' ? 'orange' : 'blue'}>{value}</Tag> },
                      { title: '类别', dataIndex: 'event_class', width: 110 },
                    ]}
                    expandable={{
                      expandedRowRender: (row) => <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 320 }}>{JSON.stringify(row, null, 2)}</pre>,
                    }}
                    scroll={{ x: 1000 }}
                  />
                  <Collapse items={[{
                    key: 'rule-json',
                    label: '高级 JSON 修订',
                    children: <Input.TextArea rows={18} value={rules} onChange={(event) => setRules(event.target.value)} style={{ fontFamily: 'Consolas, Monaco, monospace' }} />,
                  }]} />
                </Space>
              ),
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
