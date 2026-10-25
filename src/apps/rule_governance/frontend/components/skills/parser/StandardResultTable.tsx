import React from 'react'
import { Empty, Table, Typography } from 'antd'

export default function StandardResultTable({ records }: { records: Array<Record<string, unknown>> }) {
  const fields = Array.from(new Set(records.flatMap(record => Object.keys(record))))
  if (!records.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有记录，请核对空输出含义" />
  return <Table size="small" rowKey="__row" dataSource={records.map((record, index) => ({ ...record, __row: index }))}
    scroll={{ x: Math.max(360, fields.length * 130) }} pagination={records.length > 10 ? { pageSize: 10, showSizeChanger: false } : false}
    columns={fields.map(field => ({ title: field, dataIndex: field, width: 150,
      render: (value: unknown) => value === null || value === undefined
        ? <Typography.Text type="secondary">缺失</Typography.Text>
        : <Typography.Text>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</Typography.Text>,
    }))} />
}
