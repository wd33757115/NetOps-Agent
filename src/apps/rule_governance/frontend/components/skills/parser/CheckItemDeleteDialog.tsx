import React, { useEffect, useState } from 'react'
import { Alert, Button, Input, Modal, Typography, message } from 'antd'
import { useMutation, useQuery } from 'react-query'
import type { CheckItemDeletePreview, ParserCheckItem } from '@netops/services/api'
import { parserApi } from '@netops/services/api'
import { queryErrorLabel } from './parserUi'

const someOf = (names: string[], limit = 3) => names.length > limit
  ? `${names.slice(0, limit).join('、')} 等 ${names.length} 个`
  : names.join('、')

interface Step {
  what: string
  details: string[]
}

/** What goes with the check item, in the order it goes. A step with nothing to do is left out. */
const stepsOf = (preview: CheckItemDeletePreview): Step[] => {
  // Inside its own check item an event type's prefix says nothing.
  const short = (name: string) => (
    name.startsWith(`${preview.check_item_id}.`) ? name.slice(preview.check_item_id.length + 1) : name
  )
  const rules = preview.rules || []
  const bundles = preview.runtime_bundle_version_ids || []
  const withdrawn = preview.adapters_withdrawn || []
  const kept = preview.adapters_kept || []
  const events = preview.events_closed || []
  const closed = events.reduce((sum, event) => sum + (event.count || 0), 0)
  const steps: Step[] = [
    {
      what: '检查项停用',
      details: [
        '新的巡检不再执行这项检查。',
        ...(preview.cancelled_change_count ? [`${preview.cancelled_change_count} 个还没发布的修改一并取消。`] : []),
      ],
    },
  ]
  if (rules.length) {
    const policies = rules.filter((rule) => rule.kind === 'change_policy').length
    const eventRules = rules.filter((rule) => rule.kind === 'event_rule')
    steps.push({
      what: `${rules.length} 条规则停用`,
      details: [
        [
          policies ? `${policies} 条变化规则` : '',
          eventRules.length ? `${eventRules.length} 条事件规则（${someOf(eventRules.map((rule) => short(rule.name)))}）` : '',
        ].filter(Boolean).join('，') + '。',
      ],
    })
  }
  if (bundles.length) {
    steps.push({ what: `${bundles.length} 个运行包停用`, details: ['匹配这些运行包的设备不再采集这项检查。'] })
  }
  if (withdrawn.length || kept.length) {
    steps.push({
      what: withdrawn.length ? `${withdrawn.length} 个命令适配器停用` : '命令适配器保留',
      details: [
        ...(withdrawn.length ? [`只为这个检查项服务：${someOf(withdrawn.map((adapter) => adapter.name))}。TextFSM 模板保留。`] : []),
        ...(kept.length
          ? [`另有 ${kept.length} 个还为其他检查项服务，继续保留：${someOf(kept.map((adapter) => adapter.name))}。`]
          : []),
      ],
    })
  }
  if (closed) {
    steps.push({
      what: `${closed} 条事件关闭`,
      details: [`规则停用后不会再更新，关闭时注明“检查项已删除”：${someOf(events.map((event) => short(event.event_type)))}。`],
    })
  }
  return steps
}

interface CheckItemDeleteDialogProps {
  checkItem?: ParserCheckItem
  onClose: () => void
  onDeleted: () => void | Promise<void>
}

/** Deleting a check item, shown as everything that goes with it before anything is done. */
const CheckItemDeleteDialog: React.FC<CheckItemDeleteDialogProps> = ({ checkItem, onClose, onDeleted }) => {
  const checkItemId = checkItem?.check_item_id
  const name = checkItem?.display_name || checkItem?.name || ''
  const [reason, setReason] = useState('')
  useEffect(() => setReason(''), [checkItemId])
  const query = useQuery(
    ['check-item-delete-preview', checkItemId],
    () => parserApi.previewCheckItemDelete(checkItemId || ''),
    { enabled: Boolean(checkItemId), refetchOnWindowFocus: false, retry: false, cacheTime: 0 },
  )
  const remove = useMutation(
    () => parserApi.deleteCheckItem(
      checkItemId || '',
      reason.trim(),
      `catalog-check-item-delete-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
    ),
    {
      onSuccess: async () => {
        message.success(`“${name}”已删除，规则、运行包和适配器已一并停用`)
        onClose()
        await onDeleted()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const preview = query.data
  const deletable = Boolean(preview?.deletable)

  return (
    <Modal
      open={Boolean(checkItem)}
      width={600}
      centered
      title={`删除“${name}”`}
      onCancel={onClose}
      footer={[
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Button
          key="delete"
          danger
          type="primary"
          disabled={!deletable}
          loading={remove.isLoading}
          onClick={() => remove.mutate()}
        >
          删除并停用以上内容
        </Button>,
      ]}
    >
      {query.isLoading && <Typography.Text type="secondary">正在核对会一起停用的规则、适配器和事件…</Typography.Text>}
      {query.isError && (
        <Alert
          type="error"
          showIcon
          message="无法核对删除的影响"
          description={queryErrorLabel(query.error)}
          action={<Button onClick={() => void query.refetch()}>重试</Button>}
        />
      )}
      {preview && !preview.deletable && (
        <Alert type="error" showIcon message="这个检查项不能删除" description={preview.blockers.join('；')} />
      )}
      {preview && deletable && (
        <>
          <Typography.Paragraph>删除后，下面这些会一起停用。确认前不会有任何改动。</Typography.Paragraph>
          <ol className="cascade-flow" aria-label="删除会一起停用的内容">
            {stepsOf(preview).map((step) => (
              <li key={step.what} className="cascade-flow__step">
                <span className="cascade-flow__what">{step.what}</span>
                {step.details.map((detail) => <span key={detail} className="cascade-flow__detail">{detail}</span>)}
              </li>
            ))}
          </ol>
          <p className="cascade-flow__kept">
            保留：历史巡检报告、{preview.version_count ? `全部 ${preview.version_count} 个版本` : '各个版本'}、审计记录。
          </p>
          <Input.TextArea
            rows={2}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="删除备注（可选）"
            style={{ marginTop: 14 }}
          />
        </>
      )}
    </Modal>
  )
}

export default CheckItemDeleteDialog
