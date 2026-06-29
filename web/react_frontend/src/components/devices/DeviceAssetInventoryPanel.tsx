import React from 'react'
import { Col, Input, Row, Select, Table, type TableProps } from 'antd'
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
  onFiltersChange,
  onSelectedRowKeysChange,
}) => (
  <>
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
      <Col xs={12} md={4} xl={3}>
        <Select
          value={filters.status}
          style={{ width: '100%' }}
          onChange={(status) => onFiltersChange({ status })}
          options={[
            { value: '', label: '全部状态' },
            { value: 'active', label: '启用' },
            { value: 'inactive', label: '停用' },
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
    </Row>
    <Table<ManagedDevice>
      rowKey="device_id"
      loading={loading}
      dataSource={devices}
      columns={columns}
      size="small"
      pagination={{ pageSize: 20, total }}
      rowSelection={{
        selectedRowKeys,
        onChange: onSelectedRowKeysChange,
      }}
      scroll={{ x: 1910 }}
    />
  </>
)

export default DeviceAssetInventoryPanel
