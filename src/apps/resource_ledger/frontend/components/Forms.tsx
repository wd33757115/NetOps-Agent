import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Divider, Form, Input, InputNumber, Modal, Radio, Select, Space as Gap, message, type FormInstance } from 'antd'
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from 'react-query'
import {
  ADDRESS_KINDS, BINDING_FIELD, DIMENSIONS, LEDGER_KEYS, PREFIX_STATES, ZONE_KINDS, blockSize, errorText, ipValue, ledger,
  type AddressRow, type Dimension, type Level, type Overview, type PathStep, type PrefixDetail, type PrefixRow, type Site, type Space, type Zone,
} from '../api'

type Done = () => void

export function useRefresh() {
  const client = useQueryClient()
  return () => LEDGER_KEYS.forEach(key => client.invalidateQueries(key))
}

/** Zone choices read as "region2 生产业务区（西安机房）": the site disambiguates repeated names. */
export function zoneOptions(overview?: Overview, zones = overview?.zones || []) {
  const sites = new Map((overview?.sites || []).map(site => [site.id, site.name]))
  return zones.map(zone => ({
    value: zone.id,
    label: `${zone.name}${zone.title ? ` ${zone.title}` : ''}（${sites.get(zone.site_id) || '未知机房'}）`,
  }))
}

export function useSubmit<T, R = unknown>(action: (values: T) => Promise<R>, success: string, done: (saved: R) => void) {
  const [busy, setBusy] = useState(false)
  return {
    busy,
    submit: async (values: T) => {
      setBusy(true)
      try {
        const saved = await action(values)
        message.success(success)
        done(saved)
      } catch (error) {
        message.error(errorText(error))
      } finally {
        setBusy(false)
      }
    },
  }
}

export function FormModal<T extends object>({ open, title, subtitle, okText, busy, onClose, onSubmit, initial, width = 560, okDisabled, children }: {
  open: boolean; title: string; subtitle?: React.ReactNode; okText: string; busy: boolean; onClose: Done; onSubmit: (values: T) => void
  initial?: Partial<T>; width?: number; okDisabled?: boolean; children: React.ReactNode | ((form: FormInstance<T>) => React.ReactNode)
}) {
  const [form] = Form.useForm<T>()
  // Fill the form when it opens only, so a background refetch never wipes what was typed.
  const latest = useRef(initial)
  latest.current = initial
  useEffect(() => { if (open) { form.resetFields(); form.setFieldsValue((latest.current || {}) as never) } }, [open, form])
  return <Modal open={open} width={width} onCancel={onClose} destroyOnClose
    title={<div>{title}{subtitle && <div className="rl-modal-sub">{subtitle}</div>}</div>}
    footer={[<Button key="cancel" onClick={onClose}>取消</Button>,
      <Button key="ok" type="primary" loading={busy} disabled={okDisabled} onClick={() => form.submit()}>{okText}</Button>]}>
    <Form<T> form={form} layout="vertical" onFinish={onSubmit}>{typeof children === 'function' ? children(form) : children}</Form>
  </Modal>
}

const Row = ({ columns = '1fr 1fr', children }: { columns?: string; children: React.ReactNode }) =>
  <div className="rl-form-row" style={{ gridTemplateColumns: columns }}>{children}</div>

// --------------------------------------------------------------------------- bindings

type Inherited = Partial<Record<Exclude<Dimension, 'custom'>, string>>

/** What a block inherits from the blocks above it, including what bound objects imply. */
export function inheritedFrom(path: PathStep[], overview?: Overview): Inherited {
  const result: Inherited = {}
  for (const step of path) if (step.dimension !== 'custom' && step.id) result[step.dimension] = step.id
  const zone = overview?.zones.find(z => z.id === result.zone)
  if (zone) result.site = result.site || zone.site_id
  const site = overview?.sites.find(s => s.id === result.site)
  if (site) result.region = result.region || site.region_id
  const sub = overview?.subsystems.find(s => s.id === result.subsystem)
  if (sub) result.system = result.system || sub.system_id
  return result
}

/** The object a block of this level binds, narrowed to what fits under the inherited ones; new names can be created in place. */
function BindingSelect({ dimension, inherited, overview, value, onChange }: {
  dimension: Exclude<Dimension, 'custom'>; inherited: Inherited; overview?: Overview; value?: string; onChange?: (value?: string) => void
}) {
  const refresh = useRefresh()
  const [typed, setTyped] = useState('')
  const [made, setMade] = useState<{ value: string; label: string }[]>([])
  const [busy, setBusy] = useState(false)
  useEffect(() => { setMade([]); setTyped('') }, [dimension])
  const options = useMemo(() => {
    if (!overview) return []
    const regionOf = new Map(overview.sites.map(s => [s.id, s.region_id]))
    const systems = new Map(overview.systems.map(s => [s.id, s.name]))
    switch (dimension) {
      case 'region': return overview.regions.map(r => ({ value: r.id, label: r.name }))
      case 'site': return overview.sites.filter(s => !inherited.region || s.region_id === inherited.region).map(s => ({ value: s.id, label: s.name }))
      case 'zone': return zoneOptions(overview, overview.zones.filter(z =>
        inherited.site ? z.site_id === inherited.site : !inherited.region || regionOf.get(z.site_id) === inherited.region))
      case 'system': return overview.systems.map(s => ({ value: s.id, label: s.name }))
      case 'subsystem': return overview.subsystems.filter(s => !inherited.system || s.system_id === inherited.system)
        .map(s => ({ value: s.id, label: inherited.system ? s.name : `${s.name}（${systems.get(s.system_id) || ''}）` }))
    }
  }, [overview, dimension, inherited])
  const all = [...options, ...made.filter(m => !options.some(o => o.value === m.value))]
  // Sites, zones and subsystems are created under what the block inherits.
  const parent = { region: 'ok', system: 'ok', site: inherited.region, zone: inherited.site, subsystem: inherited.system }[dimension]
  const name = typed.trim()
  const creatable = name && parent && !all.some(o => o.label.split('（')[0].split(' ')[0] === name)
  const create = async () => {
    setBusy(true)
    try {
      const body = { region: { name }, system: { name }, site: { name, region_id: inherited.region, kind: '自建' },
        zone: { name, site_id: inherited.site, kind: '生产' }, subsystem: { name, system_id: inherited.system } }[dimension]
      const kind = ({ region: 'regions', site: 'sites', zone: 'zones', system: 'systems', subsystem: 'subsystems' } as const)[dimension]
      const created = await ledger.save(kind, body)
      setMade(previous => [...previous, { value: created.id, label: name }])
      onChange?.(created.id)
      setTyped('')
      message.success(`已新建${DIMENSIONS[dimension]}“${name}”`)
      refresh()
    } catch (error) {
      message.error(errorText(error))
    } finally {
      setBusy(false)
    }
  }
  return <Select showSearch allowClear optionFilterProp="label" value={value} onChange={onChange} options={all}
    placeholder={`选择${DIMENSIONS[dimension]}，没有的可以直接输入名称新建`} searchValue={typed} onSearch={setTyped}
    // Never empty: antd closes a dropdown with nothing to show, which would hide the create button.
    notFoundContent={creatable ? <span className="rl-muted">还没有“{name}”</span> : <span className="rl-muted">{parent ? '没有匹配项' : `需要上级网段先划到${dimension === 'subsystem' ? '业务系统' : dimension === 'zone' ? '机房' : '地域'}才能新建`}</span>}
    dropdownRender={menu => <>
      {menu}
      {creatable && <><Divider style={{ margin: '4px 0' }} />
        <Button type="text" block icon={<PlusOutlined aria-hidden="true" />} loading={busy} style={{ textAlign: 'left' }}
          onMouseDown={event => event.preventDefault()} onClick={create}>新建{DIMENSIONS[dimension]}“{name}”</Button></>}
    </>} />
}

function BindingFields({ level, inherited, overview }: { level?: Level | null; inherited: Inherited; overview?: Overview }) {
  if (!level || level.dimension === 'custom') {
    return <Form.Item name="label" label={level ? `${level.title}名称` : '名称'} rules={level ? [{ required: true, message: '请填写名称' }] : []}
      extra={level ? undefined : '不分层的网段只记名称，归属沿用上级。'}><Input maxLength={64} /></Form.Item>
  }
  const dimension = level.dimension
  return <Form.Item name={BINDING_FIELD[dimension]} label={level.title} rules={[{ required: true, message: `请选择${DIMENSIONS[dimension]}` }]}>
    <BindingSelect dimension={dimension} inherited={inherited} overview={overview} />
  </Form.Item>
}

function InheritBar({ path }: { path: PathStep[] }) {
  return <div className="rl-inherit">
    <span className="rl-muted">继承</span>
    {path.length ? path.map((step, index) => <React.Fragment key={step.prefix_id}>
      {index > 0 && <i aria-hidden="true">›</i>}<span title={step.level}>{step.name}</span>
    </React.Fragment>) : <span className="rl-muted">无，这是地址资源的第一级划分</span>}
  </div>
}

const sizeLabel = (length: number) => {
  const size = blockSize(length)
  return `/${length}（${size >= 1024 ? `${size / 1024}K` : size} 个地址）`
}

// --------------------------------------------------------------------------- carving

type CarveValues = {
  label?: string; purpose?: string; vlan?: number; gateway?: string; status: string; owner?: string; ticket?: string; note?: string
} & Partial<Record<'region_id' | 'site_id' | 'zone_id' | 'system_id' | 'subsystem_id', string>>

/** Carve the next block of a size out of a block, binding the object of the chosen level. */
export function CarveModal({ open, parentId, within, overview, onClose, onSaved }: {
  open: boolean; parentId?: string; within?: { first: string; last: string }; overview?: Overview; onClose: Done; onSaved: (id: string) => void
}) {
  const [level, setLevel] = useState<string>()
  const [length, setLength] = useState<number>()
  const [network, setNetwork] = useState<string>()
  const [choosing, setChoosing] = useState(false)
  useEffect(() => { if (open) { setLevel(undefined); setLength(undefined); setNetwork(undefined); setChoosing(false) } }, [open, parentId])
  const options = useQuery(['rl-carve', parentId, level, length], () => ledger.carve(parentId!, { level_id: level || undefined, length }),
    { enabled: open && Boolean(parentId), keepPreviousData: true, retry: false })
  const data = options.data
  useEffect(() => { if (data && level === undefined) setLevel(data.level_id || '') }, [data, level])
  useEffect(() => {
    if (!data) return
    const inGap = within && data.candidates.find(c => { const v = ipValue(c.split('/')[0]); return ipValue(within.first) <= v && v <= ipValue(within.last) })
    setNetwork(previous => previous && data.candidates.includes(previous) ? previous : inGap || data.candidates[0])
  }, [data, within])
  const chosen = data?.levels.find(lv => lv.id === level) || null
  const parent = data?.parent
  const inherited = useMemo(() => inheritedFrom(parent?.path || [], overview), [parent, overview])
  const { busy, submit } = useSubmit<CarveValues>(async values => {
    const created = await ledger.createPrefix({ ...values, parent_id: parentId, network, level_id: level || undefined })
    onSaved(created.id)
  }, '已划分', () => undefined)
  const lengths = parent ? Array.from({ length: Math.max(0, 30 - parent.length) }, (_, i) => parent.length + 1 + i) : []
  const size = data?.length
  const afterPlanned = parent && size && parent.usage.kind === 'planned'
    ? Math.round((parent.usage.used + blockSize(size)) * 100 / parent.usage.total)
    : parent && size ? Math.round(blockSize(size) * 100 / blockSize(parent.length)) : 0
  return <FormModal<CarveValues> open={open} width={680} busy={busy} okText="划分" onClose={onClose} onSubmit={submit}
    okDisabled={!network || options.isFetching} initial={{ status: '使用中' }}
    title="划分下一级" subtitle={parent && `从 ${parent.network}${parent.title ? `（${parent.level ? `${parent.level.title} ` : ''}${parent.title}）` : ''}中划出一块`}>
    {options.error ? <Alert type="error" showIcon message={errorText(options.error)} style={{ marginBottom: 14 }} /> : null}
    {parent && <InheritBar path={parent.path} />}
    <Form.Item label="层级" required extra="默认是下一级；也可以跳过中间层级，或划一个不分层的网段。">
      <Radio.Group optionType="button" value={level} onChange={event => { setLevel(event.target.value); setLength(undefined) }}
        options={[...(data?.levels || []).map(lv => ({ value: lv.id, label: lv.title })), { value: '', label: '不分层' }]} />
    </Form.Item>
    <BindingFields level={chosen} inherited={inherited} overview={overview} />
    <Row>
      <Form.Item label="大小" required>
        <Select value={size} onChange={value => setLength(value)} showSearch optionFilterProp="label"
          options={lengths.map(value => ({ value, label: `${sizeLabel(value)}${chosen && value === data?.levels.find(lv => lv.id === chosen.id)?.suggested_length ? '，层级默认' : ''}` }))} />
      </Form.Item>
      <Form.Item name="status" label="状态"><Select options={PREFIX_STATES.map(value => ({ value }))} /></Form.Item>
    </Row>
    <Form.Item label="网段" required>
      {data && !data.candidates.length
        ? <span className="rl-error-text">{parent?.network} 里已经没有空闲的 /{size}，请换一个更小的大小</span>
        : choosing
          ? <Select value={network} onChange={setNetwork} options={(data?.candidates || []).map(value => ({ value, label: value }))} />
          : <div className="rl-pick"><strong>{network || '—'}</strong><span className="rl-muted">{network === data?.candidates[0] ? `下一个可用的 /${size}` : `未划分的 /${size}`}</span>
              {Boolean(data?.candidates.length && data.candidates.length > 1) && <Button type="link" onClick={() => setChoosing(true)}>改选</Button>}</div>}
      {data && data.count > 0 && parent && <div className="rl-hint">
        划出后 {parent.title || parent.network} 还剩 {data.count - 1} 个 /{size} 可用（已划分 {afterPlanned}%）
        {afterPlanned >= 75 && '，建议提前规划扩容'}。</div>}
    </Form.Item>
    <Form.Item name="purpose" label="用途说明"><Input maxLength={200} placeholder="例如 Web 与应用" /></Form.Item>
    <Row columns="1fr 1fr 1fr 1fr">
      <Form.Item name="vlan" label="VLAN"><InputNumber min={1} max={4094} style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="gateway" label="网关"><Input maxLength={15} placeholder="可留空" /></Form.Item>
      <Form.Item name="owner" label="负责人"><Input maxLength={64} /></Form.Item>
      <Form.Item name="ticket" label="申请工单号"><Input maxLength={80} /></Form.Item>
    </Row>
  </FormModal>
}

// --------------------------------------------------------------------------- blocks

type BlockValues = CarveValues & { level_id?: string; network?: string; space_id?: string }

/** A new address resource: a top block written out in full. */
export function ResourceModal({ open, spaces, onClose, onSaved }: { open: boolean; spaces: Space[]; onClose: Done; onSaved: (id: string) => void }) {
  const { busy, submit } = useSubmit<BlockValues>(async values => {
    const created = await ledger.createPrefix(values)
    onSaved(created.id)
  }, '地址资源已新增', () => undefined)
  return <FormModal<BlockValues> open={open} title="新增地址资源" subtitle="总体的网络资源，例如 10.1.0.0/16；之后按层级逐级划分。"
    okText="新增" busy={busy} onClose={onClose} onSubmit={submit} initial={{ status: '使用中', space_id: spaces[0]?.id }} width={600}>
    <Row columns="1fr 1.4fr">
      <Form.Item name="network" label="网段" rules={[{ required: true, message: '请填写网段' }]} extra="写成 10.1.0.0/16，必须是网段起始地址。">
        <Input maxLength={18} placeholder="10.1.0.0/16" />
      </Form.Item>
      <Form.Item name="label" label="名称" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={64} placeholder="西部生产地址" /></Form.Item>
    </Row>
    <Form.Item name="purpose" label="说明"><Input maxLength={200} placeholder="例如 西安、成都生产" /></Form.Item>
    {spaces.length > 1 && <Form.Item name="space_id" label="路由域" rules={[{ required: true }]}
      extra="同一段私网地址可以在不同路由域里各用一次。"><Select options={spaces.map(s => ({ value: s.id, label: s.name }))} /></Form.Item>}
    <Row columns="1fr 1fr 1fr">
      <Form.Item name="status" label="状态"><Select options={PREFIX_STATES.map(value => ({ value }))} /></Form.Item>
      <Form.Item name="owner" label="负责人"><Input maxLength={64} /></Form.Item>
      <Form.Item name="ticket" label="申请工单号"><Input maxLength={80} /></Form.Item>
    </Row>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

/** Edit a block in place: its level and binding, and the descriptive fields. */
export function BlockModal({ open, prefix, overview, onClose, onSaved }: { open: boolean; prefix?: PrefixRow | null; overview?: Overview; onClose: Done; onSaved: Done }) {
  const { busy, submit } = useSubmit<BlockValues>(values => ledger.updatePrefix(prefix!.id, values), '网段已保存', onSaved)
  const above = prefix ? (prefix.level ? prefix.path.slice(0, -1) : prefix.path) : []
  const inherited = useMemo(() => inheritedFrom(above, overview), [above, overview])
  const initial = useMemo(() => prefix ? {
    level_id: prefix.level?.id || '', region_id: prefix.region_id || undefined, site_id: prefix.site_id || undefined,
    zone_id: prefix.zone_id || undefined, system_id: prefix.system_id || undefined, subsystem_id: prefix.subsystem_id || undefined,
    label: prefix.label || undefined, purpose: prefix.purpose || undefined, vlan: prefix.vlan || undefined, gateway: prefix.gateway || undefined,
    status: prefix.status, owner: prefix.owner || undefined, ticket: prefix.ticket || undefined, note: prefix.note || undefined,
  } : undefined, [prefix])
  return <FormModal<BlockValues> open={open} title={`编辑网段 ${prefix?.network || ''}`} okText="保存" busy={busy} onClose={onClose}
    onSubmit={values => submit({ ...values, level_id: values.level_id || undefined })} initial={initial} width={640}
    subtitle="网段地址不能修改；需要调整时请新划一块再删除旧的。">
    {form => <>
      <InheritBar path={above} />
      <Form.Item name="level_id" label="层级">
        <Select options={[{ value: '', label: prefix?.parent_id ? '不分层' : '地址资源（不分层）' },
          ...(overview?.levels || []).map(lv => ({ value: lv.id, label: lv.title }))]} />
      </Form.Item>
      <Form.Item noStyle shouldUpdate={(a, b) => a.level_id !== b.level_id}>
        {() => <BindingFields level={overview?.levels.find(lv => lv.id === form.getFieldValue('level_id'))} inherited={inherited} overview={overview} />}
      </Form.Item>
      <Form.Item name="purpose" label="用途说明"><Input maxLength={200} /></Form.Item>
      <Row columns="1fr 1fr 1fr">
        <Form.Item name="vlan" label="VLAN"><InputNumber min={1} max={4094} style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="gateway" label="网关"><Input maxLength={15} /></Form.Item>
        <Form.Item name="status" label="状态"><Select options={PREFIX_STATES.map(value => ({ value }))} /></Form.Item>
      </Row>
      <Row>
        <Form.Item name="owner" label="负责人"><Input maxLength={64} /></Form.Item>
        <Form.Item name="ticket" label="申请工单号"><Input maxLength={80} /></Form.Item>
      </Row>
      <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
    </>}
  </FormModal>
}

// --------------------------------------------------------------------------- planning levels and routing domains

type LevelValues = { levels: Partial<Level>[] }

export function LevelsModal({ open, levels, onClose, onSaved }: { open: boolean; levels: Level[]; onClose: Done; onSaved: Done }) {
  const { busy, submit } = useSubmit<LevelValues>(values => ledger.saveLevels(values.levels.map(lv => ({
    id: lv.id, dimension: lv.dimension, title: lv.title, default_length: lv.default_length ?? null,
  }))), '规划层级已保存', onSaved)
  const initial = useMemo(() => ({ levels }), [levels])
  return <FormModal<LevelValues> open={open} title="规划层级" subtitle="地址资源从上往下逐级划分；每一级的网段对应一个地域、机房、网络区域、业务系统或子系统，也可以自定义名称。"
    okText="保存" busy={busy} onClose={onClose} onSubmit={submit} initial={initial} width={720}>
    <Form.List name="levels">{(fields, { add, remove, move }) => <>
      <div className="rl-levels-head"><span>层级名称</span><span>对应</span><span>默认大小</span><span /></div>
      {fields.map((field, index) => <div className="rl-levels-row" key={field.key}>
        <Form.Item name={[field.name, 'title']} rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={32} /></Form.Item>
        <Form.Item name={[field.name, 'dimension']}><Select options={(Object.keys(DIMENSIONS) as Dimension[]).map(value => ({ value, label: DIMENSIONS[value] }))} /></Form.Item>
        <Form.Item name={[field.name, 'default_length']}><InputNumber min={1} max={32} prefix="/" placeholder="自动" style={{ width: '100%' }} /></Form.Item>
        <Gap size={0}>
          <Button type="text" aria-label="上移" icon={<ArrowUpOutlined aria-hidden="true" />} disabled={!index} onClick={() => move(index, index - 1)} />
          <Button type="text" aria-label="下移" icon={<ArrowDownOutlined aria-hidden="true" />} disabled={index === fields.length - 1} onClick={() => move(index, index + 1)} />
          <Button type="text" danger aria-label="删除" icon={<DeleteOutlined aria-hidden="true" />} onClick={() => remove(field.name)} />
        </Gap>
      </div>)}
      <Button type="dashed" block icon={<PlusOutlined aria-hidden="true" />} onClick={() => add({ dimension: 'custom', title: '' })}>添加层级</Button>
      <p className="rl-note">地域、机房、网络区域要按从大到小排列，业务系统在子系统之上。默认大小留空时，按同级已有网段或上级大小自动建议。已有网段在用的层级不能删除。</p>
    </>}</Form.List>
  </FormModal>
}

export function SpacesModal({ open, spaces, onClose, onSaved }: { open: boolean; spaces: Space[]; onClose: Done; onSaved: Done }) {
  const [form] = Form.useForm<Space>()
  const [editing, setEditing] = useState<Space | null>(null)
  const save = async (values: Space) => {
    try { await ledger.save('spaces', { ...values, kind: editing?.kind || '其他' }, editing?.id); message.success('路由域已保存'); setEditing(null); form.resetFields(); onSaved() }
    catch (error) { message.error(errorText(error)) }
  }
  const remove = async (space: Space) => {
    try { await ledger.remove('spaces', space.id); message.success('路由域已删除'); onSaved() } catch (error) { message.error(errorText(error)) }
  }
  return <Modal open={open} title={<div>路由域<div className="rl-modal-sub">同一段私网地址在不同路由域（VRF）里可以各用一次。只有一个路由域时页面上不显示它。</div></div>}
    onCancel={onClose} footer={<Button onClick={onClose}>完成</Button>} destroyOnClose width={560}>
    <ul className="rl-ranges">{spaces.map(space => <li key={space.id}>
      <span>{space.name}{space.note && <span className="rl-muted">，{space.note}</span>}</span>
      <Gap size={0}><Button type="link" size="small" onClick={() => { setEditing(space); form.setFieldsValue(space) }}>改名</Button>
        <Button type="link" size="small" danger onClick={() => remove(space)}>删除</Button></Gap>
    </li>)}</ul>
    <Form form={form} layout="inline" style={{ marginTop: 14 }} onFinish={save}>
      <Form.Item name="name" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={64} placeholder="例如 灾备网" /></Form.Item>
      <Form.Item name="note"><Input maxLength={500} placeholder="说明" /></Form.Item>
      <Button htmlType="submit" type="primary">{editing ? '保存' : '新增路由域'}</Button>
      {editing && <Button type="link" onClick={() => { setEditing(null); form.resetFields() }}>取消</Button>}
    </Form>
  </Modal>
}

// --------------------------------------------------------------------------- zones

export function ZoneModal({ open, overview, zone, onClose, onSaved }: { open: boolean; overview?: Overview; zone?: Zone | null; onClose: Done; onSaved: Done }) {
  const { busy, submit } = useSubmit<Zone>(values => ledger.save('zones', values, zone?.id), zone ? '网络区域已保存' : '网络区域已新增', onSaved)
  const regions = new Map((overview?.regions || []).map(region => [region.id, region.name]))
  const sites = (overview?.sites || []).map((site: Site) => ({ value: site.id, label: `${site.name}（${regions.get(site.region_id) || ''}）` }))
  return <FormModal<Zone> open={open} title={zone ? `编辑网络区域 ${zone.name}` : '新增网络区域'} okText="保存" busy={busy} onClose={onClose} onSubmit={submit}
    initial={zone || { kind: '生产' } as Partial<Zone>} width={600}>
    <Form.Item name="site_id" label="机房" rules={[{ required: true, message: '请选择机房' }]} extra={sites.length ? undefined : '还没有机房，请先在“地域与机房”里新增。'}>
      <Select showSearch optionFilterProp="label" options={sites} />
    </Form.Item>
    <Row>
      <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={64} placeholder="region2" /></Form.Item>
      <Form.Item name="title" label="说明名"><Input maxLength={64} placeholder="生产业务区" /></Form.Item>
    </Row>
    <Row>
      <Form.Item name="kind" label="类型" rules={[{ required: true }]}><Select options={ZONE_KINDS.map(value => ({ value }))} /></Form.Item>
      <Form.Item name="security_level" label="安全等级"><Input maxLength={20} placeholder="例如 等保三级" /></Form.Item>
    </Row>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

// --------------------------------------------------------------------------- addresses

type AllocateValues = { ip?: string; kind: string; hostname?: string; dns_name?: string; note?: string }

export function AllocateModal({ open, prefix, ip, onClose, onDone }: { open: boolean; prefix: PrefixDetail; ip?: string; onClose: Done; onDone: (row: AddressRow) => void }) {
  const [choose, setChoose] = useState(Boolean(ip))
  useEffect(() => { if (open) setChoose(Boolean(ip)) }, [open, ip])
  const { busy, submit } = useSubmit<AllocateValues>(async values => {
    const row = await ledger.allocate(prefix.id, { ...values, ip: choose ? values.ip : undefined })
    onDone(row)
  }, '地址已分配', onClose)
  const suggested = ip || prefix.next_free
  return <FormModal<AllocateValues> open={open} title="分配 IP 地址" subtitle={`从 ${prefix.network}${prefix.title ? `（${prefix.title}）` : ''}分配；之后可以在主机台账里关联到主机。`}
    okText="分配" busy={busy} onClose={onClose} onSubmit={submit} initial={{ kind: '主机', ip: suggested || undefined }} width={600}>
    {!choose
      ? <Form.Item label="地址">
          {suggested
            ? <div className="rl-pick"><strong>{suggested}</strong><span className="rl-muted">第一个空闲地址，已避开网关和保留范围</span>
                <Button type="link" onClick={() => setChoose(true)}>改选其他地址</Button></div>
            : <span className="rl-error-text">这个网段已经没有空闲地址</span>}
        </Form.Item>
      : <Form.Item name="ip" label="地址" rules={[{ required: true, message: '请填写地址' }]} extra={`可用范围 ${prefix.usable?.first} – ${prefix.usable?.last}`}>
          <Input maxLength={15} />
        </Form.Item>}
    <Form.Item name="kind" label="用途"><Radio.Group options={ADDRESS_KINDS.map(value => ({ value, label: value }))} /></Form.Item>
    <Row>
      <Form.Item name="hostname" label="名称"><Input maxLength={128} placeholder="risk-web-vip" /></Form.Item>
      <Form.Item name="dns_name" label="DNS 名"><Input maxLength={255} placeholder="risk.xa.corp" /></Form.Item>
    </Row>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

export function AddressModal({ open, address, onClose, onSaved }: { open: boolean; address?: AddressRow | null; onClose: Done; onSaved: Done }) {
  const { busy, submit } = useSubmit<AllocateValues>(values => ledger.updateAddress(address!.id, values), '地址信息已保存', onSaved)
  const linked = Boolean(address?.host)
  return <FormModal<AllocateValues> open={open} title={`编辑 ${address?.ip || ''}`} okText="保存" busy={busy} onClose={onClose} onSubmit={submit}
    subtitle={linked ? `这是主机 ${address!.host!.hostname} 的地址，名称随主机台账变化。` : undefined}
    initial={address ? { kind: address.kind, hostname: address.hostname || undefined, dns_name: address.dns_name || undefined, note: address.note || undefined } : undefined}>
    <Form.Item name="kind" label="用途"><Radio.Group options={ADDRESS_KINDS.map(value => ({ value, label: value }))} /></Form.Item>
    <Row>
      <Form.Item name="hostname" label="名称"><Input maxLength={128} disabled={linked} /></Form.Item>
      <Form.Item name="dns_name" label="DNS 名"><Input maxLength={255} /></Form.Item>
    </Row>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

type ReservationValues = { first: string; last: string; purpose: string }

export function ReservationModal({ open, prefix, onClose, onSaved }: { open: boolean; prefix: PrefixDetail; onClose: Done; onSaved: Done }) {
  const { busy, submit } = useSubmit<ReservationValues>(values => ledger.reserve(prefix.id, values), '保留范围已新增', onSaved)
  return <FormModal<ReservationValues> open={open} title="保留地址范围" okText="保留" busy={busy} onClose={onClose} onSubmit={submit}
    subtitle={`保留范围内的地址不会被自动分配，例如扩容预留、VRRP 实地址。可用范围 ${prefix.usable?.first} – ${prefix.usable?.last}。`}>
    <Row>
      <Form.Item name="first" label="起始地址" rules={[{ required: true, message: '请填写起始地址' }]}><Input maxLength={15} /></Form.Item>
      <Form.Item name="last" label="结束地址" rules={[{ required: true, message: '请填写结束地址' }]}><Input maxLength={15} /></Form.Item>
    </Row>
    <Form.Item name="purpose" label="用途" rules={[{ required: true, message: '请填写用途' }]}><Input maxLength={200} placeholder="Web 扩容预留" /></Form.Item>
  </FormModal>
}
