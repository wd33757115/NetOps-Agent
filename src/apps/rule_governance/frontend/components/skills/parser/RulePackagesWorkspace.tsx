import React, { useState } from 'react'
import { Alert, Button, Checkbox, Descriptions, Drawer, Empty, Grid, Input, List, Select, Space, Steps, Table, Tabs, Tag, Typography, Upload, message } from 'antd'
import { DownloadOutlined, InboxOutlined, ReloadOutlined, SwapOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from 'react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { packageApi, packageKinds, packageError, downloadPackage, fileBase64, type ImportResult, type PackagePreview, type PlanItem } from '../../../services/rulePackages'

// Rules are compiled from the check item when it is released, so a package's copies are not imported.
const actions = { create: '新增', reuse: '复用', new_version: '创建新版本', derived: '发布时编译' }
const assetLabels: Record<string, string> = { ...packageKinds, check: '检查项信息', implementation: '采集实现信息', policy: '变化策略信息', event: '事件规则信息', identity: '实体标识信息', comparison: '比较策略信息', assessment: '评估策略信息', link: '检查项与采集关联', parser_binding: 'Parser 命令绑定' }
const summary = (counts: Record<string, number>) => Object.entries(counts).map(([key, n]) => `${actions[key as keyof typeof actions] || key} ${n} 项`).join(' · ')
const json = (value: unknown) => JSON.stringify(value, null, 2)

export default function RulePackagesWorkspace() {
  const [params] = useSearchParams()
  const client = useQueryClient()
  const screens = Grid.useBreakpoint()
  const [tab, setTab] = useState('export')
  const [kind, setKind] = useState('check_version')
  const [status, setStatus] = useState('active')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [historyPage, setHistoryPage] = useState(1)
  const [selected, setSelected] = useState<React.Key[]>(params.getAll('asset'))
  const [samples, setSamples] = useState(false)
  const [drafts, setDrafts] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [file, setFile] = useState<{ name: string; content: string }>()
  const [preview, setPreview] = useState<PackagePreview>()
  const [confirmed, setConfirmed] = useState(false)
  const [result, setResult] = useState<ImportResult>()
  const [detail, setDetail] = useState<PlanItem>()
  const [historyDetail, setHistoryDetail] = useState<ImportResult>()
  const [validation, setValidation] = useState<{ name: string; status: string; blockers: string[]; warnings?: string[] }[]>()
  const query = useQuery(['rule-package-assets', kind, status, search, page], () => packageApi.assets({ kind, status, search, offset: (page - 1) * 30, limit: 30 }))
  const history = useQuery(['rule-package-history', historyPage], () => packageApi.history(historyPage), { enabled: tab === 'history' })
  const act = async (name: string, fn: () => Promise<void>) => {
    if (busy) return
    setBusy(name); setError('')
    try { await fn() } catch (e) { setError(packageError(e)) } finally { setBusy('') }
  }
  const exportFile = (all: boolean) => act('export', async () => {
    const data = await packageApi.export({ selections: selected.map(String), all_active: all, include_drafts: drafts, include_samples: samples })
    downloadPackage(data); message.success(`已导出 ${data.asset_count} 项资产与依赖，${data.sample_count} 份样本`)
    client.invalidateQueries('rule-package-history')
  })
  const loadFile = async (value: File) => {
    await act('preview', async () => {
      setPreview(undefined); setResult(undefined); setConfirmed(false); setFile(undefined)
      const content = await fileBase64(value)
      setFile({ name: value.name, content })
      setPreview(await packageApi.preview(content))
    })
    return false
  }
  const refreshPreview = () => file && act('preview', async () => {
    setPreview(undefined); setConfirmed(false); setPreview(await packageApi.preview(file.content))
  })
  const importFile = () => file && preview && act('import', async () => {
    const data = await packageApi.import(file.content, preview.revision, confirmed)
    setResult(data); setPreview(undefined); setFile(undefined)
    client.invalidateQueries('rule-package-assets'); client.invalidateQueries('rule-package-history')
  })
  const validate = (id: string) => act('validate', async () => setValidation((await packageApi.validate(id)).checks))
  const resultBody = (data: ImportResult) => <>
    <Alert type="success" showIcon message={data.replayed ? '此规则包已导入，已返回原记录' : '导入完成：导入的内容都是草稿，发布后生效'} description={summary(data.counts)} />
    <p className="rules-transfer-note">{data.notice}</p>
    <Space wrap><Button loading={busy === 'validate'} disabled={!!busy} onClick={() => validate(data.record_id)}>试算能否发布</Button>
      <Link to="/apps/rule-governance?workspace=production&subtab=catalog-management&view=changes">前往变更与审批</Link></Space>
    {validation && <List dataSource={validation} renderItem={item => <List.Item><div><Tag color={item.blockers.length ? 'orange' : item.warnings?.length ? 'gold' : 'green'}>{item.blockers.length ? '待处理' : item.warnings?.length ? '可发布，但需注意' : '通过'}</Tag>{item.name}
      {(item.blockers.length > 0 || !!item.warnings?.length) && <ul className="rules-transfer-check-notes">{[...item.blockers, ...(item.warnings || [])].map((b, i) => <li key={i}>{b}</li>)}</ul>}</div></List.Item>} />}
    {data.changes.length > 0 && <List header="要发布的草稿" dataSource={data.changes} pagination={{ pageSize: 8 }} renderItem={item => <List.Item>
      <Link to={`/apps/rule-governance?workspace=production&subtab=catalog-management&change_id=${encodeURIComponent(item.change_id)}`}>{item.asset_id} · 查看与发布</Link></List.Item>} />}
    <Table size="small" rowKey="ref" dataSource={data.items} pagination={{ pageSize: 10 }} scroll={{ x: 600 }} columns={[
      { title: '资产', dataIndex: 'ref', render: (ref, row) => <div>{row.name || ref}<div className="rules-transfer-id">{assetLabels[row.kind] || row.kind}</div></div> }, { title: '处理', dataIndex: 'action', render: a => actions[a as keyof typeof actions] },
      { title: '本地版本', dataIndex: 'version' }, { title: '本地 ID', dataIndex: 'local_id', ellipsis: true, render: v => v || '—' },
    ]} />
  </>
  return <section className="rules-transfer">
    <header className="product-workspace-header"><div><Typography.Title level={4}>规则包导入与导出</Typography.Title>
      <Typography.Text type="secondary">迁移检查项和规则，自动携带必要依赖；导入后验证发布。</Typography.Text></div><SwapOutlined aria-hidden="true" /></header>
    {error && <Alert type="error" showIcon message="操作未完成" description={error} closable onClose={() => setError('')} />}
    <Tabs activeKey={tab} onChange={key => { if (!busy) { setTab(key); setError('') } }} items={[
      { key: 'export', label: '选择与导出', children: <>
        <div className="rules-transfer-toolbar"><Select aria-label="资产类型" value={kind} onChange={v => { setKind(v); setPage(1) }} options={Object.entries(packageKinds).map(([value, label]) => ({ value, label }))} />
          <Select aria-label="资产状态" value={status} onChange={v => { setStatus(v); setPage(1) }} options={[{ value: 'active', label: '已发布' }, { value: 'draft', label: '草稿' }, { value: 'all', label: '所有状态' }]} />
          <Input.Search aria-label="搜索规则资产" placeholder="搜索标识、命令或规则名称" allowClear onSearch={v => { setSearch(v); setPage(1) }} />
          <Button aria-label="刷新规则资产" icon={<ReloadOutlined />} onClick={() => query.refetch()} /></div>
        {query.isError && <Alert type="error" message="资产加载失败" action={<Button onClick={() => query.refetch()}>重试</Button>} />}
        <Table rowKey="ref" loading={query.isLoading} dataSource={query.data?.items || []} scroll={{ x: 640 }}
          rowSelection={{ selectedRowKeys: selected, preserveSelectedRowKeys: true, onChange: setSelected,
            getCheckboxProps: row => ({ 'aria-label': `选择 ${row.name}` }), getTitleCheckboxProps: () => ({ 'aria-label': '全选当前页资产' }) }}
          pagination={{ current: page, pageSize: 30, total: query.data?.total, onChange: setPage, showSizeChanger: false }} columns={[
            { title: '资产', dataIndex: 'name', render: (name, row) => <div><strong>{name}</strong><div className="rules-transfer-id">{row.ref}</div></div> },
            { title: '适用范围', dataIndex: 'scope' }, { title: '版本', dataIndex: 'version', width: 140 },
            { title: '状态', dataIndex: 'status', width: 90, render: s => <Tag>{s === 'active' ? '已发布' : s === 'draft' ? '草稿' : '已停用'}</Tag> },
          ]} />
        <div className="rules-transfer-options"><Checkbox checked={drafts} onChange={e => setDrafts(e.target.checked)}>允许选择未发布版本</Checkbox>
          <Checkbox checked={samples} onChange={e => setSamples(e.target.checked)}>携带已脱敏的验证样本</Checkbox></div>
        {samples && <Alert type="info" message="样本可能保留业务信息；导出后请检查再分享。只携带已保存且校验一致的脱敏文本。" />}
        <div className="rules-transfer-actions"><span>已选择 {selected.length} 项<Button type="link" disabled={!selected.length || !!busy} onClick={() => setSelected([])}>清空</Button></span>
          <Space wrap><Button disabled={!!busy} onClick={() => exportFile(true)}>导出全部已发布规则</Button>
            <Button type="primary" icon={<DownloadOutlined />} loading={busy === 'export'} disabled={!selected.length || !!busy} onClick={() => exportFile(false)}>导出选中与依赖</Button></Space></div>
      </> },
      { key: 'import', label: '导入规则包', children: <>
        <Steps size="small" current={result ? 2 : preview ? 1 : 0} items={[{ title: '上传与预检' }, { title: '核对与导入' }, { title: '到变更与审批发布' }]} />
        {!result && <Upload.Dragger showUploadList={false} accept=".zip,.netops-rules.zip" disabled={!!busy} beforeUpload={loadFile}>
          <p className="ant-upload-drag-icon"><InboxOutlined /></p><p className="ant-upload-text">{busy === 'preview' ? '正在校验格式、依赖和规则内容…' : file?.name || '选择或拖入规则包'}</p>
          <p className="ant-upload-hint">.netops-rules.zip · 最大 12 MB · 预检不写入业务资产</p></Upload.Dragger>}
        {file && !result && <Button disabled={!!busy} loading={busy === 'preview'} onClick={refreshPreview}>重新预检</Button>}
        {preview && <>
          <Alert type={preview.requires_confirmation ? 'warning' : 'info'} showIcon message={summary(preview.counts)}
            description={`${preview.sample_count} 份样本；共完成 ${preview.reports.length} 项结构与回放检查。规则不随包导入，发布检查项时编译；适配器的解析模板在发布前用样本和本地巡检输出回放，通过后随检查项一起生效。`} />
          <Table rowKey="ref" size="small" dataSource={preview.items} pagination={{ pageSize: 12 }} scroll={{ x: 660 }} columns={[
            { title: '资产', dataIndex: 'ref', render: (ref, row) => <div>{row.name || ref}<div className="rules-transfer-id">{assetLabels[row.kind] || row.kind}</div></div> }, { title: '处理方式', dataIndex: 'action', render: (a, row) => <><Tag color={a === 'new_version' ? 'orange' : 'default'}>{actions[a as keyof typeof actions]}</Tag>{row.same_version_conflict && <span>同版本内容不同</span>}</> },
            { title: '目标版本', dataIndex: 'version' }, { title: '详情', render: (_, row) => <Button type="link" onClick={() => setDetail(row)}>查看差异</Button> },
          ]} />
          {preview.samples.length > 0 && <details><summary>查看携带样本与预期结果（{preview.samples.length} 份）</summary><pre className="rules-transfer-json">{json(preview.samples)}</pre></details>}
          {preview.requires_confirmation && <Checkbox checked={confirmed} onChange={e => setConfirmed(e.target.checked)}>已核对冲突，确认创建新版本，保留当前生效版本</Checkbox>}
          <div className="rules-transfer-actions"><span className="rules-transfer-note">确认后一次性写入待验证资产。</span><Button type="primary" loading={busy === 'import'} disabled={!!busy || (preview.requires_confirmation && !confirmed)} onClick={importFile}>确认导入</Button></div>
        </>}
        {result && <><div className="rules-transfer-result">{resultBody(result)}</div><Button onClick={() => { setResult(undefined); setValidation(undefined) }}>导入另一个规则包</Button></>}
      </> },
      { key: 'history', label: '导入导出记录', children: <>
        {history.isError && <Alert type="error" message="记录加载失败" action={<Button onClick={() => history.refetch()}>重试</Button>} />}
        <Table rowKey="id" loading={history.isLoading} dataSource={history.data?.items || []} scroll={{ x: 650 }} locale={{ emptyText: <Empty description="暂无导入导出记录" /> }}
          pagination={{ current: historyPage, pageSize: 20, total: history.data?.total, onChange: setHistoryPage, showSizeChanger: false }} columns={[
            { title: '时间', dataIndex: 'created_at', render: v => v ? new Date(v).toLocaleString('zh-CN') : '—' },
            { title: '操作', dataIndex: 'action', render: v => v === 'rule_package_import' ? '导入' : '导出' },
            { title: '操作者', dataIndex: 'actor' }, { title: '文件摘要', dataIndex: 'sha256', render: v => <Typography.Text copyable>{v.slice(0, 12)}</Typography.Text> },
            { title: '结果', render: (_, row) => row.counts ? summary(row.counts) : `${row.asset_count} 项资产` },
            { title: '详情', render: (_, row) => row.action === 'rule_package_import' && <Button type="link" disabled={!!busy} onClick={() => act('detail', async () => { setValidation(undefined); setHistoryDetail(await packageApi.detail(row.id)) })}>查看</Button> },
          ]} /></> },
    ]} />
    <Drawer title="资产内容差异" width={screens.md ? 900 : '100%'} open={!!detail} onClose={() => setDetail(undefined)}>
      {detail && <><Descriptions column={1} items={[{ key: 'ref', label: '资产', children: detail.ref }, { key: 'action', label: '处理', children: actions[detail.action] }]} />
        {detail.metadata_preserved && <Alert type="info" message="已有资产身份保持不变，目录版本的名称和描述在发布时处理。" />}
        <div className="rules-transfer-diff"><section><h3>本地现有内容</h3><pre className="rules-transfer-json">{json(detail.before)}</pre></section>
          <section><h3>包内内容 / 目标版本</h3><pre className="rules-transfer-json">{json(detail.after)}</pre></section></div></>}
    </Drawer>
    <Drawer title="规则包导入记录" width={screens.md ? 980 : '100%'} open={!!historyDetail} onClose={() => setHistoryDetail(undefined)}>{historyDetail && resultBody(historyDetail)}</Drawer>
  </section>
}
