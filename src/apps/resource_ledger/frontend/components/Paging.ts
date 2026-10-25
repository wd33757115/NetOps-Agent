import { useState } from 'react'
import type { TablePaginationConfig } from 'antd'

// Every list in the ledger pages the same way: the bar always shows, with the row count and a page size.
const SIZES = [20, 50, 100]
const showTotal = (total: number) => `共 ${total} 条`

/** Paging in the browser, for lists the page already holds in full. */
export const pageLocally = (pageSize = 20): TablePaginationConfig =>
  ({ defaultPageSize: pageSize, showSizeChanger: true, pageSizeOptions: SIZES, showTotal })

/** Paging on the server: the page asks for one slice at a time; `reset` goes back to the first page when filters change. */
export function useServerPage(initial = 50) {
  const [state, setState] = useState({ page: 1, size: initial })
  return {
    offset: (state.page - 1) * state.size,
    limit: state.size,
    reset: () => setState(current => ({ ...current, page: 1 })),
    pagination: (total: number): TablePaginationConfig => ({
      current: state.page, pageSize: state.size, total, showSizeChanger: true, pageSizeOptions: SIZES, showTotal,
      onChange: (page, size) => setState({ page: size === state.size ? page : 1, size }),
    }),
  }
}
