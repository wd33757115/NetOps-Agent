import React from 'react'
import { Space, Table, Tag, Typography } from 'antd'
import type { DevicePatrolPreviewRow } from '@netops/services/api'

export default function PatrolPlanPreview({ rows }: { rows: DevicePatrolPreviewRow[] }) {
  return <section aria-label="巡检设备与执行命令">
    <Typography.Title level={5}>巡检设备与执行命令</Typography.Title>
    <Typography.Paragraph type="secondary">以下命令来自本次预检计划，按所列顺序采集。修改设备或分组后需重新预览。</Typography.Paragraph>
    <Table<DevicePatrolPreviewRow> rowKey="device_id" size="small" dataSource={rows}
      pagination={{ pageSize: 8, showTotal: total => `共 ${total} 台设备` }} scroll={{ x: 850 }} columns={[
        { title: '选中设备', width: 200, render: (_, row) => <Space direction="vertical" size={2}>
          <Typography.Text strong>{row.device_name || row.device_id}</Typography.Text>
          <Typography.Text type="secondary">{row.ip || '未设置管理地址'}</Typography.Text>
        </Space> },
        { title: '命令集 / 检查项', width: 210, render: (_, row) => <Space direction="vertical" size={4}>
          <Typography.Text>{row.plan.profile_name || '设备有效采集方案'}</Typography.Text>
          <Typography.Text type="secondary">{row.command_count} 条命令 · {row.plan.check_item_ids?.length || 0} 个检查项</Typography.Text>
          {!!row.plan.check_item_ids?.length && <details><summary>查看检查项</summary>{row.plan.check_item_ids.map(id => <div key={id}>{id}</div>)}</details>}
        </Space> },
        { title: '即将执行的命令', width: 320, render: (_, row) => row.commands?.length
          ? <pre aria-label={`${row.device_name || row.device_id} 的执行命令`} style={{ margin: 0, padding: 12, background: '#f5f7fa', borderRadius: 6, maxHeight: 220, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{row.commands.map((command, index) => `${index + 1}. ${command}`).join('\n')}</pre>
          : <Typography.Text type="warning">没有可执行命令</Typography.Text> },
        { title: '预检状态', width: 120, render: (_, row) => <Space wrap size={4}>
          <Tag color={row.connection_issues?.length ? 'red' : 'green'}>{row.connection_issues?.length ? '连接阻断' : '就绪'}</Tag>
          {!!row.coverage_violations?.length && <Tag color="orange">解析缺口 {row.coverage_violations.length}</Tag>}
          {row.runtime_asset_health?.status === 'degraded' && <Tag color="orange">规则资产降级</Tag>}
        </Space> },
      ]} />
  </section>
}
