import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Modal, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { RuleGenerationGuide } from '@netops/services/api'
import { parserApi } from '@netops/services/api'
import { queryErrorLabel } from './parserUi'

type GuideRow = RuleGenerationGuide['rows'][number]

const none = <Typography.Text type="secondary">无</Typography.Text>

const columns: ColumnsType<GuideRow> = [
  { title: '语义类型', dataIndex: 'kinds', width: 132 },
  { title: '用途', dataIndex: 'use', width: 88 },
  { title: '还要填什么', dataIndex: 'needs', width: 150, render: (value: string) => value || none },
  { title: '变化规则', dataIndex: 'change_rule', width: 128, render: (value: string) => value || none },
  { title: '事件规则', dataIndex: 'event_rules', width: 150, render: (value: string) => value || none },
  { title: '巡检时的效果', dataIndex: 'effect' },
]

interface RuleGuideDialogProps {
  open: boolean
  /** The row the field being edited falls in, and the field's name. */
  current?: { row: string; field?: string }
  onClose: () => void
}

/** How a field's settings become change rules and event rules: the whole correspondence in one table. */
const RuleGuideDialog: React.FC<RuleGuideDialogProps> = ({ open, current, onClose }) => {
  // The table is the same for every field and every check item; it is read once.
  const [guide, setGuide] = useState<RuleGenerationGuide>()
  const [error, setError] = useState<string>()
  const [attempt, setAttempt] = useState(0)
  const body = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open || guide) return undefined
    let live = true
    setError(undefined)
    parserApi.ruleGenerationGuide()
      .then((result) => { if (live) setGuide(result) })
      .catch((reason) => { if (live) setError(queryErrorLabel(reason)) })
    return () => { live = false }
  }, [open, guide, attempt])

  // Opened from a field, the table is longer than the screen: bring that field's row into view.
  useEffect(() => {
    if (!open || !guide || !current?.row) return
    const container = body.current
    const row = container?.querySelector('tr.rule-guide__row--current')
    if (!container || !row) return
    // Scroll the dialog's own body only; scrollIntoView would also move whatever lies behind it.
    const offset = row.getBoundingClientRect().top - container.getBoundingClientRect().top
    container.scrollTop += offset - (container.clientHeight - row.getBoundingClientRect().height) / 2
  }, [open, guide, current?.row])

  return (
    <Modal
      open={open}
      width={960}
      centered
      title="规则是怎么生成的"
      onCancel={onClose}
      footer={<Button onClick={onClose}>知道了</Button>}
    >
      <div ref={body} className="rule-guide__body">
      <Typography.Paragraph>
        变化规则和事件规则不用单独编写。每个字段按它的语义类型、用途，以及填写的正常和异常状态或阈值字段，由系统按下表编译出来；同样的设置每次得到同样的规则。
      </Typography.Paragraph>
      {error && (
        <Alert
          type="error"
          showIcon
          message="对照表加载失败"
          description={error}
          action={<Button onClick={() => setAttempt(attempt + 1)}>重试</Button>}
        />
      )}
      {current?.field && guide && (
        <Typography.Paragraph type="secondary">标出的一行是字段 {current.field} 现在的设置。</Typography.Paragraph>
      )}
      <Table<GuideRow>
        rowKey="key"
        size="small"
        loading={open && !guide && !error}
        dataSource={guide?.rows || []}
        columns={columns}
        pagination={false}
        scroll={{ x: 760 }}
        rowClassName={(row) => (row.key === current?.row ? 'rule-guide__row--current' : '')}
        onRow={(row) => (row.key === current?.row ? { 'aria-current': 'true' as const } : {})}
      />
      {guide && (
        <div className="rule-guide__notes">
          <div>
            <Typography.Text strong>不能在表单里改的</Typography.Text>
            <ul>{guide.fixed.map((line) => <li key={line}>{line}</li>)}</ul>
          </div>
          <div>
            <Typography.Text strong>规则什么时候生成、什么时候生效</Typography.Text>
            <ul>{guide.timing.map((line) => <li key={line}>{line}</li>)}</ul>
          </div>
        </div>
      )}
      </div>
    </Modal>
  )
}

export default RuleGuideDialog
