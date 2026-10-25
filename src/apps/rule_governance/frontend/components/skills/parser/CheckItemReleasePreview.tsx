import React from 'react'
import { Alert, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { CheckItemReleasePreview as ReleasePreview, ImportedTemplateReplay } from '@netops/services/api'
import CheckItemRelationGraph from './CheckItemRelationGraph'
import CheckItemReleaseTrial from './CheckItemReleaseTrial'

type ReleaseField = ReleasePreview['fields'][number]

const changeLabel: Record<ReleaseField['change'], { color?: string; label: string }> = {
  changed: { color: 'orange', label: '变更' },
  added: { color: 'green', label: '新增' },
  removed: { label: '取消' },
  unchanged: { label: '不变' },
}

const lines = (values: string[], empty: string) => values.length
  ? (
    <Space direction="vertical" size={0}>
      {values.map((line) => <Typography.Text key={line}>{line}</Typography.Text>)}
    </Space>
  )
  : <Typography.Text type="secondary">{empty}</Typography.Text>

interface CheckItemReleasePreviewProps {
  preview?: ReleasePreview
  loading?: boolean
  error?: string
}

/** What the replay of an imported adapter's templates rested on, in a few words. */
const replayed = (templates?: Array<{ replay: ImportedTemplateReplay | null }>) => {
  const runs = (templates || []).map((template) => template.replay).filter(Boolean) as ImportedTemplateReplay[]
  if (!runs.length) return '：沿用本地已生效的解析模板'
  const samples = runs.reduce((sum, run) => sum + (run.samples || 0), 0)
  const outputs = runs.reduce((sum, run) => sum + (run.outputs || 0), 0)
  const differing = runs.reduce((sum, run) => sum + (run.records_differing || 0), 0)
  const parts = [samples && `包内 ${samples} 份样本与导出时一致`, outputs && `本地 ${outputs} 份巡检输出都能解析`]
    .filter(Boolean)
  return `：${parts.join('，')}${differing ? `；与现用模板相比有 ${differing} 条记录的值不同` : ''}`
}

/** What publishing this check item version changes in the patrol, and whether it can switch as a whole. */
const CheckItemReleasePreview: React.FC<CheckItemReleasePreviewProps> = ({ preview, loading, error }) => {
  if (loading) return <Typography.Text type="secondary">正在试算发布后的规则和适配器…</Typography.Text>
  if (error) return <Alert type="error" showIcon message="发布试算失败" description={error} />
  if (!preview?.applicable) return null

  const changed = preview.fields.filter((field) => field.change !== 'unchanged')
  const unchanged = preview.fields.length - changed.length
  const release = preview.release
  const brought = release?.adapters.filter((adapter) => adapter.brought_in) || []
  const switched = release?.adapters.filter((adapter) => adapter.in_force && !adapter.brought_in) || []
  const idle = (release?.adapters.length || 0) - switched.length - brought.length
  const dropped = [...new Set((release?.adapters || []).flatMap((adapter) => adapter.dropped_fields || []))]
  const closing = release?.events_closed || []
  const closingCount = closing.reduce((sum, event) => sum + (event.count || 0), 0)
  const columns: ColumnsType<ReleaseField> = [
    {
      title: '字段',
      dataIndex: 'canonical_field',
      width: 190,
      render: (name: string, field) => (
        <Space size={6} wrap>
          <Typography.Text strong>{name}</Typography.Text>
          <Tag color={changeLabel[field.change].color} style={{ marginInlineEnd: 0 }}>{changeLabel[field.change].label}</Tag>
        </Space>
      ),
    },
    {
      title: '现在',
      key: 'before',
      render: (_, field) => lines(field.before, field.change === 'added' ? '没有这个字段' : '只进报告，不判断'),
    },
    {
      title: '发布后',
      key: 'after',
      render: (_, field) => lines(field.after, field.change === 'removed' ? '字段删除，规则停用' : '只进报告，不判断'),
    },
  ]

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {preview.releasable ? (
        <Alert
          type="success"
          showIcon
          message="可以整体切换"
          description={[
            !release?.rules_activated
              ? '没有规则需要生效'
              : changed.length
                ? `${release.rules_activated} 条规则随新版本生效`
                : `${release.rules_activated} 条规则内容不变，随新版本重新生效`,
            switched.length ? `${switched.length} 个适配器和运行包一起切换` : !brought.length && '没有正在使用的适配器需要切换',
            brought.length > 0 && `${brought.length} 个导入的适配器随之生效`,
            '发布前不影响正在运行的巡检',
          ].filter(Boolean).join('；') + '。'}
        />
      ) : (
        <Alert
          type="error"
          showIcon
          message="现在不能发布，当前版本继续巡检"
          description={(
            <Space direction="vertical" size={2}>
              {preview.blockers.map((blocker) => <Typography.Text key={blocker}>{blocker}</Typography.Text>)}
            </Space>
          )}
        />
      )}
      {preview.graph && preview.check_item_id && (
        // A first version has nothing to compare with: the whole picture is new.
        <CheckItemRelationGraph
          checkItemId={preview.check_item_id}
          graph={preview.graph}
          onlyChanges={preview.from_version !== null && preview.from_version !== undefined}
        />
      )}
      {release && (
        <Typography.Text>
          {closingCount > 0
            ? `发布时关闭 ${closingCount} 条事件，因为产生它们的规则停用了（${closing.map((event) => `${event.event_type} ${event.count} 条`).join('、')}）。`
            : '没有事件需要关闭，现有事件保持原样。'}
        </Typography.Text>
      )}
      <CheckItemReleaseTrial trial={preview.trial} />
      {changed.length > 0 ? (
        <Table<ReleaseField>
          rowKey="canonical_field"
          size="small"
          dataSource={changed}
          columns={columns}
          pagination={false}
          scroll={{ x: 720 }}
        />
      ) : (
        <Typography.Text>巡检判断不变。</Typography.Text>
      )}
      {changed.length > 0 && unchanged > 0 && (
        <Typography.Text type="secondary">其余 {unchanged} 个字段的判断不变。</Typography.Text>
      )}
      {dropped.length > 0 && (
        <Typography.Text>已删除的字段 {dropped.join('、')} 会自动从适配器的输出中去掉，不需要改适配器。</Typography.Text>
      )}
      {brought.length > 0 && (
        <div className="release-imported">
          <Typography.Text>随新版本一起生效的导入适配器（解析模板已回放通过）：</Typography.Text>
          <ul>
            {brought.map((adapter) => (
              <li key={adapter.implementation_id}>
                <Typography.Text strong>{adapter.name}</Typography.Text>
                <Typography.Text type="secondary">{replayed(adapter.templates)}</Typography.Text>
                {adapter.text_only_warning && (
                  <Typography.Text type="warning" className="release-imported-unused">{adapter.text_only_warning}。</Typography.Text>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {release && release.adapters.length > brought.length && (
        <Typography.Text type="secondary">
          {switched.length > 0 && `随新版本切换：${switched.map((adapter) => adapter.name).join('、')}。`}
          {idle > 0 && `另有 ${idle} 个适配器原本就未生效，保持不变。`}
        </Typography.Text>
      )}
    </Space>
  )
}

export default CheckItemReleasePreview
