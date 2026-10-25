import { commandApi } from '../commandApi'
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Form, Input, InputNumber, Modal, Select, Space, Table, Typography, message } from 'antd'
import { useQuery } from 'react-query'
import { type DeviceDynamicGroup, type ParserCommandProfile, type ParserCommandProfileImpactPreview } from '@netops/services/api'

interface Props {
  open: boolean
  onSaved: () => void
  selectedDeviceIds: string[]
  currentSelector: Record<string, unknown>
  groups: string[]
  dynamicGroups: DeviceDynamicGroup[]
}
interface BindingValues {
  profile_version_id: string
  target_type: 'selected' | 'filters' | 'group' | 'dynamic_group'
  group?: string
  dynamic_group_id?: string
  priority: number
  reason: string
}
const commandsFor = (profile?: ParserCommandProfile) =>
  (profile?.items || []).filter(item => item.action === 'patrol').map(item => item.command)
const errorMessage = (error: unknown): string => {
  const candidate = error as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = candidate.response?.data?.detail
  return typeof detail === 'string' ? detail : candidate.message || '操作失败，请重试'
}
async function loadCommandProfiles() {
  const profiles: ParserCommandProfile[] = []
  for (let offset = 0; ; offset += 200) {
    const page = await commandApi.commandProfiles({ limit: 200, offset })
    profiles.push(...page.items)
    if (!page.items.length || profiles.length >= page.total) return profiles
  }
}

export default function PatrolCommandConfiguration({ open, onSaved, selectedDeviceIds, currentSelector, groups, dynamicGroups }: Props) {
  const [form] = Form.useForm<BindingValues>()
  const [editor] = Form.useForm<{ name: string; commands: string }>()
  const [editorOpen, setEditorOpen] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<ParserCommandProfileImpactPreview | null>(null)
  const previewEpoch = useRef(0)
  const { data: profiles = [], isLoading, error: profilesError, refetch } = useQuery(['patrol-command-set-options'], loadCommandProfiles, { enabled: open, refetchOnWindowFocus: false })
  const profileId = Form.useWatch('profile_version_id', form)
  const targetType = Form.useWatch('target_type', form)
  const selected = profiles.find(item => item.profile_version_id === profileId)
  const commands = commandsFor(selected)
  const options = useMemo(() => profiles.filter(item =>
    item.profile_version_id && item.version_status !== 'deprecated' && commandsFor(item).length > 0 && !(item.check_item_ids?.length),
  ).map(item => ({ value: item.profile_version_id!, label: `${item.profile_name} · v${item.version} · ${commandsFor(item).length} 条命令${item.version_status === 'active' ? '' : ' · 草稿'}` })), [profiles])
  const scopeKey = JSON.stringify([selectedDeviceIds, currentSelector, dynamicGroups])
  useEffect(() => { previewEpoch.current += 1; setPreview(null) }, [scopeKey, open])
  useEffect(() => {
    if (open) {
      form.resetFields()
      form.setFieldsValue({ target_type: selectedDeviceIds.length ? 'selected' : Object.keys(currentSelector).length ? 'filters' : 'group', priority: 100 })
      setPreview(null); setError(''); setEditorOpen(false)
    }
    // Scope updates invalidate the preview without resetting a user's selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, form])
  const selectorFor = (values: BindingValues) => {
    if (values.target_type === 'selected') {
      if (!selectedDeviceIds.length) throw new Error('请先勾选设备')
      return { device_id: selectedDeviceIds }
    }
    if (values.target_type === 'group') {
      if (!values.group) throw new Error('请选择设备分组')
      return { group: values.group }
    }
    if (values.target_type === 'dynamic_group') {
      const group = dynamicGroups.find(item => item.id === values.dynamic_group_id)
      if (!group) throw new Error('请选择动态分组')
      if (!Object.keys(group.selector).length) throw new Error('动态分组范围不能为空')
      return group.selector
    }
    if (!Object.keys(currentSelector).length) throw new Error('请先筛选设备或选择设备分组')
    return currentSelector
  }
  const perform = async (name: string, action: () => Promise<void>) => {
    setBusy(name); setError('')
    try { await action() } catch (error) {
      if (!(error as { errorFields?: unknown }).errorFields) setError(errorMessage(error))
    } finally { setBusy('') }
  }
  const inspect = () => perform('preview', async () => {
    const epoch = ++previewEpoch.current
    const values = await form.validateFields(['profile_version_id', 'target_type', 'group', 'dynamic_group_id'])
    setPreview(null)
    const result = await commandApi.commandProfileImpactPreview({ profile_version_id: values.profile_version_id, selector: selectorFor(values), mode: 'production', action: 'patrol', limit: 100, save_evidence: true })
    if (epoch === previewEpoch.current) setPreview(result)
  })
  const bind = () => perform('bind', async () => {
    const values = await form.validateFields()
    if (!preview?.evidence_saved || preview.matched_device_count < 1 || preview.profile_version_id !== values.profile_version_id) throw new Error('请先预览设备和命令')
    if (selected?.version_status !== 'active') await commandApi.activateCommandProfile(values.profile_version_id, values.reason)
    const binding = await commandApi.upsertCommandProfileBinding({ profile_version_id: values.profile_version_id, selector: selectorFor(values), priority: values.priority, status: 'disabled' })
    await commandApi.enableCommandProfileBinding(binding.binding_id, values.reason)
    message.success('巡检命令集已绑定')
    await refetch(); onSaved(); setPreview(null)
  })
  const create = () => perform('create', async () => {
    const values = await editor.validateFields()
    if (profiles.some(profile => profile.profile_name === values.name.trim())) throw new Error('该名称已存在，请使用新名称，避免影响其他设备的命令集版本')
    const lines = [...new Set(values.commands.split(/\r?\n/).map(line => line.trim()).filter(Boolean))]
    if (!lines.length) throw new Error('至少填写一条命令')
    const result = await commandApi.upsertCommandProfile({ name: values.name.trim(), action: 'patrol', commands: lines, status: 'draft' })
    await refetch()
    form.setFieldValue('profile_version_id', result.profile_version_id)
    setPreview(null); setEditorOpen(false)
    message.success('命令集已创建，请预览后绑定')
  })
  return <>
    <section aria-label="命令集配置">
      <Space direction="vertical" size={20} style={{ width: '100%' }}>
        <Typography.Text type="secondary">设备按命令集逐条采集。检查项及解析规则只处理采集结果，不增加或替换命令。</Typography.Text>
        {error && <Alert type="error" showIcon message={error} />}
        {!!profilesError && <Alert type="error" showIcon message="命令集加载失败" description={errorMessage(profilesError)} action={<Button onClick={() => refetch()}>重试</Button>} />}
        <Form form={form} layout="vertical" disabled={!!busy} onValuesChange={() => { previewEpoch.current += 1; setPreview(null) }}>
          <Form.Item name="profile_version_id" label="巡检命令集" rules={[{ required: true, message: '请选择命令集' }]}>
            <Select aria-label="巡检命令集" loading={isLoading} showSearch optionFilterProp="label" options={options} placeholder="选择已有命令集" />
          </Form.Item>
          <Space style={{ marginBottom: 12 }}>
            <Button onClick={() => { editor.resetFields(); setEditorOpen(true) }}>新建命令集</Button>
            <Button disabled={!commands.length} onClick={() => { editor.setFieldsValue({ name: `${selected?.profile_name} 副本`, commands: commands.join('\n') }); setEditorOpen(true) }}>复制并修改</Button>
          </Space>
          {commands.length > 0 && <div style={{ marginBottom: 20 }}>
            <Typography.Text strong>执行命令 · {commands.length} 条</Typography.Text>
            <pre style={{ marginTop: 8, padding: 12, background: '#f6f7f9', borderRadius: 6, maxHeight: 240, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{commands.map((command, index) => `${index + 1}. ${command}`).join('\n')}</pre>
          </div>}
          {!isLoading && !profilesError && !options.length && <Alert style={{ marginBottom: 20 }} type="info" message="还没有命令集，请先新建。原检查项方案继续保留，不会自动转换或覆盖。" />}
          <Form.Item name="target_type" label="绑定设备范围" rules={[{ required: true }]}><Select options={[
            { value: 'selected', label: `已勾选设备（${selectedDeviceIds.length} 台）`, disabled: !selectedDeviceIds.length },
            { value: 'group', label: '设备分组' }, { value: 'dynamic_group', label: '动态分组' },
            { value: 'filters', label: '当前筛选条件', disabled: !Object.keys(currentSelector).length },
          ]} /></Form.Item>
          {targetType === 'group' && <Form.Item name="group" label="设备分组" rules={[{ required: true, message: '请选择设备分组' }]}><Select showSearch options={groups.map(value => ({ value, label: value }))} /></Form.Item>}
          {targetType === 'dynamic_group' && <Form.Item name="dynamic_group_id" label="动态分组" rules={[{ required: true, message: '请选择动态分组' }]}><Select options={dynamicGroups.filter(item => item.status === 'active').map(item => ({ value: item.id, label: item.name }))} /></Form.Item>}
          <Form.Item name="reason" label="变更说明" rules={[{ required: true, whitespace: true, message: '请填写变更说明' }]}><Input placeholder="例如：核心交换机日常巡检" /></Form.Item>
          <details><summary>范围重叠与优先级</summary>
            <Typography.Paragraph type="secondary">相同范围重新绑定会替换原绑定。不同范围重叠时，数字越小越优先；优先级相同按绑定创建顺序生效，不合并命令集。</Typography.Paragraph>
            <Form.Item name="priority" label="绑定优先级" rules={[{ required: true }]}><InputNumber min={0} precision={0} /></Form.Item>
            {targetType === 'filters' && <pre>{JSON.stringify(currentSelector, null, 2)}</pre>}
          </details>
        </Form>
        {preview && <>
          <Alert type={preview.blocked_command_count ? 'warning' : 'success'} showIcon message={`${preview.matched_device_count} 台设备 · 每台 ${preview.command_count} 条命令`}
            description={preview.blocked_command_count ? `${preview.blocked_command_count} 个设备命令尚未具备完整解析能力。仍按命令集采集并保留原文，检查结果会标明缺口。` : '已核对命令集和匹配范围，确认后生效。'} />
          <Table rowKey={row => row.device_id || row.device_name || row.ip || ''} size="small" dataSource={preview.rows} pagination={{ pageSize: 10 }} scroll={{ x: 600 }} columns={[
            { title: '设备', dataIndex: 'device_name' }, { title: '管理地址', dataIndex: 'ip' },
            { title: '命令数', dataIndex: 'command_count' }, { title: '待补齐解析', dataIndex: 'blocked_count' },
          ]} />
          {preview.matched_device_count > preview.rows.length && <Typography.Text type="secondary">已检查全部匹配设备，列表展示前 {preview.rows.length} 台。</Typography.Text>}
        </>}
      </Space>
      <div style={{ marginTop: 24, paddingTop: 16, borderTop: '1px solid #e5eaf1' }}>
        <Space><Button onClick={inspect} disabled={!!busy || !commands.length} loading={busy === 'preview'}>预览设备与命令</Button><Button type="primary" onClick={bind} loading={busy === 'bind'} disabled={!!busy || !preview?.evidence_saved || !preview.matched_device_count}>{selected?.version_status === 'active' ? '确认绑定' : '启用并绑定'}</Button></Space>
      </div>
    </section>
    <Modal title="新建巡检命令集" open={editorOpen} onCancel={() => { if (!busy) setEditorOpen(false) }} closable={!busy} maskClosable={!busy} keyboard={!busy} onOk={create} okText="保存命令集" confirmLoading={busy === 'create'} destroyOnClose>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      <Form form={editor} layout="vertical" disabled={!!busy}>
        <Form.Item name="name" label="命令集名称" rules={[{ required: true, whitespace: true, message: '请填写名称' }]}><Input placeholder="例如：H3C 核心交换机日常巡检" /></Form.Item>
        <Form.Item name="commands" label="巡检命令（每行一条，按顺序执行）" rules={[{ required: true, whitespace: true, message: '请填写命令' }]}><Input.TextArea aria-label="巡检命令" rows={9} placeholder={'display version\ndisplay cpu-usage\ndisplay memory'} style={{ fontFamily: 'Consolas, monospace' }} /></Form.Item>
      </Form>
    </Modal>
  </>
}
