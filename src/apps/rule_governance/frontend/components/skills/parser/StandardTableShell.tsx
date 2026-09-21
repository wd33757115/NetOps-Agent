import React, { useEffect, useRef } from 'react'
import { Alert, Button, Table } from 'antd'
import type { TableProps } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

interface StandardTableShellProps<RecordType extends object>
  extends Omit<TableProps<RecordType>, 'locale' | 'size'> {
  emptyText: string
  error?: unknown
  errorTitle?: string
  onRetry?: () => void
}

const errorDescription = (error: unknown) => {
  if (!error) return null
  if (typeof error === 'string') return error
  if (error instanceof Error) return error.message
  return '请求未完成，请检查网络、权限或服务状态。'
}

const StandardTableShell = <RecordType extends object>({
  emptyText,
  error,
  errorTitle = '数据加载失败',
  onRetry,
  pagination = { pageSize: 20, showSizeChanger: false },
  scroll,
  ...tableProps
}: StandardTableShellProps<RecordType>) => {
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    rootRef.current?.querySelectorAll<HTMLElement>('.ant-table-content').forEach((element) => {
      element.tabIndex = 0
      element.setAttribute('aria-label', '可横向滚动的数据表格')
    })
  })

  return (
    <div ref={rootRef} className="parser-table-shell">
    {error && (
      <Alert
        type="error"
        showIcon
        message={errorTitle}
        description={errorDescription(error)}
        action={onRetry ? (
          <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
            重试
          </Button>
        ) : undefined}
        style={{ marginBottom: 12 }}
      />
    )}
    <Table<RecordType>
      {...tableProps}
      size="small"
      pagination={pagination}
      scroll={scroll}
      locale={{ emptyText }}
    />
  </div>
  )
}

export default StandardTableShell
