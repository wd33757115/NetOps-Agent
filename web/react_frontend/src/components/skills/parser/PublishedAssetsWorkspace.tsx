import React, { useMemo, useState } from 'react'
import {
  Button, Descriptions, Drawer, Empty, Grid, Input, List, Pagination, Space,
  Table, Tag, Typography, message,
} from 'antd'
import {
  DownloadOutlined, EyeOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'

import {
  parserApi,
  type ParserChangePolicy,
  type ParserEventRule,
  type ParserObservationContract,
  type ParserPage,
  type ParserTemplateItem,
} from '../../../services/api'

type PublishedAssetKind = 'parser' | 'contract' | 'policy' | 'event'
type PublishedAsset =
  | ParserTemplateItem
  | ParserObservationContract
  | ParserChangePolicy
  | ParserEventRule

interface PublishedAssetsWorkspaceProps {
  focusKind?: string | null
  focusId?: string | null
}

const kindMeta: Record<PublishedAssetKind, { label: string; shortLabel: string }> = {
  parser: { label: 'Parser 模板', shortLabel: 'Parser' },
  contract: { label: '数据契约', shortLabel: 'Contract' },
  policy: { label: '变化策略', shortLabel: 'Policy' },
  event: { label: '事件规则', shortLabel: 'Event' },
}

const focusKindMap: Record<string, PublishedAssetKind> = {
  'parser-template': 'parser',
  snapshot: 'parser',
  'observation-contract': 'contract',
  'change-policy': 'policy',
  change: 'policy',
  'event-rule': 'event',
}

const pageForKind = async (
  kind: PublishedAssetKind,
  params: { search?: string; limit: number; offset: number },
): Promise<ParserPage<PublishedAsset>> => {
  const request = { ...params, status: 'active' }
  if (kind === 'parser') return parserApi.templates(request) as Promise<ParserPage<PublishedAsset>>
  if (kind === 'contract') return parserApi.observationContracts(request) as Promise<ParserPage<PublishedAsset>>
  if (kind === 'policy') return parserApi.changePolicies(request) as Promise<ParserPage<PublishedAsset>>
  return parserApi.eventRules(request) as Promise<ParserPage<PublishedAsset>>
}

const assetId = (kind: PublishedAssetKind, asset: PublishedAsset): string => {
  if (kind === 'parser') return (asset as ParserTemplateItem).id
  if (kind === 'contract') return (asset as ParserObservationContract).id
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
      source: '-',
      activatedAt: item.created_at,
    }
  }
  if (kind === 'contract') {
    const item = asset as ParserObservationContract
    return {
      title: item.schema_id,
      scope: `${item.entity_type} / ${(item.primary_keys_json || []).join(', ') || '无主键'}`,
      version: `v${item.version}`,
      source: item.source_job_id || '-',
      activatedAt: item.activated_at || item.updated_at || item.created_at,
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
  if (kind === 'contract') {
    const item = asset as ParserObservationContract
    return `${item.required_fields_json?.length || 0} 必填字段`
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
  const [kind, setKind] = useState<PublishedAssetKind>(initialKind)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<PublishedAsset>()

  const countQueries = {
    parser: useQuery(['published-assets-count', 'parser'], () => pageForKind('parser', { limit: 1, offset: 0 })),
    contract: useQuery(['published-assets-count', 'contract'], () => pageForKind('contract', { limit: 1, offset: 0 })),
    policy: useQuery(['published-assets-count', 'policy'], () => pageForKind('policy', { limit: 1, offset: 0 })),
    event: useQuery(['published-assets-count', 'event'], () => pageForKind('event', { limit: 1, offset: 0 })),
  }

  const listQuery = useQuery(
    ['published-assets', kind, page, pageSize, search],
    () => pageForKind(kind, {
      search: search || undefined,
      limit: pageSize,
      offset: (page - 1) * pageSize,
    }),
  )

  const selectedId = selected ? assetId(kind, selected) : ''
  const templatePreviewQuery = useQuery(
    ['published-template-preview', selectedId],
    () => parserApi.preview(selectedId),
    { enabled: Boolean(selectedId && kind === 'parser') },
  )

  const exportMutation = useMutation(
    () => parserApi.exportTemplates('active'),
    {
      onSuccess: (result) => {
        const binary = window.atob(result.content_base64)
        const bytes = new Uint8Array(binary.length)
        for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
        const url = window.URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }))
        const link = document.createElement('a')
        link.href = url
        link.download = result.filename
        link.click()
        window.URL.revokeObjectURL(url)
        message.success(`已导出 ${result.exported_count} 个 Parser 模板`)
      },
      onError: () => message.error('Parser 模板导出失败'),
    },
  )

  const counts = useMemo(() => ({
    parser: countQueries.parser.data?.total || 0,
    contract: countQueries.contract.data?.total || 0,
    policy: countQueries.policy.data?.total || 0,
    event: countQueries.event.data?.total || 0,
  }), [
    countQueries.contract.data?.total,
    countQueries.event.data?.total,
    countQueries.parser.data?.total,
    countQueries.policy.data?.total,
  ])

  const selectKind = (nextKind: PublishedAssetKind) => {
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
          <Tag className="published-asset-active">生产生效</Tag>
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
      width: 86,
      fixed: 'right' as const,
      render: (_: unknown, asset: PublishedAsset) => (
        <Button type="text" icon={<EyeOutlined />} onClick={() => setSelected(asset)}>详情</Button>
      ),
    },
  ]

  const detailIdentity = selected ? assetIdentity(kind, selected) : null
  const detailJson = selected
    ? kind === 'parser'
      ? {
        validation: (selected as ParserTemplateItem).validation_json,
        template_sha256: templatePreviewQuery.data?.sha256,
        hash_valid: templatePreviewQuery.data?.hash_valid,
      }
      : selected
    : null

  return (
    <div className="published-assets-workspace">
      <div className="product-workspace-header">
        <div>
          <Typography.Title level={4}>生产资产目录</Typography.Title>
          <Typography.Text type="secondary">当前运行时可读取的已发布资产</Typography.Text>
        </div>
        <Space>
          {kind === 'parser' && (
            <Button
              icon={<DownloadOutlined />}
              loading={exportMutation.isLoading}
              onClick={() => exportMutation.mutate()}
            >
              导出 Parser
            </Button>
          )}
          <Button
            aria-label="刷新已发布资产"
            icon={<ReloadOutlined />}
            onClick={() => queryClient.invalidateQueries('published-assets')}
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
          }}
        />
        <Typography.Text type="secondary">仅展示 Active 版本</Typography.Text>
      </div>

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
                    <Typography.Text strong>{identity.title}</Typography.Text>
                    <Tag className="published-asset-active">生产生效</Tag>
                  </div>
                  <Typography.Text type="secondary">{identity.scope}</Typography.Text>
                  <div className="product-mobile-item__meta">
                    <span>{kindMeta[kind].shortLabel} {identity.version}</span>
                    <span>{formatTime(identity.activatedAt)}</span>
                  </div>
                  <Button icon={<EyeOutlined />} onClick={() => setSelected(asset)}>查看详情</Button>
                </div>
              </List.Item>
            )
          }}
        />
      ) : (
        <Table
          className="product-data-table"
          rowKey={(asset) => assetId(kind, asset)}
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
              <Descriptions.Item label="运行状态"><Tag className="published-asset-active">生产生效</Tag></Descriptions.Item>
              <Descriptions.Item label="激活时间">{formatTime(detailIdentity.activatedAt)}</Descriptions.Item>
              <Descriptions.Item label="来源任务" span={isMobile ? 1 : 2}>
                <Typography.Text copyable={detailIdentity.source !== '-'}>{detailIdentity.source}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="发布门禁" span={isMobile ? 1 : 2}>
                {validationLabel(kind, selected)}
              </Descriptions.Item>
            </Descriptions>
            {kind === 'parser' && (
              <>
                <Typography.Title level={5}>TextFSM 模板</Typography.Title>
                <pre className="product-code-panel">
                  {templatePreviewQuery.isLoading ? '正在读取模板…' : templatePreviewQuery.data?.content || '模板内容不可用'}
                </pre>
              </>
            )}
            <Typography.Title level={5}>治理元数据</Typography.Title>
            <pre className="product-code-panel">{JSON.stringify(detailJson, null, 2)}</pre>
          </Space>
        )}
      </Drawer>
    </div>
  )
}

export default PublishedAssetsWorkspace
