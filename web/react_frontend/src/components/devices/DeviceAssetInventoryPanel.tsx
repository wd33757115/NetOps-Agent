import React, { useState } from 'react'
import { Button, Col, Input, Row, Select, Space, Table, Tag, Typography, type TableProps } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import type { ManagedDevice } from '../../services/api'

type TableColumns<T> = TableProps<T>['columns']

export interface DeviceAssetFilters {
  keyword: string
  vendor: string
  model: string
  family: string
  platform: string
  deviceType: string
  site: string
  role: string
  group: string
  status: string
  connectionStatus: string
}

interface DeviceAssetInventoryPanelProps {
  columns: TableColumns<ManagedDevice>
  devices: ManagedDevice[]
  filters: DeviceAssetFilters
  loading: boolean
  selectedRowKeys: React.Key[]
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number, pageSize: number) => void
  onFiltersChange: (patch: Partial<DeviceAssetFilters>) => void
  onSelectedRowKeysChange: (keys: React.Key[]) => void
}

const DeviceAssetInventoryPanel: React.FC<DeviceAssetInventoryPanelProps> = ({
  columns,
  devices,
  filters,
  loading,
  selectedRowKeys,
  total,
  page,
  pageSize,
  onPageChange,
  onFiltersChange,
  onSelectedRowKeysChange,
}) => {
  const [expanded, setExpanded] = useState(false)
  const extraFilters = [filters.model, filters.family, filters.platform, filters.deviceType, filters.site, filters.role, filters.group].filter(Boolean).length
  return (
  <Space direction="vertical" size={12} style={{ width: '100%' }}>
    <Space wrap>
      <Typography.Title level={5} style={{ margin: 0 }}>设备资产</Typography.Title>
      <Tag color="blue">共 {total} 台</Tag>
      {selectedRowKeys.length > 0 && <Tag color="geekblue">已选 {selectedRowKeys.length} 台</Tag>}
    </Space>
    <Row gutter={[12, 12]}>
      <Col xs={24} md={8} xl={5}>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="设备名称"
          value={filters.keyword}
          onChange={(event) => onFiltersChange({ keyword: event.target.value })}
        />
      </Col>
      <Col xs={12} md={4} xl={3}>
        <Input allowClear placeholder="厂商" value={filters.vendor}
          onChange={(event) => onFiltersChange({ vendor: event.target.value })} />
      </Col>
      <Col xs={12} md={4} xl={3}>
        <Select
          value={filters.status}
          style={{ width: '100%' }}
          onChange={(status) => onFiltersChange({ status })}
          options={[
            { value: '', label: '全部状态' },
            { value: 'active', label: '启用' },
            { value: 'disabled', label: '停用' },
          ]}
        />
      </Col>
      <Col xs={12} md={4} xl={3}>
        <Select
          value={filters.connectionStatus}
          style={{ width: '100%' }}
          onChange={(connectionStatus) => onFiltersChange({ connectionStatus })}
          options={[
            { value: '', label: '连接：全部' },
            { value: 'connected', label: '已连接' },
            { value: 'failed', label: '连接失败' },
            { value: 'preflight_failed', label: '预检失败' },
          ]}
        />
      </Col>
      <Col flex="auto"><Space>
        <Button type="text" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? '收起筛选' : `更多筛选${extraFilters ? `（${extraFilters}）` : ''}`}</Button>
        <Button type="text" onClick={() => onFiltersChange(Object.fromEntries(Object.keys(filters).map(key => [key, ''])) as unknown as DeviceAssetFilters)}>清空筛选</Button>
      </Space></Col>
    </Row>
    {expanded && <Row gutter={[12, 12]}>
      <Col xs={12} md={4} xl={3}>
        <Input allowClear placeholder="型号" value={filters.model}
          onChange={(event) => onFiltersChange({ model: event.target.value })} />
      </Col>
      <Col xs={12} md={4} xl={3}>
        <Input allowClear placeholder="设备族" value={filters.family}
          onChange={(event) => onFiltersChange({ family: event.target.value })} />
      </Col>
      <Col xs={12} md={4} xl={3}>
        <Input allowClear placeholder="平台" value={filters.platform}
          onChange={(event) => onFiltersChange({ platform: event.target.value })} />
      </Col>
      <Col xs={12} md={4} xl={3}>
        <Input allowClear placeholder="设备类型" value={filters.deviceType}
          onChange={(event) => onFiltersChange({ deviceType: event.target.value })} />
      </Col>
      <Col xs={12} md={4} xl={3}>
        <Input allowClear placeholder="站点" value={filters.site}
          onChange={(event) => onFiltersChange({ site: event.target.value })} />
      </Col>
      <Col xs={12} md={4} xl={3}>
        <Input allowClear placeholder="角色" value={filters.role}
          onChange={(event) => onFiltersChange({ role: event.target.value })} />
      </Col>
      <Col xs={12} md={4} xl={3}>
        <Input allowClear placeholder="分组" value={filters.group}
          onChange={(event) => onFiltersChange({ group: event.target.value })} />
      </Col>
    </Row>}
    <Table<ManagedDevice>
      rowKey="device_id"
      loading={loading}
      dataSource={devices}
      columns={columns}
      size="small"
      pagination={{ current: page, pageSize, total, showSizeChanger: true, pageSizeOptions: [20, 50, 100], onChange: onPageChange }}
      rowSelection={{
        selectedRowKeys,
        preserveSelectedRowKeys: true,
        onChange: onSelectedRowKeysChange,
        getCheckboxProps: (record) => ({ disabled: record.status === 'deleted' }),
      }}
      scroll={{ x: 1910 }}
    />
  </Space>
)
}

export default DeviceAssetInventoryPanel
