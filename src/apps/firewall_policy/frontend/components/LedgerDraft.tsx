import React, { useState } from 'react'
import { Button, Input, message } from 'antd'
import { DownloadOutlined, EyeOutlined } from '@ant-design/icons'
import { downloadLedgerSheet, previewLedger, type LedgerDraftInput, type LedgerRow } from '../services/firewallPolicy'

type Key = keyof LedgerDraftInput

/** Who reaches whom, read from the resource ledger instead of a filled-in sheet. */
export default function LedgerDraft({ value, onChange, disabled }: { value: LedgerDraftInput; onChange: (next: LedgerDraftInput) => void; disabled: boolean }) {
  const [preview, setPreview] = useState<{ rows: LedgerRow[]; notes: string[] }>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const ready = Boolean(value.source.trim() && value.destination.trim())
  const field = (key: Key) => ({
    id: `fw-ledger-${key}`, value: value[key] || '', disabled, autoComplete: 'off',
    onChange: (event: React.ChangeEvent<HTMLInputElement>) => { onChange({ ...value, [key]: event.target.value }); setPreview(undefined); setError('') },
  })
  const show = async () => {
    setLoading(true); setError('')
    try { setPreview(await previewLedger(value)) } catch (err) { setPreview(undefined); setError((err as Error).message) } finally { setLoading(false) }
  }
  return <div className="fw-ledger">
    <div className="fw-ledger-pair">
      <label className="fw-field" htmlFor="fw-ledger-source"><span>源</span><Input {...field('source')} maxLength={500} placeholder="IP、网段、范围或业务系统，如 10.248.255.11" /></label>
      <label className="fw-field" htmlFor="fw-ledger-source_subsystem"><span>源子系统 <small>可选</small></span><Input {...field('source_subsystem')} maxLength={64} placeholder="只取这个子系统" /></label>
    </div>
    <div className="fw-ledger-pair">
      <label className="fw-field" htmlFor="fw-ledger-destination"><span>目的</span><Input {...field('destination')} maxLength={500} placeholder="如 风险评估系统" /></label>
      <label className="fw-field" htmlFor="fw-ledger-destination_subsystem"><span>目的子系统 <small>可选</small></span><Input {...field('destination_subsystem')} maxLength={64} placeholder="如 风险评估子系统1" /></label>
    </div>
    <label className="fw-field" htmlFor="fw-ledger-ports"><span>端口 <small>可选</small></span><Input {...field('ports')} maxLength={300} placeholder="不填时用台账里目的主机登记的服务端口；指定时写成 tcp/443,udp/53" /></label>
    <div className="fw-ledger-times">
      <label className="fw-field" htmlFor="fw-ledger-start_time"><span>开始时间 <small>可选</small></span><Input {...field('start_time')} maxLength={40} placeholder="2026-11-01 20:00" /></label>
      <label className="fw-field" htmlFor="fw-ledger-end_time"><span>结束时间 <small>可选</small></span><Input {...field('end_time')} maxLength={40} placeholder="2026-11-30" /></label>
      <label className="fw-field" htmlFor="fw-ledger-long_link"><span>长连接（小时） <small>可选</small></span><Input {...field('long_link')} maxLength={20} placeholder="168；0 为永不老化" /></label>
    </div>
    <div className="fw-ledger-actions">
      <Button icon={<EyeOutlined aria-hidden="true" />} loading={loading} disabled={!ready || disabled} onClick={() => void show()}>预览访问关系</Button>
      {preview && <Button type="link" icon={<DownloadOutlined aria-hidden="true" />} disabled={disabled}
        onClick={() => void downloadLedgerSheet(value).catch(err => message.error(err.message))}>下载这份申请表</Button>}
    </div>
    {error && <div className="fw-error" role="alert"><strong>台账里拼不出这条访问</strong><p>{error}</p></div>}
    {preview && <div className="fw-ledger-preview" aria-live="polite">
      <table>
        <thead><tr><th>源地址</th><th>目的地址</th><th>服务</th></tr></thead>
        <tbody>{preview.rows.map((row, index) => <tr key={index}>
          <td><span className="fw-mono">{row.src_ip.split(',').join('\n')}</span>{row.source_system && <small>{row.source_system}</small>}</td>
          <td><span className="fw-mono">{row.dst_ip.split(',').join('\n')}</span>{row.destination_system && <small>{row.destination_system}</small>}</td>
          <td className="fw-mono">{row.proto.toUpperCase()}{row.port ? ` ${row.port}` : ''}</td>
        </tr>)}</tbody>
      </table>
      {preview.notes.length > 0 && <ul>{preview.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>}
      <p>共 {preview.rows.length} 条申请。下发到哪台防火墙、哪个安全域，生成策略时按拓扑计算。</p>
    </div>}
  </div>
}
