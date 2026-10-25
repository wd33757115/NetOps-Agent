import React, { useState } from 'react'
import {
  Button, Descriptions, Drawer, Empty, Grid, Input, List, Pagination, Select,
  Space, Table, Tag, Typography,
} from 'antd'
import { EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from 'react-query'

import { parserApi, type ParserApprovalAudit } from '@netops/services/api'

type AuditAction = 'approved' | 'rejected' | 'deprecated'

const actionMeta: Record<AuditAction, { label: string; className: string }> = {
  approved: { label: '批准发布', className: 'is-published' },
  rejected: { label: '拒绝发布', className: 'is-failed' },
  deprecated: { label: '废弃资产', className: 'is-neutral' },
}

const assetTypeLabel: Record<string, string> = {
  PARSER_TEMPLATE: 'Parser 模板',
  OBSERVATION_CONTRACT: '数据契约',
  CHANGE_POLICY: '变化策略',
  EVENT_RULE: '事件规则',
  RULE_ASSET_BUNDLE: '规则资产包',
}

const formatTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

const validationSummary = (row: ParserApprovalAudit) => [
  ['总体', row.validation_success],
  ['静态验证', row.synthetic_success],
  ['历史回放', row.replay_success],
] as Array<[string, boolean | null | undefined]>

const AuditTimelineWorkspace: React.FC = () => {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [action, setAction] = useState<AuditAction>()
  const [assetType, setAssetType] = useState<string>()
  const [actor, setActor] = useState('')
  const [selected, setSelected] = useState<ParserApprovalAudit>()

  const query = useQuery(
    ['product-approval-audit', page, pageSize, search, action, assetType, actor],
    () => parserApi.approvalAudit({
      limit: pageSize,
      offset: (page - 1) * pageSize,
      search: search || undefined,
      action,
      asset_type: assetType,
      actor: actor.trim() || undefined,
    }),
    { keepPreviousData: true },
  )

  const summary = query.data?.summary || {
    total: query.data?.total || 0,
    approved: 0,
    rejected: 0,
    deprecated: 0,
  }
  const rows = query.data?.items || []

  const chooseAction = (next?: AuditAction) => {
    setAction(next)
    setPage(1)
  }

  const columns = [
    {
      title: '时间',
      dataIndex: 'occurred_at',
      width: 190,
      render: (value: string | null) => formatTime(value),
    },
    {
      title: '操作与人员',
      key: 'actor',
      width: 180,
      render: (_: unknown, row: ParserApprovalAudit) => {
        const meta = actionMeta[row.action as AuditAction] || {
          label: row.action,
          className: 'is-neutral',
        }
        return (
          <Space direction="vertical" size={2}>
            <Tag className={`product-status-tag ${meta.className}`}>{meta.label}</Tag>
            <Typography.Text>{row.actor || '-'}</Typography.Text>
          </Space>
        )
      },
    },
    {
      title: '资产',
      key: 'asset',
      width: 300,
      render: (_: unknown, row: ParserApprovalAudit) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{assetTypeLabel[row.asset_type || ''] || row.asset_type || row.job_type}</Typography.Text>
          <Typography.Text copyable={Boolean(row.asset_version_id || row.asset_ref)}>
            {row.asset_version_id || row.asset_ref || row.asset_id || '-'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '发布依据',
      key: 'validation',
      width: 250,
      render: (_: unknown, row: ParserApprovalAudit) => (
        <div className="audit-validation-row">
          {validationSummary(row).map(([label, value]) => (
            <span key={label} className={value === true ? 'is-pass' : value === false ? 'is-fail' : 'is-unknown'}>
              {label} {value === true ? '通过' : value === false ? '未通过' : '无数据'}
            </span>
          ))}
        </div>
      ),
    },
    {
      title: '理由',
      dataIndex: 'reason',
      ellipsis: true,
      render: (value: string | null) => value || '-',
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right' as const,
      width: 86,
      render: (_: unknown, row: ParserApprovalAudit) => (
        <Button type="text" icon={<EyeOutlined />} onClick={() => setSelected(row)}>详情</Button>
      ),
    },
  ]

  return (
    <div className="audit-timeline-workspace">
      <div className="product-workspace-header">
        <div>
          <Typography.Title level={4}>规则资产审计</Typography.Title>
          <Typography.Text type="secondary">生产资产发布、拒绝与废弃的不可变记录</Typography.Text>
        </div>
        <Button
          aria-label="刷新审计记录"
          icon={<ReloadOutlined />}
          onClick={() => queryClient.invalidateQueries('product-approval-audit')}
        />
      </div>

      <div className="audit-action-metrics" role="tablist" aria-label="审计动作">
        <button
          type="button"
          role="tab"
          aria-selected={!action}
          className={!action ? 'is-active' : ''}
          onClick={() => chooseAction()}
        >
          <span>全部记录</span>
          <strong>{summary.total}</strong>
        </button>
        {(Object.keys(actionMeta) as AuditAction[]).map((key) => (
          <button
            type="button"
            role="tab"
            aria-selected={action === key}
            className={action === key ? 'is-active' : ''}
            key={key}
            onClick={() => chooseAction(key)}
          >
            <span>{actionMeta[key].label}</span>
            <strong>{summary[key]}</strong>
          </button>
        ))}
      </div>

      <div className="audit-filters">
        <Input.Search
          allowClear
          value={searchDraft}
          placeholder="搜索资产、版本、Job 或审批理由"
          onChange={(event) => setSearchDraft(event.target.value)}
          onSearch={(value) => {
            setSearch(value.trim())
            setPage(1)
          }}
        />
        <Select
          allowClear
          value={assetType}
          placeholder="资产类型"
          onChange={(value) => {
            setAssetType(value)
            setPage(1)
          }}
          options={Object.entries(assetTypeLabel).map(([value, label]) => ({ value, label }))}
        />
        <Input
          allowClear
          value={actor}
          placeholder="操作人"
          onChange={(event) => {
            setActor(event.target.value)
            setPage(1)
          }}
        />
      </div>

      {isMobile ? (
        <List
          className="product-mobile-list"
          loading={query.isLoading}
          dataSource={rows}
          locale={{ emptyText: <Empty description="没有匹配的审计记录" /> }}
          renderItem={(row) => {
            const meta = actionMeta[row.action as AuditAction] || {
              label: row.action,
              className: 'is-neutral',
            }
            return (
              <List.Item>
                <div className="product-mobile-item">
                  <div className="product-mobile-item__header">
                    <Typography.Text strong>
                      {assetTypeLabel[row.asset_type || ''] || row.asset_type || row.job_type}
                    </Typography.Text>
                    <Tag className={`product-status-tag ${meta.className}`}>{meta.label}</Tag>
                  </div>
                  <Typography.Text>{row.actor || '-'} · {formatTime(row.occurred_at)}</Typography.Text>
                  <Typography.Text type="secondary">{row.reason || '未填写理由'}</Typography.Text>
                  <Button icon={<EyeOutlined />} onClick={() => setSelected(row)}>查看审计详情</Button>
                </div>
              </List.Item>
            )
          }}
        />
      ) : (
        <Table
          className="product-data-table"
          rowKey="id"
          loading={query.isLoading}
          dataSource={rows}
          columns={columns}
          pagination={false}
          scroll={{ x: 1250 }}
        />
      )}

      <Pagination
        current={page}
        pageSize={pageSize}
        total={query.data?.total || 0}
        showSizeChanger={!isMobile}
        showTotal={(total) => `共 ${total} 条审计记录`}
        onChange={(nextPage, nextSize) => {
          setPage(nextPage)
          setPageSize(nextSize)
        }}
      />

      <Drawer
        rootClassName="product-detail-drawer"
        title="审计详情"
        open={Boolean(selected)}
        width={isMobile ? '100vw' : 760}
        destroyOnClose
        onClose={() => setSelected(undefined)}
      >
        {selected && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
              <Descriptions.Item label="发生时间">{formatTime(selected.occurred_at)}</Descriptions.Item>
              <Descriptions.Item label="操作人">{selected.actor || '-'}</Descriptions.Item>
              <Descriptions.Item label="治理动作">
                {actionMeta[selected.action as AuditAction]?.label || selected.action}
              </Descriptions.Item>
              <Descriptions.Item label="资产类型">
                {assetTypeLabel[selected.asset_type || ''] || selected.asset_type || selected.job_type}
              </Descriptions.Item>
              <Descriptions.Item label="资产版本" span={isMobile ? 1 : 2}>
                <Typography.Text copyable>
                  {selected.asset_version_id || selected.asset_ref || selected.asset_id || '-'}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="来源 Job" span={isMobile ? 1 : 2}>
                <Typography.Text copyable>{selected.job_id}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="审批理由" span={isMobile ? 1 : 2}>
                {selected.reason || '-'}
              </Descriptions.Item>
            </Descriptions>

            <Typography.Title level={5}>发布依据</Typography.Title>
            <div className="audit-detail-validations">
              {validationSummary(selected).map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <Tag className={`product-status-tag ${value === true ? 'is-published' : value === false ? 'is-failed' : 'is-neutral'}`}>
                    {value === true ? '通过' : value === false ? '未通过' : '无数据'}
                  </Tag>
                </div>
              ))}
            </div>

            <Typography.Title level={5}>治理元数据</Typography.Title>
            <pre className="product-code-panel">
              {JSON.stringify({
                asset_digest: selected.asset_digest,
                source_ref: selected.source_ref,
                created_by: selected.created_by,
                created_at: selected.created_at,
                updated_at: selected.updated_at,
                governance: selected.governance,
              }, null, 2)}
            </pre>
          </Space>
        )}
      </Drawer>
    </div>
  )
}

export default AuditTimelineWorkspace
