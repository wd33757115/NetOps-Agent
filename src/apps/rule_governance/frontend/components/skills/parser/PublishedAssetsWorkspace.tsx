import React, { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { transferHref } from '../../../services/rulePackages'
import {
  Alert, Button, Checkbox, Descriptions, Drawer, Empty, Grid, Input, List, Modal, Pagination, Select, Space, message,
  Table, Tag, Typography,
} from 'antd'
import {
  EyeOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'

import {
  parserApi,
  type ParserChangePolicy,
  type ParserEventRule,
  type ParserPage,
  type ParserTemplateItem,
} from '@netops/services/api'
import ParserTemplateApprovalDrawer from './ParserTemplateApprovalDrawer'

type PublishedAssetKind = 'parser' | 'policy' | 'event'
type PublishedAsset =
  | ParserTemplateItem
  | ParserChangePolicy
  | ParserEventRule

interface PublishedAssetsWorkspaceProps {
  focusKind?: string | null
  focusId?: string | null
}

const kindMeta: Record<PublishedAssetKind, { label: string; shortLabel: string }> = {
  parser: { label: 'Parser 模板', shortLabel: 'Parser' },
  policy: { label: '变化策略', shortLabel: 'Policy' },
  event: { label: '事件规则', shortLabel: 'Event' },
}

const focusKindMap: Record<string, PublishedAssetKind> = {
  'parser-template': 'parser',
  snapshot: 'parser',
  'change-policy': 'policy',
  change: 'policy',
  'event-rule': 'event',
}

const pageForKind = async (
  kind: PublishedAssetKind,
  params: { search?: string; limit: number; offset: number; status?: string },
): Promise<ParserPage<PublishedAsset>> => {
  const request = { status: 'active', ...params }
  if (kind === 'parser') return parserApi.templates(request) as Promise<ParserPage<PublishedAsset>>
  if (kind === 'policy') return parserApi.changePolicies(request) as Promise<ParserPage<PublishedAsset>>
  return parserApi.eventRules(request) as Promise<ParserPage<PublishedAsset>>
}

const assetId = (kind: PublishedAssetKind, asset: PublishedAsset): string => {
  if (kind === 'parser') return (asset as ParserTemplateItem).id
  if (kind === 'policy') return (asset as ParserChangePolicy).version_id
  return (asset as ParserEventRule).version_id
}

const assetIdentity = (kind: PublishedAssetKind, asset: PublishedAsset) => {
  if (kind === 'parser') {
    const item = asset as ParserTemplateItem
    return {
      title: item.canonical_command,
      scope: `${item.vendor} / ${item.profile}`,
      version: `v${item.version}`,
      source: item.source_job_id || '-',
      activatedAt: item.created_at,
    }
  }
  if (kind === 'policy') {
    const item = asset as ParserChangePolicy
    return {
      title: item.policy_name,
      scope: `${item.observation_type} / ${item.compare_mode}`,
      version: `v${item.version}`,
      source: item.source_job_id || '-',
      activatedAt: item.activated_at || item.updated_at || item.created_at,
    }
  }
  const item = asset as ParserEventRule
  return {
    title: item.rule_name,
    scope: `${item.event_type} / ${item.severity}`,
    version: `v${item.version}`,
    source: item.source_job_id || '-',
    activatedAt: item.activated_at || item.created_at,
  }
}

const validationLabel = (kind: PublishedAssetKind, asset: PublishedAsset) => {
  if (kind === 'parser') {
    const report = (asset as ParserTemplateItem).validation_json || {}
    const passed = Number(report.passed_samples || report.passed || 0)
    const total = Number(report.sample_count || report.total_samples || 0)
    return total > 0 ? `${passed}/${total} 样本通过` : '已通过发布门禁'
  }
  const report = kind === 'policy'
    ? (asset as ParserChangePolicy).validation_report
    : (asset as ParserEventRule).validation_report
  const collisionCount = Number(report?.collision_count || 0)
  return collisionCount > 0 ? `${collisionCount} 个碰撞` : '验证与回放通过'
}

const formatTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

const PublishedAssetsWorkspace: React.FC<PublishedAssetsWorkspaceProps> = ({
  focusKind,
  focusId,
}) => {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const queryClient = useQueryClient()
  const initialKind = focusKindMap[String(focusKind || '')] || 'parser'
  const transferKinds = { parser: 'parser', policy: 'policy_version', event: 'event_version' }
  const [kind, setKind] = useState<PublishedAssetKind>(initialKind)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<PublishedAsset>()
  const [exportSelection, setExportSelection] = useState<React.Key[]>([])
  const [status, setStatus] = useState<'active' | 'deprecated'>('active')
  const [operation, setOperation] = useState<{ kind: PublishedAssetKind; action: 'deprecate' | 'delete'; ids: string[] }>()
  const [operationReason, setOperationReason] = useState('')
  const [failures, setFailures] = useState<Array<{ id: string; error?: string }>>([])
  const actionMutation = useMutation(
    (request: NonNullable<typeof operation>) => parserApi.publishedAssetAction({ ...request, reason: operationReason }),
    {
      onSuccess: async (result, request) => {
        const failed = result.results.filter(item => !item.success)
        setFailures(failed)
        setExportSelection(failed.map(item => item.id))
        setOperation(undefined)
        setOperationReason('')
        setSelected(undefined)
        setPage(1)
        if (result.failed) message.warning(`${result.succeeded} 项成功，${result.failed} 项失败，可重试失败项`)
        else message.success(`已${request.action === 'delete' ? '删除' : '停用'} ${result.succeeded} 项资产`)
        await Promise.all([
          queryClient.invalidateQueries('published-assets'),
          queryClient.invalidateQueries('published-assets-count'),
          queryClient.invalidateQueries('parser-templates'),
          queryClient.invalidateQueries('check-item-runtime-bundles'),
        ])
      },
      onError: (error: Error) => message.error(error.message || '操作失败，请重试'),
    },
  )
  const openOperation = (action: 'deprecate' | 'delete', ids: string[]) => {
    setOperationReason('')
    setOperation({ kind, action, ids })
  }
  const statusTag = () => status === 'active'
    ? <Tag className="published-asset-active">生产生效</Tag>
    : <Tag>已停用</Tag>
  const actions = (asset: PublishedAsset) => (
    <Space size={4}>
      <Button type="text" icon={<EyeOutlined />} onClick={() => setSelected(asset)}>{isMobile ? '查看详情' : '详情'}</Button>
      {status === 'active' && <Button type="text" onClick={() => openOperation('deprecate', [assetId(kind, asset)])}>停用</Button>}
      <Button type="text" danger onClick={() => openOperation('delete', [assetId(kind, asset)])}>删除</Button>
    </Space>
  )


  const transferKind = transferKinds[kind]
  const countQueries = {
    parser: useQuery(['published-assets-count', 'parser', status], () => pageForKind('parser', { limit: 1, offset: 0, status })),
    policy: useQuery(['published-assets-count', 'policy', status], () => pageForKind('policy', { limit: 1, offset: 0, status })),
    event: useQuery(['published-assets-count', 'event', status], () => pageForKind('event', { limit: 1, offset: 0, status })),
  }

  const listQuery = useQuery(
    ['published-assets', kind, page, pageSize, search, status],
    () => pageForKind(kind, {
      search: search || undefined,
      limit: pageSize,
      offset: (page - 1) * pageSize,
      status,
    }),
  )

  const selectedId = selected ? assetId(kind, selected) : ''
  const templateComparisonQuery = useQuery(
    ['published-template-comparison', selectedId],
    () => parserApi.comparison(selectedId),
    { enabled: Boolean(selectedId && kind === 'parser') },
  )

  const counts = useMemo(() => ({
    parser: countQueries.parser.data?.total || 0,
    policy: countQueries.policy.data?.total || 0,
    event: countQueries.event.data?.total || 0,
  }), [
    countQueries.event.data?.total,
    countQueries.parser.data?.total,
    countQueries.policy.data?.total,
  ])

  const selectKind = (nextKind: PublishedAssetKind) => {
    setExportSelection([])
    setFailures([])
    setKind(nextKind)
    setPage(1)
    setSelected(undefined)
  }

  const rows = listQuery.data?.items || []
  const columns = [
    {
      title: '生产资产',
      key: 'identity',
      width: 310,
      render: (_: unknown, asset: PublishedAsset) => {
        const identity = assetIdentity(kind, asset)
        return (
          <Space direction="vertical" size={0}>
            <Typography.Text strong>{identity.title}</Typography.Text>
            <Typography.Text type="secondary">{identity.scope}</Typography.Text>
          </Space>
        )
      },
    },
    {
      title: '类型 / 版本',
      key: 'version',
      width: 150,
      render: (_: unknown, asset: PublishedAsset) => (
        <Space direction="vertical" size={2}>
          <Tag className="published-asset-type">{kindMeta[kind].shortLabel}</Tag>
          <Typography.Text type="secondary">{assetIdentity(kind, asset).version}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '发布门禁',
      key: 'validation',
      width: 190,
      render: (_: unknown, asset: PublishedAsset) => (
        <Space direction="vertical" size={2}>
          {statusTag()}
          <Typography.Text type="secondary">{validationLabel(kind, asset)}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '来源任务',
      key: 'source',
      ellipsis: true,
      render: (_: unknown, asset: PublishedAsset) => (
        <Typography.Text copyable={assetIdentity(kind, asset).source !== '-'}>
          {assetIdentity(kind, asset).source}
        </Typography.Text>
      ),
    },
    {
      title: '激活时间',
      key: 'activatedAt',
      width: 190,
      render: (_: unknown, asset: PublishedAsset) => formatTime(assetIdentity(kind, asset).activatedAt),
    },
    {
      title: '操作',
      key: 'actions',
      width: 230,
      fixed: 'right' as const,
      render: (_: unknown, asset: PublishedAsset) => actions(asset),
    },
  ]

  const detailIdentity = selected ? assetIdentity(kind, selected) : null
  const detailJson = selected && kind !== 'parser' ? selected : null

  return (
    <div className="published-assets-workspace">
      <div className="product-workspace-header">
        <div>
          <Typography.Title level={4}>生产资产目录</Typography.Title>
          <Typography.Text type="secondary">管理已发布资产，停用或删除后退出新巡检</Typography.Text>
        </div>
        <Space>
          <Link to={transferHref(exportSelection.map(id => `${transferKind}:${id}`))}><Button>规则包{exportSelection.length ? `导出 (${exportSelection.length})` : '导入导出'}</Button></Link>
          <Button
            aria-label="刷新已发布资产"
            icon={<ReloadOutlined />}
            onClick={() => { void queryClient.invalidateQueries('published-assets'); void queryClient.invalidateQueries('published-assets-count') }}
          />
        </Space>
      </div>

      <div className="published-assets-metrics" role="tablist" aria-label="已发布资产类型">
        {(Object.keys(kindMeta) as PublishedAssetKind[]).map((key) => (
          <button
            type="button"
            role="tab"
            aria-selected={kind === key}
            className={kind === key ? 'is-active' : ''}
            key={key}
            onClick={() => selectKind(key)}
          >
            <span>{kindMeta[key].label}</span>
            <strong>{counts[key]}</strong>
          </button>
        ))}
      </div>

      <div className="published-assets-toolbar">
        <Input.Search
          allowClear
          value={searchDraft}
          placeholder={`搜索${kindMeta[kind].label}名称、命令或来源`}
          onChange={(event) => setSearchDraft(event.target.value)}
          onSearch={(value) => {
            setSearch(value.trim())
            setPage(1)
            setExportSelection([])
            setFailures([])
          }}
        />
        <Select
          aria-label="资产状态" value={status} style={{ width: 140 }}
          options={[{ value: 'active', label: '已启用' }, { value: 'deprecated', label: '已停用' }]}
          onChange={(value) => { setStatus(value); setPage(1); setExportSelection([]); setFailures([]); setSelected(undefined) }}
        />
      </div>


      <Space wrap style={{ marginBottom: 16 }}>
        <Typography.Text>已选择 {exportSelection.length} 项（支持跨页，单次最多 100 项）</Typography.Text>
        {status === 'active' && <Button disabled={!exportSelection.length || exportSelection.length > 100} onClick={() => openOperation('deprecate', exportSelection.map(String))}>批量停用</Button>}
        <Button danger disabled={!exportSelection.length || exportSelection.length > 100} onClick={() => openOperation('delete', exportSelection.map(String))}>批量删除</Button>
        {!!exportSelection.length && <Button type="text" onClick={() => setExportSelection([])}>清空选择</Button>}
      </Space>
      {!!failures.length && <Alert type="error" showIcon message="部分资产操作失败" style={{ marginBottom: 16 }}
        description={<ul>{failures.map(item => <li key={item.id}>{item.id}：{item.error}</li>)}</ul>} />}
      {listQuery.isError && <Alert type="error" message="资产列表加载失败，请刷新重试" />}
      {isMobile ? (
        <List
          className="product-mobile-list"
          loading={listQuery.isLoading}
          dataSource={rows}
          locale={{ emptyText: <Empty description="没有匹配的已发布资产" /> }}
          renderItem={(asset) => {
            const identity = assetIdentity(kind, asset)
            return (
              <List.Item>
                <div className="product-mobile-item">
                  <div className="product-mobile-item__header">
                    <Checkbox checked={exportSelection.includes(assetId(kind, asset))}
                      aria-label={`选择 ${identity.title}`}
                      onChange={event => setExportSelection(previous => event.target.checked
                        ? [...previous, assetId(kind, asset)] : previous.filter(id => id !== assetId(kind, asset)))}>
                      <Typography.Text strong>{identity.title}</Typography.Text>
                    </Checkbox>
                    {statusTag()}
                  </div>
                  <Typography.Text type="secondary">{identity.scope}</Typography.Text>
                  <div className="product-mobile-item__meta">
                    <span>{kindMeta[kind].shortLabel} {identity.version}</span>
                    <span>{formatTime(identity.activatedAt)}</span>
                  </div>
                  <Space>{actions(asset)}<Link to={transferHref([`${transferKind}:${assetId(kind, asset)}`])}>导出规则包</Link></Space>
                </div>
              </List.Item>
            )
          }}
        />
      ) : (
        <Table
          className="product-data-table"
          rowKey={(asset) => assetId(kind, asset)}
          rowSelection={{ selectedRowKeys: exportSelection, preserveSelectedRowKeys: true, onChange: setExportSelection,
            getCheckboxProps: asset => ({ 'aria-label': `选择 ${assetIdentity(kind, asset).title}` }),
            getTitleCheckboxProps: () => ({ 'aria-label': '全选当前页资产' }) }}
          loading={listQuery.isLoading}
          dataSource={rows}
          columns={columns}
          pagination={false}
          scroll={{ x: 1080 }}
          rowClassName={(asset) => (
            focusId && assetId(kind, asset) === focusId ? 'ant-table-row-selected' : ''
          )}
        />
      )}

      <Pagination
        current={page}
        pageSize={pageSize}
        total={listQuery.data?.total || 0}
        showSizeChanger={!isMobile}
        showTotal={(total) => `共 ${total} 个生产资产`}
        onChange={(nextPage, nextSize) => {
          setPage(nextPage)
          setPageSize(nextSize)
        }}
      />

      <Modal open={Boolean(operation)} title={operation?.action === 'delete' ? '删除资产' : '停用资产'}
        okText={operation?.action === 'delete' ? '确认删除' : '确认停用'} cancelText="取消"
        confirmLoading={actionMutation.isLoading} okButtonProps={{ danger: operation?.action === 'delete' }}
        closable={!actionMutation.isLoading} maskClosable={!actionMutation.isLoading}
        cancelButtonProps={{ disabled: actionMutation.isLoading }}
        onCancel={() => { if (!actionMutation.isLoading) setOperation(undefined) }}
        onOk={() => { if (operation && !actionMutation.isLoading) actionMutation.mutate(operation) }}>
        <Alert type="warning" showIcon
          message={`将${operation?.action === 'delete' ? '删除' : '停用'} ${operation?.ids.length || 0} 项${kindMeta[operation?.kind || kind].label}`}
          description={operation?.action === 'delete'
            ? '资产将退出目录，关联运行包同时停用。历史巡检结果和引用记录保留，不提供删除恢复。'
            : '资产及依赖它的运行包停止用于新巡检。资产保留在“已停用”列表，历史巡检结果不变。'} />
        <Input.TextArea aria-label="操作备注" placeholder="操作备注（可选）" value={operationReason}
          maxLength={2000} onChange={event => setOperationReason(event.target.value)} style={{ marginTop: 16 }} />
      </Modal>
      {kind === 'parser' ? (
        <ParserTemplateApprovalDrawer
          open={Boolean(selected)}
          readOnly
          title="Parser 模板详情"
          drawerWidth="min(1440px, 92vw)"
          expandedEvidence
          evidenceOnly
          onClose={() => setSelected(undefined)}
          isMobile={isMobile}
          loading={templateComparisonQuery.isLoading}
          comparison={templateComparisonQuery.data}
          onReview={() => undefined}
          onApprove={() => undefined}
        />
      ) : (
        <Drawer
          rootClassName="product-detail-drawer"
          title={`${kindMeta[kind].label}详情`}
          open={Boolean(selected)}
          width={isMobile ? '100vw' : 760}
          destroyOnClose
          onClose={() => setSelected(undefined)}
        >
          {selected && detailIdentity && (
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
                <Descriptions.Item label="资产名称" span={isMobile ? 1 : 2}>
                  {detailIdentity.title}
                </Descriptions.Item>
                <Descriptions.Item label="适用范围">{detailIdentity.scope}</Descriptions.Item>
                <Descriptions.Item label="版本">{detailIdentity.version}</Descriptions.Item>
                <Descriptions.Item label="运行状态">{statusTag()}</Descriptions.Item>
                <Descriptions.Item label="激活时间">{formatTime(detailIdentity.activatedAt)}</Descriptions.Item>
                <Descriptions.Item label="来源任务" span={isMobile ? 1 : 2}>
                  <Typography.Text copyable={detailIdentity.source !== '-'}>{detailIdentity.source}</Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="发布门禁" span={isMobile ? 1 : 2}>
                  {validationLabel(kind, selected)}
                </Descriptions.Item>
              </Descriptions>
              <Typography.Title level={5}>治理元数据</Typography.Title>
              <pre className="product-code-panel">{JSON.stringify(detailJson, null, 2)}</pre>
            </Space>
          )}
        </Drawer>
      )}
    </div>
  )
}

export default PublishedAssetsWorkspace
