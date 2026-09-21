import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import MarkdownAnswer from './MarkdownAnswer'
afterEach(cleanup)

it('renders commands, structured answers and safe source links', () => {
  const { container } = render(<MarkdownAnswer content={'**检查**\n\n```text\ndisplay interface brief\n```\n\n|设备|状态|\n|---|---|\n|sw1|未知|\n\n[手册](https://example.com/manual)'} />)
  expect(container.querySelector('strong')).toHaveTextContent('检查')
  expect(container.querySelector('pre code')).toHaveTextContent('display interface brief')
  expect(screen.getByRole('table')).toHaveTextContent('sw1')
  expect(screen.getByRole('link', { name: '手册' })).toHaveAttribute('rel', 'noopener noreferrer')
})

it('does not execute raw HTML, unsafe URLs or load model-supplied images', () => {
  const { container } = render(<MarkdownAnswer content={'<script>alert(1)</script>\n\n<img src="https://example.com/tracker">\n\n[危险](javascript:alert%281%29)\n\n![远程图片](https://example.com/tracker)\n\n[文件](file:///secret)'} />)
  expect(container.querySelector('script, img')).toBeNull()
  expect(screen.queryByRole('link')).not.toBeInTheDocument()
  expect(screen.getByText('[图片：远程图片]')).toBeInTheDocument()
})
