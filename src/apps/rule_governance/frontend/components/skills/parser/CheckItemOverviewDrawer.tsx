import React, { useState } from 'react'
import { Alert, Button, Drawer, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import type { CheckItemOverview, CheckItemPatrolStatus, ParserCheckItem } from '@netops/services/api'
import { parserApi } from '@netops/services/api'
import AdapterFieldsDialog, { type AdapterFieldsTarget } from './AdapterFieldsDialog'
import CheckItemRelationGraph from './CheckItemRelationGraph'
import RuleGuideDialog from './RuleGuideDialog'
import { queryErrorLabel } from './parserUi'

type OverviewField = CheckItemOverview['fields'][number]
type OverviewAdapter = CheckItemOverview['adapters'][number]

const describeStatus = (status: CheckItemPatrolStatus): { color?: string; label: string; detail: string } => {
  const waiting = status.adapters - status.adapters_ready
  const adapters = `${status.adapters} 个适配器`
  switch (status.state) {
    case 'topology':
      return { label: '拓扑生成', detail: '不参与巡检' }
    case 'no_adapter':
      return { label: '未适配设备', detail: '还没有命令适配器' }
    case 'rules_missing':
      return {
        color: 'orange',
        label: status.rules_awaiting_approval ? '规则待审批' : '规则未生效',
        detail: `${status.rules_active}/${status.rules_wanted} 条规则生效`,
      }
    case 'adapters_pending':
      return {
        color: 'orange',
        label: `${waiting} 个适配器未生效`,
        detail: status.adapters_ready ? `其余 ${status.adapters_ready} 个已生效` : '匹配的设备不会执行这项检查',
      }
    case 'display_only':
      return { label: '仅展示', detail: `不产生规则 · ${adapters}` }
    default:
      return {
        color: 'green',
        label: '生效',
        detail: `${status.rules_wanted ? `${status.rules_wanted} 条规则` : '按设备阈值判断'} · ${adapters}`,
      }
  }
}

/** The patrol status of one check item, as the catalog row and the overview header show it. */
export const CheckItemPatrolStatusLabel: React.FC<{ status?: CheckItemPatrolStatus | null }> = ({ status }) => {
  if (!status) return <Typography.Text type="secondary">-</Typography.Text>
  const { color, label, detail } = describeStatus(status)
  return (
    <Space size={[4, 4]} wrap>
      <Tag color={color} style={{ marginInlineEnd: 0 }}>{label}</Tag>
      {status.pending_change && (
        <Tag style={{ marginInlineEnd: 0 }}>
          {status.pending_change.status === 'draft' ? '有待发布的修改' : '修改待审批'}
        </Tag>
      )}
      <Typography.Text type="secondary">{detail}</Typography.Text>
    </Space>
  )
}

// A long list of names would make every adapter row a column of its own.
const someOf = (names: string[]) => names.length > 3
  ? `${names.slice(0, 3).join('、')} 等 ${names.length} 个`
  : names.join('、')

const adapterState: Record<OverviewAdapter['state'], { color?: string; label: string }> = {
  ready: { color: 'green', label: '已生效' },
  previous_version: { color: 'orange', label: '仍在旧版本' },
  pending: { color: 'orange', label: '未生效' },
  not_required: { label: '用于拓扑' },
}

interface CheckItemOverviewDrawerProps {
  open: boolean
  mobile?: boolean
  checkItem?: ParserCheckItem
  onClose: () => void
  onEdit: (checkItem: ParserCheckItem) => void
  onDelete?: (checkItem: ParserCheckItem) => void
  onRollback?: (checkItem: ParserCheckItem, restoresVersion: number) => void
}

/** One check item with the rules it produces and the adapters that run it. Read-only. */
const CheckItemOverviewDrawer: React.FC<CheckItemOverviewDrawerProps> = ({
  open, mobile, checkItem, onClose, onEdit, onDelete, onRollback,
}) => {
  const queryClient = useQueryClient()
  const checkItemId = checkItem?.check_item_id
  const query = useQuery(
    ['check-item-overview', checkItemId],
    () => parserApi.checkItemOverview(checkItemId || ''),
    { enabled: open && Boolean(checkItemId) },
  )
  const overview = query.data
  const status = overview?.status
  const waiting = status ? status.adapters - status.adapters_ready : 0
  const graph = overview?.graph
  const [guideOpen, setGuideOpen] = useState(false)
  const [fieldsTarget, setFieldsTarget] = useState<AdapterFieldsTarget>()
  const retiredOpen = (graph?.retired || []).reduce((sum, event) => sum + (event.open || 0), 0)
  const closeRetired = useMutation(() => parserApi.closeRetiredRuleEvents(checkItemId || ''), {
    onSuccess: async (result) => {
      const closed = result.events_closed.reduce((sum, event) => sum + (event.count || 0), 0)
      message.success(`已关闭 ${closed} 条事件`)
      await queryClient.invalidateQueries('check-item-overview')
    },
    onError: (error) => message.error(queryErrorLabel(error)),
  })

  const fieldColumns: ColumnsType<OverviewField> = [
    {
      title: '字段',
      key: 'field',
      width: 220,
      render: (_, field) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{field.canonical_field}</Typography.Text>
          {field.description && <Typography.Text type="secondary">{field.description}</Typography.Text>}
        </Space>
      ),
    },
    {
      title: '用途',
      dataIndex: 'use_label',
      width: 96,
      render: (label: string, field) => (
        // Hue is kept for status. A use that judges is set apart by weight alone.
        field.use === 'health' || field.use === 'change'
          ? <Tag>{label}</Tag>
          : <Tag bordered={false}><Typography.Text type="secondary">{label}</Typography.Text></Tag>
      ),
    },
    {
      title: '巡检时会做什么',
      key: 'lines',
      render: (_, field) => {
        if (field.rules_missing) {
          return <Typography.Text type="warning">定义了判断，但规则还没有生效，巡检不会执行。</Typography.Text>
        }
        if (!field.lines.length) {
          return (
            <Typography.Text type="secondary">
              {field.use === 'reference' ? '供其他字段作阈值。' : '只进报告，不判断是否正常。'}
            </Typography.Text>
          )
        }
        return (
          <Space direction="vertical" size={0}>
            {field.lines.map((line) => <Typography.Text key={line}>{line}</Typography.Text>)}
            {field.stale_count > 0 && (
              <Typography.Text type="warning">另有 {field.stale_count} 条旧版本规则仍为生效状态。</Typography.Text>
            )}
          </Space>
        )
      },
    },
    {
      title: '适配器提供',
      dataIndex: 'provided_by',
      width: 104,
      render: (count: number | null, field) => {
        if (count === null) return '-'
        const absent = field.absent_by || 0
        // A device that does not print a field is not a gap someone still has to close.
        const gap = count + absent < (overview?.structured_adapters || 0)
        return (
          <Space direction="vertical" size={0}>
            <Typography.Text type={gap ? 'warning' : undefined}>
              {count} / {overview?.structured_adapters}
            </Typography.Text>
            {absent > 0 && <Typography.Text type="secondary">{absent} 个设备不输出</Typography.Text>}
          </Space>
        )
      },
    },
  ]

  const adapterColumns: ColumnsType<OverviewAdapter> = [
    {
      title: '适配器',
      key: 'adapter',
      render: (_, adapter) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{adapter.name}</Typography.Text>
          <Typography.Text type="secondary">{adapter.scope || '未配置兼容范围'}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '采集命令',
      dataIndex: 'command',
      render: (command: string) => command ? <Typography.Text code>{command}</Typography.Text> : '-',
    },
    {
      title: '提供字段',
      key: 'fields',
      width: 220,
      render: (_, adapter) => {
        if (adapter.fields_provided === null) return <Typography.Text type="secondary">不按字段解析</Typography.Text>
        const absent = adapter.absent_fields || []
        const untried = (adapter.missing_fields || []).filter((name) => !absent.includes(name))
        return (
          <Space direction="vertical" size={0}>
            <Typography.Text>{adapter.fields_provided} / {overview?.fields.length}</Typography.Text>
            {Boolean(untried.length) && (
              <Typography.Text type="secondary" title={untried.join('、')}>
                不提供：{someOf(untried)}
              </Typography.Text>
            )}
            {Boolean(absent.length) && (
              <Typography.Text type="secondary" title={absent.join('、')}>
                设备不输出：{someOf(absent)}
              </Typography.Text>
            )}
          </Space>
        )
      },
    },
    {
      title: '状态',
      key: 'state',
      width: 120,
      render: (_, adapter) => (
        <Tag color={adapterState[adapter.state].color}>
          {adapter.state === 'previous_version' && adapter.running_check_item_version
            ? `仍在 v${adapter.running_check_item_version}`
            : adapterState[adapter.state].label}
        </Tag>
      ),
    },
    {
      title: '',
      key: 'fields-action',
      width: 104,
      render: (_, adapter) => {
        if (!checkItemId || !adapter.missing_fields?.length || status?.state === 'topology') return null
        const trial = adapter.field_trial
        return (
          <Button
            size="small"
            aria-label={`给 ${adapter.name} 补字段`}
            onClick={() => setFieldsTarget({
              implementationId: adapter.implementation_id, name: adapter.name, checkItemId,
            })}
          >
            {trial === 'queued' || trial === 'running' ? '试算中' : trial === 'ready' ? '核对结果' : '补字段'}
          </Button>
        )
      },
    },
  ]

  return (
    <Drawer
      open={open}
      width={mobile ? '100%' : 960}
      title={checkItem ? (checkItem.display_name || checkItem.name) : '检查项'}
      onClose={onClose}
      extra={checkItem && (
        <Space>
          {onDelete && status && status.state !== 'topology' && (
            <Button danger onClick={() => onDelete(checkItem)}>删除</Button>
          )}
          {onRollback && overview?.rollback_version && (
            <Button onClick={() => onRollback(checkItem, overview.rollback_version as number)}>
              回退到 v{overview.rollback_version}
            </Button>
          )}
          <Button onClick={() => onEdit(checkItem)}>修改</Button>
        </Space>
      )}
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {checkItem && (
          <Space size={[12, 4]} wrap>
            <Typography.Text type="secondary">{checkItem.check_item_id} · v{checkItem.version}</Typography.Text>
            <CheckItemPatrolStatusLabel status={status} />
          </Space>
        )}
        {query.isError && (
          <Alert
            type="error"
            showIcon
            message="检查项详情加载失败"
            description={queryErrorLabel(query.error)}
            action={<Button onClick={() => void query.refetch()}>重试</Button>}
          />
        )}
        {status?.state === 'topology' && (
          <Alert type="info" showIcon message="这个检查项只为拓扑生成提供数据，不参与巡检，所以不需要规则。" />
        )}
        {status?.state === 'rules_missing' && (
          <Alert
            type="warning"
            showIcon
            message={`定义了 ${status.rules_wanted} 条规则，现在生效 ${status.rules_active} 条`}
            description={status.rules_awaiting_approval
              ? '规则已生成，正在等待审批。审批通过前，未生效的判断不会在巡检中执行。'
              : '未生效的判断不会在巡检中执行。打开“修改”，在表单底部为已发布版本创建规则草稿。'}
          />
        )}
        {status && status.state !== 'topology' && waiting > 0 && (
          <Alert
            type="warning"
            showIcon
            message={`${waiting} 个适配器没有生效`}
            description="匹配到这些适配器的设备不会执行这项检查。在“制作任务”中完成它们的验证和发布后生效。"
          />
        )}
        {Boolean(status?.blockers.length) && (
          <Alert type="warning" showIcon message="规则无法生成" description={status?.blockers.join('；')} />
        )}
        {Boolean(overview?.leftover_count) && (
          <Alert
            type="warning"
            showIcon
            message={`另有 ${overview?.leftover_count} 条规则绑定在旧版本已不存在的字段上，仍为生效状态`}
          />
        )}

        {graph && checkItemId && (
          <div>
            <div className="check-item-editor-heading">
              <Typography.Title level={5}>字段、规则与事件</Typography.Title>
              <Button size="small" onClick={() => setGuideOpen(true)}>规则是怎么生成的</Button>
            </div>
            {Boolean(graph.errors.length) && !status?.blockers.length && (
              <Alert type="warning" showIcon message="规则无法生成" description={graph.errors.join('；')} style={{ marginBottom: 12 }} />
            )}
            <CheckItemRelationGraph
              checkItemId={checkItemId}
              graph={graph}
              retiredAction={(
                <Space size={12} wrap style={{ marginTop: 2 }}>
                  <Typography.Text type="secondary">
                    这些规则已经不再运行，留下的 {retiredOpen} 条事件不会再更新。
                  </Typography.Text>
                  <Button size="small" loading={closeRetired.isLoading} onClick={() => closeRetired.mutate()}>
                    关闭这 {retiredOpen} 条事件
                  </Button>
                </Space>
              )}
            />
            {graph.patrolled !== false && (
              <dl className="relation-lifecycle" style={{ marginTop: 16 }}>
                <dt>修改后发布</dt>
                <dd>规则随新版本一起切换：不变的照常运行，新增的生效，取消的停用。停用的规则留下的事件在发布时关闭。</dd>
                <dt>回退</dt>
                <dd>把上一个发布过的版本的内容作为新版本再发布一次，规则、适配器和运行包一起回去；确认前先试算。</dd>
                <dt>删除</dt>
                <dd>规则、运行包和只为它服务的适配器一起停用，事件关闭。历史报告、各个版本和审计记录保留。</dd>
              </dl>
            )}
          </div>
        )}
        <div>
          <Typography.Title level={5}>全部字段</Typography.Title>
          <Table<OverviewField>
            rowKey="canonical_field"
            size="small"
            loading={query.isLoading}
            dataSource={overview?.fields || []}
            columns={fieldColumns}
            pagination={false}
            scroll={{ x: 720 }}
            locale={{ emptyText: '这个检查项不按字段解析' }}
          />
        </div>
        <div>
          <Typography.Title level={5}>命令适配器</Typography.Title>
          <Table<OverviewAdapter>
            rowKey="implementation_version_id"
            size="small"
            loading={query.isLoading}
            dataSource={overview?.adapters || []}
            columns={adapterColumns}
            pagination={false}
            scroll={{ x: 820 }}
            locale={{ emptyText: '还没有命令适配器' }}
          />
        </div>
      </Space>
      <RuleGuideDialog open={guideOpen} onClose={() => setGuideOpen(false)} />
      <AdapterFieldsDialog
        target={fieldsTarget}
        onClose={() => setFieldsTarget(undefined)}
        onChanged={() => queryClient.invalidateQueries('check-item-overview')}
      />
    </Drawer>
  )
}

export default CheckItemOverviewDrawer
