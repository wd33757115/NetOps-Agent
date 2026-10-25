import React, { useEffect, useRef, useState } from 'react'
import { Button, Form, Input, Modal, message } from 'antd'
import { DownloadOutlined, FileExcelOutlined, CheckOutlined, SafetyOutlined } from '@ant-design/icons'
import { generateChangeTicket, readRun, activeRun, downloadArtifact, cancelRun, retryRun, type FirewallRun, type ChangeTicketFields, type TicketSource } from '../services/firewallPolicy'

/** Ticket tasks reference a persisted policy run or block batch and survive page reloads. */
export default function FirewallChangeTicket({ source, open, onOpenChange }: { source: TicketSource; open: boolean; onOpenChange: (open: boolean) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [job, setJob] = useState<FirewallRun>()
  const download = job?.status === 'completed' ? job : undefined
  const [refresh, setRefresh] = useState(0)
  const requestId = useRef(crypto.randomUUID())
  const running = useRef(false)
  const [form] = Form.useForm<ChangeTicketFields>()
  const manifest = source.manifest
  const text = (name: string) => typeof manifest[name] === 'string' ? String(manifest[name]) : ''
  const defaults: ChangeTicketFields = {
    ticket_title: text('ticket_title') || '防火墙策略变更',
    assignee: text('assignee'), due_date: text('due_date').replace(' ', 'T').slice(0, 16),
    change_background: text('change_background'), change_purpose: text('change_purpose'),
    requester: text('requester'), requester_dept: text('requester_dept'),
    technical_reviewer: text('technical_reviewer'), reviewer: text('reviewer'),
  }
  const generate = async (values: ChangeTicketFields) => {
    if (running.current) return
    running.current = true
    setBusy(true); setError('')
    try {
      const fields = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, (value || '').trim()])) as unknown as ChangeTicketFields
      const next = await generateChangeTicket(source.run_id, { ...defaults, ...fields }, requestId.current)
      requestId.current = crypto.randomUUID()
      setJob(next); setRefresh(value => value + 1)
      onOpenChange(false)
    } catch (err) { setError(err instanceof Error ? err.message : '工单生成失败，请重试。') }
    finally { running.current = false; setBusy(false) }
  }
  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const parent = await readRun(source.run_id)
        if (stopped) return
        const latest = parent.tickets?.[0]
        setJob(latest); setBusy(activeRun(latest))
        if (latest?.input.fields) form.setFieldsValue(latest.input.fields)
        if (latest?.status === 'failed' || latest?.status === 'cancelled') setError(latest.error || '工单任务未完成')
        if (activeRun(latest)) timer = setTimeout(poll, 1500)
      } catch (err) { if (!stopped) { setBusy(false); setError((err as Error).message) } }
    }
    void poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [source.run_id, refresh, form])
  return <>
    <div className="fw-ticket-bar">
      <div className="fw-ticket-intro"><span className="fw-ticket-symbol"><FileExcelOutlined aria-hidden="true" /></span><div><strong>下一步，制作变更工单</strong><p>设备与配置已就绪，补充信息即可导出。</p></div></div>
      <div className="fw-ticket-actions">
        {busy && <Button onClick={() => job && void cancelRun(job.id).then(() => setRefresh(value => value + 1)).catch(err => setError(err.message))}>取消工单任务</Button>}
        {(job?.status === 'failed' || job?.status === 'cancelled') && <Button onClick={() => void retryRun(job.id, requestId.current).then(() => { requestId.current = crypto.randomUUID(); setRefresh(value => value + 1) }).catch(err => setError(err.message))}>重试工单</Button>}
        <Button onClick={() => onOpenChange(true)} disabled={busy}>{download ? '修改工单信息' : '生成工单'}</Button>
        {download && <Button type="primary" onClick={() => void downloadArtifact(download, 'change_excel').catch(err => message.error(err.message))} icon={<DownloadOutlined aria-hidden="true" />}>下载工单 Excel</Button>}
      </div>
    </div>
    {busy && <p role="status">工单任务已保存，正在生成…</p>}
    {error && !open && <p className="fw-error" role="alert">{error} <Button onClick={() => setRefresh(value => value + 1)}>刷新工单状态</Button></p>}
    <Modal title={<div className="fw-ticket-modal-heading"><span className="fw-ticket-symbol"><FileExcelOutlined aria-hidden="true" /></span><div><span>生成变更工单</span><small>完善变更信息，导出标准 Excel 工单。</small></div></div>} open={open} onCancel={() => onOpenChange(false)} width={880}
      footer={<div className="fw-ticket-submit"><span><SafetyOutlined aria-hidden="true" /> {busy ? '正在生成工单，请稍候…' : '生成表格，不提交审批。'}</span><div><Button disabled={busy} onClick={() => onOpenChange(false)}>取消</Button><Button type="primary" htmlType="submit" form="fw-change-ticket-form" loading={busy}>生成 Excel</Button></div></div>}
      closable={!busy} maskClosable={!busy} keyboard={!busy} className="fw-ticket-modal">
      <div className="fw-ticket-layout"><aside className="fw-ticket-context"><span className="fw-guide-kicker">本次变更</span><strong>{source.ticket_id}</strong><p>{source.summary}</p><ul>{source.devices.map(name => <li key={name}><SafetyOutlined aria-hidden="true" />{name}</li>)}</ul><div className="fw-ticket-included"><span>已自动带入</span>{['设备清单', '执行命令', '验证与回退'].map(label => <p key={label}><CheckOutlined aria-hidden="true" />{label}</p>)}</div></aside>
      <Form id="fw-change-ticket-form" form={form} layout="vertical" initialValues={defaults} onFinish={generate} disabled={busy} requiredMark={false}>
        <div className="fw-ticket-form-label">基本信息</div>
        <Form.Item name="ticket_title" label="变更标题" rules={[{ required: true, whitespace: true, message: '请填写变更标题' }]}><Input maxLength={200} /></Form.Item>
        <div className="fw-ticket-fields">
          <Form.Item name="assignee" label="变更负责人"><Input maxLength={80} placeholder="选填" /></Form.Item>
          <Form.Item name="due_date" label="计划时间"><Input type="datetime-local" /></Form.Item>
        </div>
        <Form.Item name="change_background" label="变更背景"><Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} maxLength={2000} placeholder="说明本次变更的原因（选填）" /></Form.Item>
        <Form.Item name="change_purpose" label="变更目的"><Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} maxLength={2000} placeholder="说明本次变更要达到的效果（选填）" /></Form.Item>
        <details className="fw-ticket-more"><summary>申请与评审信息（选填）</summary>
          <div className="fw-ticket-fields">
            <Form.Item name="requester" label="申请人"><Input maxLength={80} /></Form.Item>
            <Form.Item name="requester_dept" label="申请部门"><Input maxLength={120} /></Form.Item>
            <Form.Item name="technical_reviewer" label="技术评审人"><Input maxLength={80} /></Form.Item>
            <Form.Item name="reviewer" label="复核人"><Input maxLength={80} /></Form.Item>
          </div>
        </details>
        {error && <div className="fw-error" role="alert">{error}</div>}
      </Form>
      </div>
    </Modal>
  </>
}
