import React, { useImperativeHandle, useMemo, useRef, useState } from 'react'
import { Empty, Input, Radio, Spin, Typography, type InputRef } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import type { ParserCheckItem } from '@netops/services/api'

interface CheckItemPickerProps {
  items: ParserCheckItem[]
  value?: string
  loading?: boolean
  modeLabels: Record<string, string>
  onChange: (checkItemId: string) => void
}

export interface CheckItemPickerHandle {
  focus: () => void
}

/** Choose one check item from the whole catalog: search on top, every match in view below. */
const CheckItemPicker = React.forwardRef<CheckItemPickerHandle, CheckItemPickerProps>((
  { items, value, loading, modeLabels, onChange },
  ref,
) => {
  const [search, setSearch] = useState('')
  const searchRef = useRef<InputRef>(null)
  // A dialog is still hidden when its content mounts, so autoFocus is lost there;
  // the owner focuses the search once the dialog has opened.
  useImperativeHandle(ref, () => ({ focus: () => searchRef.current?.focus() }), [])
  // Only what a row shows is searched, so every match explains itself.
  const rows = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    if (!keyword) return items
    return items.filter((item) => [
      item.display_name,
      item.name,
      item.check_item_id,
      item.display_description,
      item.description,
      modeLabels[item.result_mode],
    ].filter(Boolean).join(' ').toLowerCase().includes(keyword))
  }, [items, modeLabels, search])
  const selected = items.find((item) => item.check_item_id === value)

  return (
    <div className="check-item-picker">
      <Input
        ref={searchRef}
        allowClear
        size="large"
        prefix={<SearchOutlined />}
        aria-label="搜索检查项"
        placeholder="搜索名称、标识或说明，例如 接口、bgp、vpc"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      <Spin spinning={Boolean(loading)}>
        <div className="check-item-picker__list" role="radiogroup" aria-label="关联目标检查项">
          <Radio.Group value={value} onChange={(event) => onChange(event.target.value)}>
            {rows.map((item) => (
              <Radio key={item.check_item_id} value={item.check_item_id} className="check-item-picker__option">
                <span className="check-item-picker__name">{item.display_name || item.name}</span>
                <span className="check-item-picker__aside">{modeLabels[item.result_mode] || item.result_mode}</span>
                <span className="check-item-picker__description">
                  {item.display_description || item.description}
                </span>
                <span className="check-item-picker__aside">{item.check_item_id}</span>
              </Radio>
            ))}
          </Radio.Group>
          {!rows.length && !loading && (
            <Empty
              className="check-item-picker__empty"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={items.length ? '没有匹配的检查项，换一个关键词试试' : '目录里还没有检查项，请先新增检查项'}
            />
          )}
        </div>
      </Spin>
      <Typography.Text type={selected ? undefined : 'secondary'}>
        {selected
          ? <>已选：<Typography.Text strong>{selected.display_name || selected.name}</Typography.Text></>
          : `共 ${items.length} 个检查项，${search.trim() ? `匹配 ${rows.length} 个` : '点选其中一个'}`}
      </Typography.Text>
    </div>
  )
})

CheckItemPicker.displayName = 'CheckItemPicker'

export default CheckItemPicker
