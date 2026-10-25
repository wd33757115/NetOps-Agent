import React, { useMemo, useState } from 'react'
import { Alert, Button, Segmented, Table, Upload, message } from 'antd'
import { DownloadOutlined, InboxOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import Layout, { ROOT } from '../Layout'
import { useRefresh } from '../components/Forms'
import { pageLocally } from '../components/Paging'
import { errorText, ledger, type ImportReport } from '../api'
import { useAuth } from '@netops/context/AuthContext'

type Row = ImportReport['rows'][number]

export default function ImportPage() {
  const { user } = useAuth()
  const refresh = useRefresh()
  const [file, setFile] = useState<File>()
  const [report, setReport] = useState<ImportReport>()
  const [busy, setBusy] = useState<'preview' | 'commit'>()
  const [show, setShow] = useState<'attention' | 'all'>('attention')
  const isAdmin = user?.role === 'admin'

  const run = async (target: File, commit: boolean) => {
    setBusy(commit ? 'commit' : 'preview')
    try {
      const result = await ledger.importWorkbook(target, commit)
      setReport(result)
      if (commit && result.committed) {
        message.success('已写入台账')
        refresh()
      }
    } catch (error) {
      message.error(errorText(error))
    } finally {
      setBusy(undefined)
    }
  }

  const totals = useMemo(() => {
    const sum = { 新增: 0, 更新: 0, 无变化: 0, 错误: 0 }
    Object.values(report?.summary || {}).forEach(counts => (Object.keys(sum) as (keyof typeof sum)[]).forEach(key => { sum[key] += counts[key] }))
    return sum
  }, [report])
  const rows = (report?.rows || []).filter(row => show === 'all' || row.result !== '无变化')
  const step = report?.committed ? 3 : report ? 2 : 1

  return <Layout active="import" crumbs={['资源台账', '导入与导出']} title="导入与导出"
    subtitle="用 Excel 一次导入站点、网络区域、业务系统、网段、地址、主机、运营商和专线。先核对预览，没有错误后再写入；写入前不会改动现有数据。"
    actions={<>
      <Button icon={<DownloadOutlined aria-hidden="true" />} onClick={() => ledger.exportAll().catch(error => message.error(errorText(error)))}>导出全部台账</Button>
      <Button icon={<DownloadOutlined aria-hidden="true" />} onClick={() => ledger.downloadTemplate().catch(error => message.error(errorText(error)))}>下载导入模板</Button>
      {report && !report.committed && <Button type="primary" disabled={!isAdmin || report.errors > 0} loading={busy === 'commit'}
        onClick={() => file && run(file, true)}>写入 {totals.新增 + totals.更新} 行</Button>}
    </>}>
    {!isAdmin && <Alert type="info" showIcon style={{ marginBottom: 16 }} message="批量导入需要管理员账号。你可以先预览核对，再请管理员写入。" />}
    <div className="rl-steps">
      <span className={step > 1 ? 'done' : 'on'}><b>{step > 1 ? '✓' : 1}</b>选择文件</span>
      <span className={step === 2 ? 'on' : step > 2 ? 'done' : ''}><b>{step > 2 ? '✓' : 2}</b>核对预览</span>
      <span className={step === 3 ? 'on' : ''}><b>3</b>写入</span>
    </div>
    <Upload.Dragger accept=".xlsx" multiple={false} maxCount={1} showUploadList={false} disabled={Boolean(busy)}
      beforeUpload={next => { setFile(next); setReport(undefined); void run(next, false); return false }}>
      <p style={{ fontSize: 32, color: 'var(--rl-blue)', margin: 0 }}><InboxOutlined aria-hidden="true" /></p>
      <p style={{ margin: '6px 0 2px', fontWeight: 500 }}>{file ? `${file.name}，点击或拖入可替换` : '把填好的导入模板拖到这里，或点击选择'}</p>
      <p className="rl-muted" style={{ margin: 0 }}>.xlsx，最大 10 MB。工作表：站点、网络区域、业务系统、网段、保留范围、IP 地址、主机、运营商、运营商网络、局端、专线类型、共享风险、专线；用不到的表可以删掉。导出的台账改完可以直接导回。</p>
    </Upload.Dragger>
    {busy === 'preview' && <div className="rl-empty">正在核对…</div>}
    {report && <div style={{ marginTop: 22 }}>
      {report.committed && <Alert type="success" showIcon style={{ marginBottom: 16 }} message={`已写入：新增 ${totals.新增} 行，更新 ${totals.更新} 行`}
        action={<Link to={ROOT}>查看网段规划</Link>} />}
      <div className="rl-summary">
        <div><strong>{totals.新增}</strong><span>新增</span></div>
        <div><strong>{totals.更新}</strong><span>更新</span></div>
        <div><strong>{totals.无变化}</strong><span>无变化</span></div>
        <div className={totals.错误 ? 'bad' : ''}><strong>{totals.错误}</strong><span>{totals.错误 ? '有错误，修正后重新上传' : '错误'}</span></div>
      </div>
      {Object.keys(report.created).length > 0 && <Alert type="info" showIcon style={{ marginBottom: 14 }}
        message={report.committed ? '同时新建了：' : '写入时会同时新建：'}
        description={Object.entries(report.created).map(([kind, names]) => `${kind}：${names.join('、')}`).join('；')} />}
      <Segmented value={show} onChange={value => setShow(value as typeof show)} style={{ marginBottom: 12 }}
        options={[{ value: 'attention', label: '只看新增、更新和错误' }, { value: 'all', label: '全部行' }]} />
      <Table<Row> size="middle" rowKey={row => `${row.sheet}-${row.row}`} dataSource={rows} pagination={pageLocally(50)}
        rowClassName={row => row.result === '错误' ? 'rl-error-row' : ''}
        columns={[
          { title: '工作表', dataIndex: 'sheet', width: 100 },
          { title: '行号', dataIndex: 'row', width: 70 },
          { title: '结果', dataIndex: 'result', width: 80, render: value => <span className={`rl-result-tag ${value}`}>{value}</span> },
          { title: '内容', dataIndex: 'label', width: 300, render: value => <strong style={{ fontWeight: 550 }}>{value}</strong> },
          { title: '说明', key: 'message', render: (_, row) => row.result === '错误' ? <span className="rl-error-text">{row.message}</span>
            : row.changes?.length ? <ul className="rl-change-detail">{row.changes.map(c => <li key={c.field}>{c.field}：<del>{String(c.before ?? '空')}</del> → {String(c.after ?? '空')}</li>)}</ul>
            : null },
        ]} />
    </div>}
  </Layout>
}
