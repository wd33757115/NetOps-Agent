import React, { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  AutoComplete,
  Button,
  Drawer,
  Input,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  SaveOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'
import {
  parserApi,
  type ParserValueMappingConfig,
  type ParserValueMappingRule,
} from '@netops/services/api'

const EMPTY_RULE: ParserValueMappingRule = {
  check_item: '',
  vendor: '',
  model: '',
  command: '',
  canonical: '',
  aliases: [],
}

const normalizeValueName = (value: string) => (
  value.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
)

const normalizeAliases = (values: string[]) => Array.from(new Set(
  values.map(normalizeValueName).filter(Boolean),
))

const normalizeScope = (value: string) => value.trim().replace(/\s+/g, ' ')
const comparable = (value: string) => normalizeScope(value).toLowerCase()

const sameScope = (left: ParserValueMappingRule, right: ParserValueMappingRule) => (
  comparable(left.check_item) === comparable(right.check_item)
  && comparable(left.vendor) === comparable(right.vendor)
  && comparable(left.model) === comparable(right.model)
  && comparable(left.command) === comparable(right.command)
)

const ParserValueMappingManager: React.FC = () => {
  const [open, setOpen] = useState(false)
  const [mappings, setMappings] = useState<ParserValueMappingRule[]>([])
  const [draft, setDraft] = useState<ParserValueMappingRule>({ ...EMPTY_RULE })
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [dirty, setDirty] = useState(false)

  const query = useQuery<ParserValueMappingConfig, Error>(
    ['parser-value-mappings'],
    parserApi.getValueMappings,
    { enabled: open, refetchOnWindowFocus: false },
  )

  useEffect(() => {
    if (!query.data) return
    setMappings(query.data.scoped_value_mappings || [])
    setDraft({ ...EMPTY_RULE })
    setEditingIndex(null)
    setSaved(false)
    setDirty(false)
  }, [query.data])

  const saveMutation = useMutation(
    () => parserApi.updateValueMappings({ scoped_value_mappings: mappings }),
    {
      onSuccess: (data) => {
        setMappings(data.scoped_value_mappings || [])
        setFormError(null)
        setSaved(true)
        setDirty(false)
      },
      onError: (error: Error) => {
        setSaved(false)
        setFormError(error.message || 'Value 映射保存失败')
      },
    },
  )

  const optionsFor = (field: keyof Pick<ParserValueMappingRule, 'check_item' | 'vendor' | 'model' | 'command'>) => {
    const values = mappings
      .filter((item) => field === 'check_item' || comparable(item.check_item) === comparable(draft.check_item))
      .filter((item) => ['check_item', 'vendor'].includes(field) || comparable(item.vendor) === comparable(draft.vendor))
      .filter((item) => field !== 'command' || comparable(item.model) === comparable(draft.model))
      .map((item) => item[field])
    return Array.from(new Set(values)).sort().map((value) => ({ value }))
  }

  const rows = useMemo(() => mappings
    .map((item, index) => ({ ...item, index }))
    .sort((left, right) => [left.check_item, left.vendor, left.model, left.command, left.canonical]
      .join('|').localeCompare([right.check_item, right.vendor, right.model, right.command, right.canonical].join('|'))), [mappings])

  const updateScope = (
    field: 'check_item' | 'vendor' | 'model' | 'command',
    value: string,
  ) => {
    setDraft((current) => {
      if (field === 'check_item') return { ...current, check_item: value, vendor: '', model: '', command: '' }
      if (field === 'vendor') return { ...current, vendor: value, model: '', command: '' }
      if (field === 'model') return { ...current, model: value, command: '' }
      return { ...current, command: value }
    })
    setFormError(null)
  }

  const resetDraft = () => {
    setDraft({ ...EMPTY_RULE })
    setEditingIndex(null)
    setFormError(null)
  }

  const applyDraft = () => {
    const normalized: ParserValueMappingRule = {
      check_item: normalizeScope(draft.check_item),
      vendor: normalizeScope(draft.vendor),
      model: normalizeScope(draft.model),
      command: normalizeScope(draft.command),
      canonical: normalizeValueName(draft.canonical),
      aliases: normalizeAliases(draft.aliases).filter(
        (alias) => alias !== normalizeValueName(draft.canonical),
      ),
    }
    if (!normalized.check_item || !normalized.vendor || !normalized.model || !normalized.command) {
      setFormError('请按顺序填写检查项、设备厂家、设备型号和命令')
      return
    }
    if (!normalized.canonical || normalized.aliases.length === 0) {
      setFormError('请填写标准 Value，并至少添加一个原始 Value')
      return
    }
    const conflict = mappings.find((item, index) => (
      index !== editingIndex
      && sameScope(item, normalized)
      && item.canonical !== normalized.canonical
      && item.aliases.some((alias) => normalized.aliases.includes(alias))
    ))
    if (conflict) {
      setFormError(`同一作用域内的原始 Value 已映射到 ${conflict.canonical}`)
      return
    }
    setMappings((current) => {
      if (editingIndex !== null) {
        return current.map((item, index) => (index === editingIndex ? normalized : item))
      }
      const existingIndex = current.findIndex((item) => (
        sameScope(item, normalized) && item.canonical === normalized.canonical
      ))
      if (existingIndex < 0) return [...current, normalized]
      return current.map((item, index) => index === existingIndex
        ? { ...item, aliases: normalizeAliases([...item.aliases, ...normalized.aliases]) }
        : item)
    })
    resetDraft()
    setSaved(false)
    setDirty(true)
  }

  return <>
    <Button icon={<SettingOutlined />} onClick={() => setOpen(true)}>
      Value 映射
    </Button>
    <Drawer
      rootClassName="parser-workspace-overlay parser-value-mapping-drawer"
      title="TextFSM Value 映射"
      open={open}
      width={920}
      destroyOnClose
      onClose={() => setOpen(false)}
      extra={<Button
        type="primary"
        icon={<SaveOutlined />}
        loading={saveMutation.isLoading}
        disabled={!dirty}
        onClick={() => saveMutation.mutate()}
      >
        保存配置
      </Button>}
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <div>
          <Typography.Title level={5} style={{ margin: 0 }}>按适用范围创建映射</Typography.Title>
          <Typography.Text type="secondary">
            检查项 → 设备厂家 → 设备型号 → 命令，四级全部匹配后才转换 Value。
          </Typography.Text>
        </div>

        {query.isLoading && <div style={{ textAlign: 'center', padding: 32 }}><Spin /></div>}
        {query.error && <Alert type="error" showIcon message="Value 映射加载失败" description={query.error.message} />}
        {formError && <Alert type="error" showIcon message={formError} closable onClose={() => setFormError(null)} />}
        {saved && <Alert type="success" showIcon message="配置已保存并生效" />}

        {!query.isLoading && !query.error && <>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <label>
              <Typography.Text strong>1. 检查项</Typography.Text>
              <AutoComplete
                value={draft.check_item}
                options={optionsFor('check_item')}
                onChange={(value) => updateScope('check_item', value)}
                placeholder="例如 environment.power"
                style={{ width: '100%', marginTop: 6 }}
                filterOption={(input, option) => comparable(String(option?.value || '')).includes(comparable(input))}
              />
            </label>
            <label>
              <Typography.Text strong>2. 设备厂家</Typography.Text>
              <AutoComplete
                value={draft.vendor}
                options={optionsFor('vendor')}
                onChange={(value) => updateScope('vendor', value)}
                placeholder="例如 H3C"
                style={{ width: '100%', marginTop: 6 }}
                disabled={!draft.check_item.trim()}
                filterOption={(input, option) => comparable(String(option?.value || '')).includes(comparable(input))}
              />
            </label>
            <label>
              <Typography.Text strong>3. 设备型号</Typography.Text>
              <AutoComplete
                value={draft.model}
                options={optionsFor('model')}
                onChange={(value) => updateScope('model', value)}
                placeholder="例如 S6520XP"
                style={{ width: '100%', marginTop: 6 }}
                disabled={!draft.vendor.trim()}
                filterOption={(input, option) => comparable(String(option?.value || '')).includes(comparable(input))}
              />
            </label>
            <label>
              <Typography.Text strong>4. 命令</Typography.Text>
              <AutoComplete
                value={draft.command}
                options={optionsFor('command')}
                onChange={(value) => updateScope('command', value)}
                placeholder="例如 display power"
                style={{ width: '100%', marginTop: 6 }}
                disabled={!draft.model.trim()}
                filterOption={(input, option) => comparable(String(option?.value || '')).includes(comparable(input))}
              />
            </label>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr auto', gap: 12, alignItems: 'end' }}>
            <label>
              <Typography.Text strong>标准 Value</Typography.Text>
              <Input
                value={draft.canonical}
                onChange={(event) => setDraft((current) => ({ ...current, canonical: event.target.value }))}
                placeholder="power_supply_status"
                style={{ marginTop: 6 }}
              />
            </label>
            <label>
              <Typography.Text strong>原始 Value</Typography.Text>
              <Select
                mode="tags"
                value={draft.aliases}
                onChange={(aliases) => setDraft((current) => ({ ...current, aliases }))}
                tokenSeparators={[',', ' ']}
                placeholder="输入模板中的 Value 后回车，例如 STATE"
                style={{ width: '100%', marginTop: 6 }}
              />
            </label>
            <Space>
              {editingIndex !== null && <Button onClick={resetDraft}>取消</Button>}
              <Button type="primary" icon={<PlusOutlined />} onClick={applyDraft}>
                {editingIndex === null ? '添加映射' : '更新映射'}
              </Button>
            </Space>
          </div>

          <Table
            size="small"
            rowKey={(row) => `${row.check_item}|${row.vendor}|${row.model}|${row.command}|${row.canonical}|${row.index}`}
            pagination={false}
            locale={{ emptyText: '暂无 Value 映射' }}
            dataSource={rows}
            columns={[
              {
                title: '适用范围',
                width: 360,
                render: (_value, row) => <Space direction="vertical" size={1}>
                  <Typography.Text strong>{row.check_item}</Typography.Text>
                  <Typography.Text type="secondary">{row.vendor} / {row.model}</Typography.Text>
                  <Typography.Text code>{row.command}</Typography.Text>
                </Space>,
              },
              {
                title: 'Value 映射',
                render: (_value, row) => <Space direction="vertical" size={4}>
                  <Typography.Text code>{row.canonical}</Typography.Text>
                  <Space size={[4, 4]} wrap>
                    {row.aliases.map((alias) => <Tag key={alias}>{alias}</Tag>)}
                  </Space>
                </Space>,
              },
              {
                title: '',
                width: 92,
                render: (_value, row) => <Space size={0}>
                  <Button
                    type="text"
                    icon={<EditOutlined />}
                    aria-label={`编辑 ${row.canonical}`}
                    onClick={() => {
                      setDraft({
                        check_item: row.check_item,
                        vendor: row.vendor,
                        model: row.model,
                        command: row.command,
                        canonical: row.canonical,
                        aliases: [...row.aliases],
                      })
                      setEditingIndex(row.index)
                      setFormError(null)
                    }}
                  />
                  <Button
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    aria-label={`删除 ${row.canonical}`}
                    onClick={() => {
                      setMappings((current) => current.filter((_item, index) => index !== row.index))
                      if (editingIndex === row.index) resetDraft()
                      setSaved(false)
                      setDirty(true)
                    }}
                  />
                </Space>,
              },
            ]}
          />

          {query.data?.config_path && <Typography.Text type="secondary">
            配置文件：<Typography.Text code>{query.data.config_path}</Typography.Text>
          </Typography.Text>}
        </>}
      </Space>
    </Drawer>
  </>
}

export default ParserValueMappingManager
