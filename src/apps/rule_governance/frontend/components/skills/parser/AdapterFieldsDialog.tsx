import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Modal, Spin, Tag, Typography, message } from 'antd'
import type { AdapterFieldSource, AdapterFieldsState } from '@netops/services/api'
import { parserApi } from '@netops/services/api'
import { queryErrorLabel } from './parserUi'

const POLL_MS = 3000
const USES: Record<string, string> = {
  evidence: '仅展示', change: '变化检测', current_state: '健康判断', reference: '阈值参考',
}
const active = (status?: string) => status === 'queued' || status === 'running'

export interface AdapterFieldsTarget {
  implementationId: string
  name: string
  checkItemId: string
}

interface AdapterFieldsDialogProps {
  /** The adapter to add fields to; the dialog is closed without one. */
  target?: AdapterFieldsTarget
  onClose: () => void
  /** A trial ended or fields went into force: what the page shows about the adapter is out of date. */
  onChanged: () => void | Promise<void>
}

/** The output line a value was read from, with the captured part marked. */
const SourceLine: React.FC<{ source: AdapterFieldSource }> = ({ source }) => {
  const at = source.line.indexOf(source.value)
  return (
    <div className="adapter-fields__source">
      {source.above && <div className="adapter-fields__source-above">{source.above}</div>}
      <div>
        {at < 0 ? source.line : (
          <>
            {source.line.slice(0, at)}
            <mark>{source.value}</mark>
            {source.line.slice(at + source.value.length)}
          </>
        )}
      </div>
    </div>
  )
}

/**
 * Adding fields to one adapter: choose, wait for the trial, confirm each field, save.
 *
 * A value that parses is not thereby the right value, so nothing is saved until
 * a person has seen each new field beside the output line it was read from.
 */
const AdapterFieldsDialog: React.FC<AdapterFieldsDialogProps> = ({ target, onClose, onChanged }) => {
  const [state, setState] = useState<AdapterFieldsState>()
  const [error, setError] = useState<string>()
  const [wanted, setWanted] = useState<string[]>([])
  const [kept, setKept] = useState<string[]>([])
  // A finished trial stays on screen until the engineer asks for another one.
  const [choosing, setChoosing] = useState(false)
  const [working, setWorking] = useState(false)
  const [showLines, setShowLines] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const wasActive = useRef(false)
  // The page hands over a new callback on every render; the timers must not restart for that.
  const changed = useRef(onChanged)
  changed.current = onChanged
  const implementationId = target?.implementationId
  const checkItemId = target?.checkItemId

  const take = useCallback((next: AdapterFieldsState) => {
    setState(next)
    setError(undefined)
    if (wasActive.current && !active(next.status)) void changed.current()
    wasActive.current = active(next.status)
  }, [])

  useEffect(() => {
    setState(undefined)
    setError(undefined)
    setChoosing(false)
    setShowLines(false)
    wasActive.current = false
    if (!implementationId || !checkItemId) return undefined
    let current = true
    parserApi.adapterFields(implementationId, checkItemId)
      .then((next) => { if (current) take(next) })
      .catch((reason) => { if (current) setError(queryErrorLabel(reason)) })
    return () => { current = false }
  }, [implementationId, checkItemId, take])

  const status = state?.status
  useEffect(() => {
    if (!implementationId || !checkItemId || !active(status)) return undefined
    const poll = window.setInterval(() => {
      parserApi.adapterFields(implementationId, checkItemId).then(take).catch(() => undefined)
    }, POLL_MS)
    return () => window.clearInterval(poll)
  }, [implementationId, checkItemId, status, take])

  const startedAt = state?.started_at
  useEffect(() => {
    if (!active(status) || !startedAt) return undefined
    const tick = () => setSeconds(Math.max(0, Math.round((Date.now() - new Date(startedAt).getTime()) / 1000)))
    tick()
    const timer = window.setInterval(tick, 1000)
    return () => window.clearInterval(timer)
  }, [status, startedAt])

  const result = state?.result
  const ready = status === 'ready' && result && !result.stale && !choosing
  // Fields a trial already found missing from the device's output are not asked for again by default.
  useEffect(() => {
    setWanted((state?.missing_fields || []).filter((field) => !field.absent).map((field) => field.name))
    setKept(result?.provided_fields || [])
  }, [state?.finished_at, state?.status, state?.missing_fields.length])

  const start = async () => {
    if (!implementationId || !checkItemId) return
    setWorking(true)
    try {
      setChoosing(false)
      take(await parserApi.tryAdapterFields(implementationId, checkItemId, wanted))
    } catch (reason) {
      message.error(queryErrorLabel(reason))
    } finally {
      setWorking(false)
    }
  }

  const save = async () => {
    if (!implementationId || !checkItemId) return
    setWorking(true)
    try {
      const saved = await parserApi.saveAdapterFields(implementationId, checkItemId, kept)
      message.success(`已启用 ${saved.fields.join('、')}，下次巡检生效`)
      take(await parserApi.adapterFields(implementationId, checkItemId))
      await changed.current()
    } catch (reason) {
      message.error(queryErrorLabel(reason))
    } finally {
      setWorking(false)
    }
  }

  const missing = state?.missing_fields || []
  const few = Boolean(state && state.sample_count > 0 && state.sample_count < 3)
  const footer = () => {
    if (ready) {
      return [
        <Typography.Text key="note" type="secondary" className="adapter-fields__footnote">
          请核对取到的值确实是这个字段的含义，不是的话取消勾选。保存后解析模板、适配器和运行包一起切换，下次巡检生效。
        </Typography.Text>,
        <Button key="cancel" disabled={working} onClick={onClose}>取消</Button>,
        <Button key="again" disabled={working} onClick={() => setChoosing(true)}>重新试算</Button>,
        <Button key="save" type="primary" loading={working}
          disabled={!kept.length} onClick={() => void save()}>
          {kept.length ? `保存并启用 ${kept.length} 个字段` : '保存并启用'}
        </Button>,
      ]
    }
    if (active(status)) return [<Button key="close" onClick={onClose}>关闭，稍后回来看</Button>]
    return [
      <Button key="cancel" disabled={working} onClick={onClose}>
        {missing.length && status !== 'saved' ? '取消' : '关闭'}
      </Button>,
      ...(missing.length ? [
        <Button key="start" type="primary" loading={working}
          disabled={!wanted.length || !state?.sample_count} onClick={() => void start()}>
          开始试算
        </Button>,
      ] : []),
    ]
  }

  return (
    <Modal
      open={Boolean(target)}
      width={800}
      centered
      maskClosable={false}
      title={`给 ${target?.name || ''} 补字段`}
      onCancel={onClose}
      footer={footer()}
    >
      <div className="adapter-fields">
        {error && (
          <Alert type="error" showIcon message="补字段的信息加载失败" description={error} />
        )}
        {!state && !error && <div className="adapter-fields__wait"><Spin /></div>}

        {state && active(status) && (
          <div className="adapter-fields__wait">
            <Spin />
            <Typography.Text strong>AI 正在补字段：{state.requested_fields.join('、')}</Typography.Text>
            <Typography.Text type="secondary">
              已用时 {seconds} 秒，通常需要 1–3 分钟。可以关闭这个窗口，结果会保留，从同一个按钮回来看。
            </Typography.Text>
          </div>
        )}

        {state && ready && result && (
          <>
            <Typography.Paragraph type="secondary">
              在现有解析模板上新增规则，用这个适配器巡检时解析过的 {result.sample_count} 份输出验证。
            </Typography.Paragraph>
            {few && (
              <Alert type="warning" showIcon className="adapter-fields__alert"
                message={`只有 ${result.sample_count} 份输出可用来验证`}
                description="样本少，自动校验能发现的问题有限，请更仔细地核对下面取到的值。" />
            )}
            <ul className="adapter-fields__list" aria-label="试算取到值的字段">
              {result.fields.filter((field) => result.provided_fields.includes(field.name)).map((field) => (
                <li key={field.name} className="adapter-fields__field">
                  <Checkbox
                    aria-label={`保存 ${field.name}`}
                    checked={kept.includes(field.name)}
                    onChange={(event) => setKept(event.target.checked
                      ? [...kept, field.name] : kept.filter((name) => name !== field.name))}
                  />
                  <div>
                    <div className="adapter-fields__head">
                      <span className="adapter-fields__name">{field.name}</span>
                      {field.description && <Typography.Text type="secondary">{field.description}</Typography.Text>}
                      <Typography.Text type="secondary" className="adapter-fields__count">
                        {field.samples} 份输出、{field.records} 条记录取到了值
                      </Typography.Text>
                    </div>
                    <div className="adapter-fields__label">取到的值</div>
                    <div className="adapter-fields__values">
                      {field.examples.map((value) => <code key={value}>{value}</code>)}
                    </div>
                    {Boolean(field.sources.length) && (
                      <>
                        <div className="adapter-fields__label">来自设备输出的这一行</div>
                        {field.sources.slice(0, 1).map((source) => <SourceLine key={source.line} source={source} />)}
                      </>
                    )}
                  </div>
                </li>
              ))}
              {result.absent_fields.map((name) => (
                <li key={name} className="adapter-fields__field adapter-fields__field--absent">
                  <span aria-hidden="true">–</span>
                  <div>
                    <span className="adapter-fields__name">{name}</span>
                    <Typography.Text type="secondary">
                      {result.sample_count} 份输出里都没有这个值。已记为“设备不输出”，目录里不再当作缺口。
                    </Typography.Text>
                  </div>
                </li>
              ))}
            </ul>
            <div className="adapter-fields__kept">
              <Typography.Text strong>已有字段不受影响</Typography.Text>
              <Typography.Text type="secondary">
                原有 {result.rules_in_force} 条解析规则一条未改，只新增 {result.rules_added || 0} 条；
                {result.sample_count} 份输出共 {result.record_count || 0} 条记录，已有字段的解析结果和现在完全一样。
                <button type="button" className="adapter-fields__toggle" onClick={() => setShowLines(!showLines)}>
                  {showLines ? '收起模板改动' : '查看模板改动'}
                </button>
              </Typography.Text>
              {showLines && (
                <pre className="adapter-fields__lines" aria-label="新增的模板内容">
                  {(result.added_lines || []).join('\n')}
                </pre>
              )}
            </div>
          </>
        )}

        {state && !active(status) && !ready && (
          <>
            {status === 'saved' && state.saved && !choosing && (
              <Alert type="success" showIcon className="adapter-fields__alert"
                message={`已启用 ${state.saved.fields.join('、')}`}
                description={`适配器 v${state.saved.previous_implementation_version} → v${state.saved.implementation_version}，解析模板和 ${state.saved.runtime_bundle_version_ids.length} 个运行包一起切换，下次巡检生效。`} />
            )}
            {status === 'ready' && result?.stale && (
              <Alert type="warning" showIcon className="adapter-fields__alert"
                message="上次试算的结果已经不能用"
                description="试算之后，适配器、检查项或解析模板变了。请重新试算。" />
            )}
            {status === 'no_data' && result && !choosing && (
              <Alert type="info" showIcon className="adapter-fields__alert"
                message={`设备输出里没有这 ${result.absent_fields.length} 个字段`}
                description={`${result.absent_fields.join('、')}：${result.sample_count} 份输出里都没有。已记为“设备不输出”，目录里不再当作缺口。`} />
            )}
            {(status === 'failed' || status === 'unsupported' || status === 'regressed' || status === 'cancelled') && !choosing && (
              <Alert type="error" showIcon className="adapter-fields__alert"
                message="上次试算没有成功，没有做任何改动"
                description={(
                  <>
                    <div>{result?.reason || state.error || '试算没有完成。'}</div>
                    {(result?.detail || state.error_detail) && (
                      <details className="adapter-fields__detail">
                        <summary>技术细节</summary>
                        <div>{result?.detail || state.error_detail}</div>
                        {result?.regression?.differences.map((line) => <div key={line}>{line}</div>)}
                      </details>
                    )}
                  </>
                )} />
            )}
            {missing.length ? (
              <>
                <Typography.Paragraph>
                  {/* One string: a line break inside the text would show as a space between Chinese clauses. */}
                  {`检查项有 ${state.field_count} 个字段，这个适配器现在提供 ${state.provided_count} 个，还有 ${missing.length} 个没有提供。`
                    + `选择要补的字段，AI 会在现有解析模板上新增规则，再用它在巡检中解析过的 ${state.sample_count} 份输出验证。`}
                </Typography.Paragraph>
                {!state.sample_count && (
                  <Alert type="warning" showIcon className="adapter-fields__alert"
                    message="还没有可以用来验证的输出"
                    description="这个适配器还没有在巡检中解析过任何输出。先让匹配的设备巡检一次，再回来补字段。" />
                )}
                {few && (
                  <Typography.Paragraph type="secondary">
                    只有 {state.sample_count} 份输出可用来验证，样本少，试算后请更仔细地核对取到的值。
                  </Typography.Paragraph>
                )}
                <ul className="adapter-fields__list" aria-label="还没有提供的字段">
                  {missing.map((field) => (
                    <li key={field.name} className="adapter-fields__field">
                      <Checkbox
                        aria-label={`补 ${field.name}`}
                        checked={wanted.includes(field.name)}
                        onChange={(event) => setWanted(event.target.checked
                          ? [...wanted, field.name] : wanted.filter((name) => name !== field.name))}
                      />
                      <div className="adapter-fields__head">
                        <span className="adapter-fields__name">{field.name}</span>
                        {field.description && <Typography.Text type="secondary">{field.description}</Typography.Text>}
                        <span className="adapter-fields__count">
                          {field.absent && <Tag bordered={false}>上次试算：设备不输出</Tag>}
                          <Tag bordered={false}>{USES[field.use] || field.use}</Tag>
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
                <Typography.Text type="secondary">
                  试算通常需要 1–3 分钟，不保存任何东西。取到值的字段要由你逐个核对后才会保存。
                </Typography.Text>
              </>
            ) : (
              <Typography.Paragraph>这个适配器已经提供了检查项的全部字段。</Typography.Paragraph>
            )}
          </>
        )}
      </div>
    </Modal>
  )
}

export default AdapterFieldsDialog
