import StatusNotice from '@netops/components/common/StatusNotice'
import React, { useRef, useEffect, useMemo, useState } from 'react'
import {
  
  Button,
  Descriptions,
  Divider,
  Input,
  Space,
  Steps,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  CheckCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'

import { useAuth } from '@netops/context/AuthContext'
import {
  deviceApi,
  offlinePatrolApi,
  type OfflinePatrolJob,
  type PatrolCurrentAuthority,
} from '@netops/services/api'

const ACTIVE_AUTHORITY_STATUSES = new Set(['approved', 'attesting', 'dispatching'])
const READINESS_AUTHORITY_STATUSES = new Set([
  'ready_for_attestation',
  'approved',
  'attesting',
])

const statusLabel: Record<string, string> = {
  not_requested: '尚未申请',
  blocked: '材料不完整',
  ready_for_attestation: '待独立复核',
  approved: '复核通过，等待计划',
  attesting: '正在冻结生产计划',
  dispatching: '生产巡检执行中',
  completed: '执行已结束',
  failed: '执行失败',
  revoked: '资格已撤销',
  expired: '申请已过期',
}

const statusColor = (status: string) => {
  if (status === 'completed') return 'green'
  if (status === 'failed' || status === 'blocked' || status === 'revoked') return 'red'
  if (status === 'expired') return 'orange'
  if (ACTIVE_AUTHORITY_STATUSES.has(status) || status === 'ready_for_attestation') return 'gold'
  return 'default'
}

const errorMessage = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail
    || error?.response?.data?.error
    || error?.message
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    return String(detail.message || detail.code || fallback)
  }
  return fallback
}

const authorityStep = (authority?: PatrolCurrentAuthority) => {
  const status = authority?.status || 'not_requested'
  if (status === 'completed') return 4
  if (status === 'dispatching') return 3
  if (status === 'approved' || status === 'attesting') return 2
  if (status === 'ready_for_attestation') return 1
  return 0
}

const OfflineCurrentAuthorityPanel: React.FC<{
  job: OfflinePatrolJob
  onOpenSources?: () => void
  onOpenRuntimeAssets?: () => void
  onStartReplacement?: () => void
}> = ({
  job,
  onOpenSources,
  onOpenRuntimeAssets,
  onStartReplacement,
}) => {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [statement, setStatement] = useState(
    '本次提交已覆盖所选设备、成功命令输出、失败命令和未采集项，文件及设备清单与现场采集记录一致。',
  )
  const [collectionTool, setCollectionTool] = useState('netops-offline-collector')
  const [collectorIdentity, setCollectorIdentity] = useState('')
  const [attestationReason, setAttestationReason] = useState('')
  const [revocationReason, setRevocationReason] = useState('')
  const canAttest = Boolean(user)

  const query = useQuery(
    ['offline-current-authority', job.id],
    () => offlinePatrolApi.currentAuthority(job.id),
    {
      enabled: job.report_intent === 'current_attestation',
      refetchInterval: (value) => (
        value && ACTIVE_AUTHORITY_STATUSES.has(value.status) ? 3000 : false
      ),
    },
  )
  const authority = query.data
  const readinessQuery = useQuery(
    ['patrol-production-readiness', 'patrol-attested-offline-current', 'workflow'],
    () => deviceApi.patrolReadiness({
      workflow_name: 'patrol-attested-offline-current',
      launch_mode: 'workflow',
    }),
    {
      enabled: job.report_intent === 'current_attestation'
        && READINESS_AUTHORITY_STATUSES.has(authority?.status || ''),
      refetchInterval: (value) => (
        value && !value.can_launch ? 5000 : false
      ),
    },
  )
  const platformReadiness = readinessQuery.data
  const platformReady = Boolean(platformReadiness?.can_launch)

  const initializedProof = useRef<string>()
  useEffect(() => {
    if (initializedProof.current === authority?.request_id) return
    const proof = authority?.proof
    if (!proof) return
    initializedProof.current = authority?.request_id
    if (proof.completeness_statement) setStatement(proof.completeness_statement)
    if (proof.collection_tool) setCollectionTool(proof.collection_tool)
    if (proof.collector_identity) setCollectorIdentity(proof.collector_identity)
  }, [authority?.request_id, authority?.proof])

  const refresh = async () => {
    await queryClient.invalidateQueries(['offline-current-authority', job.id])
    await query.refetch()
  }

  const prepare = useMutation(() => offlinePatrolApi.prepareCurrentAuthority(
    job.id,
    {
      completeness_statement: statement.trim(),
      completeness_evidence_refs: [
        'source_manifest',
        'identity_binding_manifest',
        'current_run_snapshot_manifest',
        'offline_job_result_ledger',
      ],
      observed_at_evidence_refs: [
        'current_period_observed_at',
        'snapshot_observed_at_set',
      ],
      collection_tool: collectionTool.trim(),
      collector_identity: collectorIdentity.trim(),
    },
    crypto.randomUUID(),
  ), {
    onSuccess: async (result) => {
      message.success(result.status === 'blocked' ? '材料核验完成，仍有阻断项' : '证明请求已创建，等待独立核验')
      await refresh()
    },
    onError: (error: any) => message.error(errorMessage(error, '创建现状证明失败')),
  })

  const attest = useMutation(() => offlinePatrolApi.attestCurrentAuthority(
    job.id,
    {
      request_digest: String(authority?.request_digest || ''),
      reason: attestationReason.trim(),
    },
    crypto.randomUUID(),
  ), {
    onSuccess: async () => {
      setAttestationReason('')
      message.success('独立复核已通过，平台将冻结生产计划并自动调度')
      await refresh()
    },
    onError: (error: any) => message.error(errorMessage(error, '独立复核失败')),
  })

  const revoke = useMutation(() => offlinePatrolApi.revokeCurrentAuthority(
    job.id,
    revocationReason.trim(),
    crypto.randomUUID(),
  ), {
    onSuccess: async () => {
      setRevocationReason('')
      message.success('当前结论资格已撤销')
      await refresh()
    },
    onError: (error: any) => message.error(errorMessage(error, '撤销资格失败')),
  })

  const workflowCounts = useMemo(
    () => Object.entries(authority?.production_batch?.execution_status_counts || authority?.production_batch?.workflow_status_counts || {}),
    [authority?.production_batch?.execution_status_counts, authority?.production_batch?.workflow_status_counts],
  )
  const sameActor = Boolean(
    authority?.requested_by_principal_id
    && String(authority.requested_by_principal_id) === String(user?.id),
  )
  const canPrepare = job.status === 'completed'
    && ['not_requested', 'blocked', 'failed', 'expired', 'revoked'].includes(
      authority?.status || 'not_requested',
    )
  const currentSources = (job.source_files || []).filter(
    (item) => item.period_role === 'current',
  )
  const currentBindings = (job.device_bindings || []).filter(
    (item) => item.period_role === 'current',
  )
  const sourceFileCount = authority?.source_file_count
    ?? Math.max(currentSources.length, Number(job.preview_totals?.file_count || 0))
  const deviceCount = authority?.device_count
    ?? Math.max(currentBindings.length, Number(job.preview_totals?.device_count || 0))
  const selectedCheckItemCount = authority?.selected_check_item_count
  const unconfirmedBindingCount = currentBindings.filter(
    (item) => item.match_status !== 'confirmed' || !item.inventory_device_id,
  ).length
  const showIdentityPreflight = authority?.status === 'not_requested'
    && unconfirmedBindingCount > 0
  const dispatchErrorCode = authority?.production_batch?.dispatch_error_code
    || authority?.production_batch?.dispatch_error?.split(':', 1)[0]
  const runtimeAssetBlocked = dispatchErrorCode === 'authority_production_plan_incomplete'

  const blockerAction = (code: string, target?: string) => {
    if (target === 'sources' || code === 'identity_binding_unconfirmed') {
      return onOpenSources ? (
        <Button size="small" onClick={onOpenSources}>处理设备绑定</Button>
      ) : null
    }
    if (target === 'new_current_job') {
      return onStartReplacement ? (
        <Button size="small" onClick={onStartReplacement}>新建当前任务</Button>
      ) : null
    }
    return null
  }

  if (job.report_intent !== 'current_attestation') {
    return (
      <StatusNotice
        type="info"
        showIcon
        message="该任务仅用于历史分析"
        description="历史任务可以形成变化与风险证据，但不能直接描述当前网络现状。"
      />
    )
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <StatusNotice
        type="warning"
        showIcon
        message="当前现状证明不是把历史任务改名为生产巡检"
        description="平台会冻结采集时间、原始文件、设备身份、应巡范围和成功/失败账本；独立复核后另建 production_current 批次，原离线任务保持不变。"
      />

      <Steps
        current={authorityStep(authority)}
        size="small"
        items={[
          { title: '证据核验', description: '文件、时间、设备与完整性' },
          { title: '独立复核', description: '导入人与复核人分离' },
          { title: '冻结计划', description: '检查项与 RuntimeBundle' },
          { title: '生产执行', description: '独立 Workflow 与事实空间' },
          { title: '报告门禁', description: '结论、证据与签发' },
        ]}
      />

      {query.isLoading ? (
        <StatusNotice type="info" showIcon message="正在读取现状证明状态" />
      ) : query.isError ? (
        <StatusNotice
          type="error"
          showIcon
          message="无法读取现状证明状态"
          description={errorMessage(query.error, '请检查服务状态')}
          action={<Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>重试</Button>}
        />
      ) : (
        <Descriptions bordered size="small" column={2}>
          <Descriptions.Item label="当前状态">
            <Tag color={statusColor(authority?.status || 'not_requested')}>
              {statusLabel[authority?.status || 'not_requested'] || authority?.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="证据范围">
            {deviceCount} 台设备 · {selectedCheckItemCount == null
              ? '检查项待核验'
              : `${selectedCheckItemCount} 个检查项`} · {sourceFileCount} 个文件
          </Descriptions.Item>
          <Descriptions.Item label="申请人">
            {authority?.requested_by_display || '-'}
          </Descriptions.Item>
          <Descriptions.Item label="有效期">
            {authority?.expires_at ? new Date(authority.expires_at).toLocaleString() : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="生产批次">
            <Typography.Text copyable code>
              {authority?.production_batch?.patrol_batch_id || '-'}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="执行情况">
            <Space wrap>
              {workflowCounts.length
                ? workflowCounts.map(([key, value]) => <Tag key={key}>{key} {value}</Tag>)
                : <Typography.Text type="secondary">尚未创建设备 Workflow</Typography.Text>}
            </Space>
          </Descriptions.Item>
        </Descriptions>
      )}

      {showIdentityPreflight && (
        <StatusNotice
          type="warning"
          showIcon
          message={`${unconfirmedBindingCount} 台设备尚未绑定 Inventory`}
          description="当前现状证明不接受外部设备身份。请先确认资产匹配；没有候选时先在设备资产中新增设备，再返回本任务完成绑定。"
          action={(
            <Space direction="vertical" size={4}>
              {onOpenSources && <Button size="small" onClick={onOpenSources}>打开文件与设备</Button>}
            </Space>
          )}
        />
      )}

      {(authority?.blockers || []).map((item) => (
        <StatusNotice
          key={item.code}
          type="error"
          showIcon
          message={item.message}
          description={<Typography.Text code>{item.code}</Typography.Text>}
          action={blockerAction(item.code, item.remediation?.target)}
        />
      ))}
      {authority?.production_batch?.dispatch_error && (
        <StatusNotice
          type="error"
          showIcon
          message={runtimeAssetBlocked ? '当前范围没有可执行的生产规则资产' : '生产计划或调度失败'}
          description={runtimeAssetBlocked
            ? '平台已停止调度，没有把“未执行”显示为“已完成”。请先补齐并激活对应检查项的 RuntimeBundle，再重新提交当前现状证明。'
            : authority.production_batch.dispatch_error}
          action={runtimeAssetBlocked && onOpenRuntimeAssets
            ? <Button size="small" onClick={onOpenRuntimeAssets}>前往规则资产控制面</Button>
            : undefined}
        />
      )}

      {canPrepare && (
        <>
          <Divider orientation="left">提交证明材料</Divider>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Input.TextArea
              rows={3}
              value={statement}
              onChange={(event) => setStatement(event.target.value)}
              placeholder="说明设备、成功输出、失败记录和未采集项如何被完整核算"
            />
            <Space.Compact block>
              <Input
                value={collectionTool}
                onChange={(event) => setCollectionTool(event.target.value)}
                placeholder="采集工具及版本"
              />
              <Input
                value={collectorIdentity}
                onChange={(event) => setCollectorIdentity(event.target.value)}
                placeholder="现场采集人或采集主体"
              />
            </Space.Compact>
            <Typography.Text type="secondary">
              平台会自动冻结源文件清单、设备绑定、Snapshot、命令失败账本和应巡范围，无需手工填写内部 ID。
            </Typography.Text>
            <Button
              icon={<SafetyCertificateOutlined />}
              loading={prepare.isLoading}
              disabled={statement.trim().length < 10 || !collectionTool.trim() || !collectorIdentity.trim()}
              onClick={() => prepare.mutate()}
            >
              核验证据并提交复核
            </Button>
          </Space>
        </>
      )}

      {READINESS_AUTHORITY_STATUSES.has(authority?.status || '') && (
        <>
          <Divider orientation="left">生产运行环境</Divider>
          {readinessQuery.isLoading ? (
            <StatusNotice
              type="info"
              showIcon
              message="正在检查生产巡检运行环境"
            />
          ) : readinessQuery.isError ? (
            <StatusNotice
              type="error"
              showIcon
              message="无法确认生产巡检运行环境"
              description="运行环境状态未知时不能进入生产执行，请重新检查。"
              action={(
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  onClick={() => readinessQuery.refetch()}
                >
                  重新检查
                </Button>
              )}
            />
          ) : platformReadiness && !platformReady ? (
            <StatusNotice
              type="error"
              showIcon
              message="生产巡检运行环境未就绪"
              description={(
                <Space direction="vertical" size={4}>
                  {(platformReadiness.blockers || []).map((item) => (
                    <Typography.Text key={item.failure_code || item.id}>
                      {item.name}：{item.message}。{item.remediation}
                    </Typography.Text>
                  ))}
                </Space>
              )}
              action={(
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  onClick={() => readinessQuery.refetch()}
                >
                  重新检查运行环境
                </Button>
              )}
            />
          ) : platformReadiness ? (
            <StatusNotice
              type="success"
              showIcon
              message="生产巡检运行环境已就绪"
              description="Worker、Beat、数据库、运行存储和交付存储均已通过生产准入检查。"
            />
          ) : null}
        </>
      )}

      {authority?.status === 'ready_for_attestation' && (
        <>
          <Divider orientation="left">独立核验</Divider>
          {sameActor ? (
            <StatusNotice
              type="info"
              showIcon
              message="等待另一位独立核验人复核"
              description="同一身份不能同时提交材料和确认当前结论资格。"
            />
          ) : canAttest ? (
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Input.TextArea
                rows={2}
                value={attestationReason}
                onChange={(event) => setAttestationReason(event.target.value)}
                placeholder="记录你核对了哪些证据，以及同意进入生产计划的依据"
              />
              <Button
                icon={<CheckCircleOutlined />}
                loading={attest.isLoading}
                disabled={attestationReason.trim().length < 5 || !platformReady}
                onClick={() => attest.mutate()}
              >
                确认材料并进入生产执行
              </Button>
            </Space>
          ) : (
            <StatusNotice type="info" showIcon message="等待独立核验" />
          )}
        </>
      )}

      {canAttest && authority?.qualification?.status === 'approved' && (
        <>
          <Divider orientation="left">资格控制</Divider>
          <Space.Compact block>
            <Input
              value={revocationReason}
              onChange={(event) => setRevocationReason(event.target.value)}
              placeholder="仅在证据失效或身份/范围有误时填写撤销原因"
            />
            <Button
              danger
              icon={<StopOutlined />}
              loading={revoke.isLoading}
              disabled={revocationReason.trim().length < 5}
              onClick={() => revoke.mutate()}
            >
              撤销当前资格
            </Button>
          </Space.Compact>
        </>
      )}
    </Space>
  )
}

export default OfflineCurrentAuthorityPanel
