import React, { useMemo, useState } from 'react'
import {
  Alert, Button, Col, Descriptions, Drawer, Input, Row, Select, Space, Spin,
  Statistic, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import {
  CheckOutlined, CloseOutlined, EyeOutlined, PlayCircleOutlined, ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import {
  parserApi, type ParserCommandAlias, type ParserDeviceProfile,
  type ParserIdentitySignature, type ParserJobItem, type ParserTemplateItem,
  type ParserImportPreview, type ParserTemplateComparison, type ParserTemplatePreview,
} from '../../services/api'
import { useIsMobile } from '../../hooks/useBreakpoint'

const statusColor = (status: string) => {
  if (status === 'active' || status === 'succeeded') return 'green'
  if (status === 'failed' || status === 'rejected') return 'red'
  if (status === 'deprecated' || status === 'cancelled' || status === 'superseded') return 'default'
  return 'blue'
}

const codePanelStyle: React.CSSProperties = {
  background: '#f5f5f5', border: '1px solid #e5e7eb', borderRadius: 6,
  fontFamily: 'Consolas, Monaco, monospace', margin: 0, overflow: 'auto',
  padding: 16, whiteSpace: 'pre',
}

const ParserControlTab: React.FC = () => {
  const queryClient = useQueryClient()
  const isMobile = useIsMobile()
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [preview, setPreview] = useState<ParserTemplatePreview | null>(null)
  const [jobOpen, setJobOpen] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [templateStatus, setTemplateStatus] = useState<string>()
  const [templateSearch, setTemplateSearch] = useState('')
  const [jobStatus, setJobStatus] = useState<string>()
  const [jobType, setJobType] = useState<string>()
  const [jobSearch, setJobSearch] = useState('')
  const [sourcePath, setSourcePath] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [importPreview, setImportPreview] = useState<ParserImportPreview | null>(null)
  const [comparisonOpen, setComparisonOpen] = useState(false)
  const [comparisonTemplate, setComparisonTemplate] = useState<ParserTemplateItem | null>(null)
  const [comparison, setComparison] = useState<ParserTemplateComparison | null>(null)
  const [comparisonLoading, setComparisonLoading] = useState(false)

  const polling = { refetchInterval: 5000 }
  const { data: coverage } = useQuery('parser-coverage', parserApi.coverage, polling)
  const { data: templates = [], isLoading: templatesLoading } = useQuery(
    'parser-templates', parserApi.templates, polling,
  )
  const { data: jobs = [], isLoading: jobsLoading } = useQuery(
    'parser-jobs', parserApi.jobs, polling,
  )
  const { data: dispatchControl } = useQuery(
    'parser-dispatch-control', parserApi.dispatchControl, polling,
  )
  const { data: jobDetail, isLoading: jobLoading } = useQuery(
    ['parser-job', jobId], () => parserApi.job(jobId as string),
    { enabled: Boolean(jobId), refetchInterval: jobId ? 5000 : false },
  )
  const { data: aliases = [] } = useQuery('parser-aliases', parserApi.aliases)
  const { data: deviceProfiles = [] } = useQuery('parser-device-profiles', parserApi.deviceProfiles)
  const { data: identitySignatures = [] } = useQuery(
    'parser-identity-signatures', parserApi.identitySignatures,
  )
  const { data: datasets = [] } = useQuery('parser-datasets', parserApi.datasets, polling)

  const filteredTemplates = useMemo(() => templates.filter((item) => {
    const text = `${item.vendor} ${item.profile} ${item.canonical_command} ${item.category}`.toLowerCase()
    return (!templateStatus || item.status === templateStatus)
      && (!templateSearch || text.includes(templateSearch.trim().toLowerCase()))
  }), [templates, templateStatus, templateSearch])

  const filteredJobs = useMemo(() => jobs.filter((item) => {
    const text = `${item.vendor} ${item.profile} ${item.canonical_command}`.toLowerCase()
    return (!jobStatus || item.status === jobStatus)
      && (!jobType || item.job_type === jobType)
      && (!jobSearch || text.includes(jobSearch.trim().toLowerCase()))
  }), [jobs, jobStatus, jobType, jobSearch])

  const refresh = () => {
    ;['parser-coverage', 'parser-templates', 'parser-jobs', 'parser-aliases',
      'parser-device-profiles', 'parser-identity-signatures', 'parser-datasets'].forEach((key) =>
      queryClient.invalidateQueries(key))
    queryClient.invalidateQueries('parser-dispatch-control')
    if (jobId) queryClient.invalidateQueries(['parser-job', jobId])
  }

  const action = useMutation(
    ({ type, id }: { type: string; id: string }) => {
      if (type === 'activate') return parserApi.activate(id)
      if (type === 'reject') return parserApi.reject(id)
      if (type === 'deprecate') return parserApi.deprecate(id)
      if (type === 'retry') return parserApi.retryJob(id)
      if (type === 'dispatch') return parserApi.dispatchJob(id)
      return parserApi.cancelJob(id)
    },
    {
      onSuccess: () => { message.success('操作已提交'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '操作失败'),
    },
  )

  const toggleDispatch = useMutation(
    (enabled: boolean) => parserApi.setDispatchControl(enabled),
    {
      onSuccess: (result) => {
        message.success(result.auto_dispatch_enabled ? '自动调度已开启' : '自动调度已关闭')
        refresh()
      },
      onError: () => message.error('自动调度开关更新失败'),
    },
  )

  const showPreview = async (id: string) => {
    setPreviewOpen(true); setPreviewLoading(true); setPreview(null)
    try { setPreview(await parserApi.preview(id)) } catch {
      message.error('模板预览加载失败'); setPreviewOpen(false)
    } finally { setPreviewLoading(false) }
  }
  const showJob = (id: string) => { setJobId(id); setJobOpen(true) }
  const showComparison = async (template: ParserTemplateItem) => {
    setComparisonTemplate(template); setComparisonOpen(true)
    setComparisonLoading(true); setComparison(null)
    try { setComparison(await parserApi.comparison(template.id)) } catch {
      message.error('模板对比加载失败'); setComparisonOpen(false)
    } finally { setComparisonLoading(false) }
  }

  const importPreviewMutation = useMutation(
    () => parserApi.previewImport({
      source_path: sourcePath.trim(), dataset_id: datasetId.trim() || undefined,
    }),
    {
      onSuccess: setImportPreview,
      onError: (error: any) => message.error(error?.response?.data?.detail || '导入预览失败'),
    },
  )
  const importMutation = useMutation(
    () => parserApi.executeImport({
      source_path: sourcePath.trim(), dataset_id: datasetId.trim() || undefined,
    }),
    {
      onSuccess: (result: any) => {
        message.success(`导入完成：新增 ${result.new_snapshot_count || 0}，重复 ${result.duplicate_snapshot_count || 0}`)
        setImportPreview(null); refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '导入失败'),
    },
  )

  const templateColumns = [
    { title: '厂商', dataIndex: 'vendor', width: 90 },
    { title: '型号 / 设备族', dataIndex: 'profile', width: 180 },
    { title: '命令', dataIndex: 'canonical_command' },
    { title: '类别', dataIndex: 'category', width: 130 },
    { title: '版本', dataIndex: 'version', width: 150 },
    { title: '状态', dataIndex: 'status', width: 120,
      render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
    { title: '操作', width: 190, render: (_: unknown, row: ParserTemplateItem) => (
      <Space>
        <Button icon={<EyeOutlined />} size="small" title="查看解析模板" onClick={() => showPreview(row.id)} />
        {row.status !== 'active' && row.status !== 'rejected' &&
          <Button icon={<CheckOutlined />} size="small" title="对比并激活" onClick={() => showComparison(row)} />}
        {row.status !== 'rejected' && row.status !== 'active' &&
          <Button icon={<CloseOutlined />} size="small" title="拒绝" onClick={() => action.mutate({ type: 'reject', id: row.id })} />}
        {row.status === 'active' &&
          <Button icon={<StopOutlined />} size="small" title="废弃" onClick={() => action.mutate({ type: 'deprecate', id: row.id })} />}
      </Space>
    )},
  ]

  const jobColumns = [
    { title: '类型', dataIndex: 'job_type', width: 150 },
    { title: '设备', dataIndex: 'profile', width: 180 },
    { title: '命令', dataIndex: 'canonical_command' },
    { title: '样本', dataIndex: 'sample_count', width: 70 },
    { title: '尝试', dataIndex: 'attempts', width: 70 },
    { title: '状态', dataIndex: 'status', width: 120,
      render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
    { title: '操作', width: 170, render: (_: unknown, row: ParserJobItem) => (
      <Space>
        <Button icon={<EyeOutlined />} size="small" title="查看任务" onClick={() => showJob(row.id)} />
        {['collecting', 'pending'].includes(row.status) &&
          <Button icon={<PlayCircleOutlined />} size="small" title="立即调度" onClick={() => action.mutate({ type: 'dispatch', id: row.id })} />}
        {['failed', 'cancelled'].includes(row.status) &&
          <Button icon={<ReloadOutlined />} size="small" title="重试" onClick={() => action.mutate({ type: 'retry', id: row.id })} />}
        {['collecting', 'pending', 'running', 'validating'].includes(row.status) &&
          <Button icon={<StopOutlined />} size="small" title="取消" onClick={() => action.mutate({ type: 'cancel', id: row.id })} />}
      </Space>
    )},
  ]

  const aliasColumns = [
    { title: '输入命令', dataIndex: 'alias' }, { title: '规范命令', dataIndex: 'canonical_command' },
    { title: '置信度', dataIndex: 'confidence', width: 100 },
    { title: '状态', dataIndex: 'status', width: 110,
      render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
  ]
  const profileColumns = [
    { title: '厂商', dataIndex: 'vendor', width: 100 }, { title: '型号', dataIndex: 'model' },
    { title: '设备族', dataIndex: 'family' }, { title: '置信度', dataIndex: 'confidence', width: 100 },
    { title: '状态', dataIndex: 'status', width: 110,
      render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
  ]
  const signatureColumns = [
    { title: '厂商', dataIndex: 'vendor', width: 90 }, { title: '型号', dataIndex: 'model', width: 180 },
    { title: '证据命令', dataIndex: 'evidence_command', width: 180 },
    { title: '识别规则', dataIndex: 'pattern' }, { title: '置信度', dataIndex: 'confidence', width: 90 },
  ]
  const datasetColumns = [
    { title: '数据集', dataIndex: 'dataset_id', width: 220 },
    { title: '设备', dataIndex: 'device_count', width: 70 },
    { title: 'Snapshot', dataIndex: 'snapshot_count', width: 90 },
    { title: '结构化覆盖率', dataIndex: 'structured_coverage_rate', width: 130,
      render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: '缺模板率', dataIndex: 'missing_template_rate', width: 110,
      render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: '解析失败率', dataIndex: 'parse_failure_rate', width: 110,
      render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: '主键稳定率', dataIndex: 'primary_key_stability_rate', width: 110,
      render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: '最后导入', dataIndex: 'last_imported_at', width: 190 },
  ]

  return <div>
    <Row gutter={16} style={{ marginBottom: 16 }}>
      <Col span={4}><Statistic title="模板" value={coverage?.templates_total || 0} /></Col>
      <Col span={4}><Statistic title="Active" value={coverage?.templates_active || 0} /></Col>
      <Col span={4}><Statistic title="待处理任务" value={coverage?.jobs_pending || 0} /></Col>
      <Col span={4}><Statistic title="缺模板" value={coverage?.missing_candidates || 0} /></Col>
      <Col span={4}><Statistic title="待修复" value={coverage?.repair_candidates || 0} /></Col>
      <Col span={4}><Button icon={<ReloadOutlined />} title="刷新" onClick={refresh} /></Col>
    </Row>
    <Tabs items={[
      { key: 'templates', label: '模板资产', children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space wrap>
            <Input.Search allowClear placeholder="搜索厂商、型号、命令或类别" style={{ width: 300 }}
              value={templateSearch} onChange={(event) => setTemplateSearch(event.target.value)} />
            <Select allowClear placeholder="模板状态" style={{ width: 150 }} value={templateStatus}
              onChange={setTemplateStatus} options={[
                { value: 'active', label: 'active' }, { value: 'draft', label: 'draft' },
                { value: 'pending_approval', label: 'pending_approval' },
                { value: 'rejected', label: 'rejected' }, { value: 'deprecated', label: 'deprecated' },
              ]} />
          </Space>
          <Table rowKey="id" loading={templatesLoading} dataSource={filteredTemplates} columns={templateColumns} pagination={{ pageSize: 20 }} size="small" scroll={{ x: 980 }} />
        </Space> },
      { key: 'jobs', label: '任务队列', children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Row justify="space-between" align="middle" gutter={[12, 12]}>
            <Col>
              <Space wrap>
                <Input.Search allowClear placeholder="搜索厂商、设备或命令" style={{ width: 280 }}
                  value={jobSearch} onChange={(event) => setJobSearch(event.target.value)} />
                <Select allowClear placeholder="任务状态" style={{ width: 150 }} value={jobStatus}
                  onChange={setJobStatus} options={[
                    'collecting', 'pending', 'running', 'validating', 'succeeded', 'failed', 'cancelled', 'superseded',
                  ].map((value) => ({ value, label: value }))} />
                <Select allowClear placeholder="任务类型" style={{ width: 170 }} value={jobType}
                  onChange={setJobType} options={[
                    { value: 'CREATE_TEMPLATE', label: 'CREATE_TEMPLATE' },
                    { value: 'REPAIR_TEMPLATE', label: 'REPAIR_TEMPLATE' },
                  ]} />
              </Space>
            </Col>
            <Col>
              <Space>
                <Typography.Text>自动调度</Typography.Text>
                <Switch checked={Boolean(dispatchControl?.auto_dispatch_enabled)}
                  loading={toggleDispatch.isLoading}
                  onChange={(checked) => toggleDispatch.mutate(checked)} />
              </Space>
            </Col>
          </Row>
          <Table rowKey="id" loading={jobsLoading} dataSource={filteredJobs} columns={jobColumns} pagination={{ pageSize: 20 }} size="small" scroll={{ x: 930 }} />
        </Space> },
      { key: 'aliases', label: `命令别名 (${aliases.length})`, children:
        <Table<ParserCommandAlias> rowKey="id" dataSource={aliases} columns={aliasColumns} pagination={{ pageSize: 20 }} size="small" /> },
      { key: 'profiles', label: `设备档案 (${deviceProfiles.length})`, children:
        <Table<ParserDeviceProfile> rowKey="id" dataSource={deviceProfiles} columns={profileColumns} pagination={{ pageSize: 20 }} size="small" /> },
      { key: 'signatures', label: `身份规则 (${identitySignatures.length})`, children:
        <Table<ParserIdentitySignature> rowKey="id" dataSource={identitySignatures} columns={signatureColumns} pagination={{ pageSize: 20 }} size="small" scroll={{ x: 900 }} /> },
      { key: 'datasets', label: `数据集覆盖率 (${datasets.length})`, children:
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Row gutter={[12, 12]}>
            <Col flex="auto"><Input value={sourcePath} onChange={(event) => {
              setSourcePath(event.target.value); setImportPreview(null)
            }} placeholder="巡检文件或目录绝对路径" /></Col>
            <Col flex="240px"><Input value={datasetId} onChange={(event) => {
              setDatasetId(event.target.value); setImportPreview(null)
            }} placeholder="数据集 ID，例如 2025Q4" /></Col>
            <Col><Button icon={<EyeOutlined />} loading={importPreviewMutation.isLoading}
              disabled={!sourcePath.trim()} onClick={() => importPreviewMutation.mutate()}>
              导入预览
            </Button></Col>
            <Col><Button type="primary" loading={importMutation.isLoading}
              disabled={!importPreview || importPreview.new_snapshots === 0}
              onClick={() => importMutation.mutate()}>
              确认导入
            </Button></Col>
          </Row>
          {importPreview && <Alert type={importPreview.parse_failed ? 'warning' : 'info'} showIcon
            message={`预览：${importPreview.file_count} 个文件，${importPreview.device_count} 台设备，新增 ${importPreview.new_snapshots}，重复 ${importPreview.duplicate_snapshots}`}
            description={`模板就绪 ${importPreview.ready}；缺模板 ${importPreview.template_missing}；解析失败 ${importPreview.parse_failed}；忽略 ${importPreview.ignored}；身份未知 ${importPreview.identity_unknown}`} />}
          <Table rowKey="dataset_id" dataSource={datasets} columns={datasetColumns}
            pagination={{ pageSize: 20 }} size="small" scroll={{ x: 1100 }} />
        </Space> },
    ]} />

    <Drawer title="解析模板预览" open={previewOpen} onClose={() => setPreviewOpen(false)}
      width={isMobile ? '100vw' : 720} destroyOnClose>
      {previewLoading ? <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}><Spin /></div>
        : preview ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="厂商">{preview.vendor}</Descriptions.Item>
            <Descriptions.Item label="型号">{preview.profile}</Descriptions.Item>
            <Descriptions.Item label="命令">{preview.canonical_command}</Descriptions.Item>
            <Descriptions.Item label="类别">{preview.category}</Descriptions.Item>
            <Descriptions.Item label="版本">{preview.version}</Descriptions.Item>
            <Descriptions.Item label="状态"><Tag color={statusColor(preview.status)}>{preview.status}</Tag></Descriptions.Item>
            <Descriptions.Item label="哈希校验" span={2}>
              <Tag color={preview.hash_valid ? 'green' : 'red'}>{preview.hash_valid ? '通过' : '失败'}</Tag>
              <Typography.Text copyable style={{ marginLeft: 8 }}>{preview.sha256}</Typography.Text>
            </Descriptions.Item>
          </Descriptions>
          <Typography.Title level={5} style={{ margin: 0 }}>TextFSM 模板</Typography.Title>
          <Typography.Paragraph copyable style={{ ...codePanelStyle, maxHeight: 480 }}>{preview.content}</Typography.Paragraph>
          <Typography.Title level={5} style={{ margin: 0 }}>验证结果</Typography.Title>
          <Typography.Paragraph style={{ ...codePanelStyle, maxHeight: 280, whiteSpace: 'pre-wrap' }}>
            {JSON.stringify(preview.validation, null, 2)}
          </Typography.Paragraph>
        </Space> : null}
    </Drawer>

    <Drawer title="模板激活前对比" open={comparisonOpen}
      onClose={() => { setComparisonOpen(false); setComparisonTemplate(null); setComparison(null) }}
      width={isMobile ? '100vw' : 920} destroyOnClose
      extra={<Button type="primary" icon={<CheckOutlined />}
        disabled={!comparison || comparison.sample_count === 0
          || comparison.passed_samples !== comparison.sample_count}
        loading={action.isLoading}
        onClick={() => comparisonTemplate && action.mutate(
          { type: 'activate', id: comparisonTemplate.id },
          { onSuccess: () => setComparisonOpen(false) },
        )}>激活模板</Button>}>
      {comparisonLoading ? <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}><Spin /></div>
        : comparison ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert type={comparison.contract_changed ? 'warning' : 'success'} showIcon
            message={comparison.contract_changed ? '数据契约发生变化' : '数据契约未变化'}
            description={`回归通过 ${comparison.passed_samples}/${comparison.sample_count}；当前 active 模板 ${comparison.active_template_id || '无'}`} />
          <Table rowKey="snapshot_id" size="small" pagination={{ pageSize: 10 }}
            dataSource={comparison.rows} scroll={{ x: 1100 }} columns={[
              { title: '数据集', dataIndex: 'dataset_id', width: 150 },
              { title: '设备', dataIndex: 'device_id', width: 200 },
              { title: '当前模板', width: 130, render: (_: unknown, row: any) =>
                <Tag color={statusColor(row.current?.status || 'unavailable')}>{row.current?.status || '无'}</Tag> },
              { title: '候选模板', width: 130, render: (_: unknown, row: any) =>
                <Tag color={statusColor(row.proposed.status)}>{row.proposed.status}</Tag> },
              { title: '结果变化', dataIndex: 'changed', width: 100,
                render: (value: boolean) => <Tag color={value ? 'orange' : 'green'}>{value ? '有变化' : '一致'}</Tag> },
              { title: '候选结构化结果', render: (_: unknown, row: any) =>
                <Typography.Text code>{JSON.stringify(row.proposed.records || row.proposed.failure_code || [])}</Typography.Text> },
            ]} />
          <Typography.Title level={5} style={{ margin: 0 }}>候选数据契约</Typography.Title>
          <Typography.Paragraph style={{ ...codePanelStyle, maxHeight: 260, whiteSpace: 'pre-wrap' }}>
            {JSON.stringify(comparison.candidate_contract, null, 2)}
          </Typography.Paragraph>
        </Space> : null}
    </Drawer>

    <Drawer title="生成任务详情" open={jobOpen} onClose={() => { setJobOpen(false); setJobId(null) }}
      width={isMobile ? '100vw' : 760} destroyOnClose>
      {jobLoading ? <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}><Spin /></div>
        : jobDetail ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="任务类型">{jobDetail.job_type}</Descriptions.Item>
            <Descriptions.Item label="状态"><Tag color={statusColor(jobDetail.status)}>{jobDetail.status}</Tag></Descriptions.Item>
            <Descriptions.Item label="厂商">{jobDetail.vendor}</Descriptions.Item>
            <Descriptions.Item label="设备">{jobDetail.profile}</Descriptions.Item>
            <Descriptions.Item label="命令" span={2}>{jobDetail.canonical_command}</Descriptions.Item>
            <Descriptions.Item label="样本数">{jobDetail.sample_count}</Descriptions.Item>
            <Descriptions.Item label="尝试次数">{jobDetail.attempts}</Descriptions.Item>
            <Descriptions.Item label="Celery Task" span={2}>{jobDetail.celery_task_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="失败代码">{jobDetail.failure_code || '-'}</Descriptions.Item>
            <Descriptions.Item label="错误">{jobDetail.last_error || '-'}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{jobDetail.created_at || '-'}</Descriptions.Item>
            <Descriptions.Item label="调度时间">{jobDetail.dispatched_at || '-'}</Descriptions.Item>
            <Descriptions.Item label="开始时间">{jobDetail.started_at || '-'}</Descriptions.Item>
            <Descriptions.Item label="完成时间">{jobDetail.completed_at || '-'}</Descriptions.Item>
          </Descriptions>
          <Typography.Title level={5} style={{ margin: 0 }}>样本引用</Typography.Title>
          <Table rowKey="id" size="small" pagination={false} dataSource={jobDetail.samples || []}
            scroll={{ x: 780 }} columns={[
              { title: '设备', dataIndex: 'device_id', width: 170 },
              { title: 'Snapshot', dataIndex: 'snapshot_id', width: 250 },
              {
                title: '数据源',
                dataIndex: 'snapshot_source',
                render: () => 'PostgreSQL',
              },
              { title: '数据集', dataIndex: 'dataset_id' },
            ]} />
        </Space> : null}
    </Drawer>
  </div>
}

export default ParserControlTab
