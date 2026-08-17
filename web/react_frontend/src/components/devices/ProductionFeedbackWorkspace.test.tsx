import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { patrolApi, type ProductionFeedback } from '../../services/api'
import ProductionFeedbackWorkspace from './ProductionFeedbackWorkspace'

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'release-admin', email: '', role: 'admin' },
  }),
}))

const feedback: ProductionFeedback = {
  id: 'feedback-1',
  feedback_type: 'false_positive',
  status: 'submitted',
  execution_id: 'execution-1',
  assessment_id: 'assessment-1',
  event_occurrence_id: 'event-occurrence-1',
  patrol_batch_id: 'batch-1',
  device_id: 'device-1',
  check_item_version_id: 'check-interface-v1',
  runtime_bundle_version_id: 'bundle-interface-v2',
  runtime_bundle_digest: 'b'.repeat(64),
  evidence_digest: 'e'.repeat(64),
  source_context_json: { schema_version: 'production-feedback-source/v1' },
  statement: '接口处于维护窗口，本次 InterfaceDown 不应升级为故障。',
  reported_by: 'operator-a',
  created_at: '2026-08-08T10:00:00Z',
}

const renderWorkspace = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ProductionFeedbackWorkspace />
    </QueryClientProvider>,
  )
}

describe('ProductionFeedbackWorkspace', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    const getComputedStyle = window.getComputedStyle.bind(window)
    vi.spyOn(window, 'getComputedStyle').mockImplementation(
      (element) => getComputedStyle(element),
    )
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })
    vi.spyOn(patrolApi, 'listProductionFeedback').mockResolvedValue({
      items: [feedback],
      total: 1,
      page: 1,
      page_size: 25,
    })
  })

  it('shows governed feedback and its immutable evidence context', async () => {
    const user = userEvent.setup()
    renderWorkspace()

    expect(await screen.findByText('误报')).toBeInTheDocument()
    expect(screen.getByText('待复核')).toBeInTheDocument()
    expect(screen.getByText(feedback.statement)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '查看反馈证据' }))

    expect(await screen.findByText('反馈与冻结证据')).toBeInTheDocument()
    expect(screen.getByText('这条反馈不会改写原事件、风险、结论或已签发报告。')).toBeInTheDocument()
    expect(screen.getByText('bundle-interface-v2')).toBeInTheDocument()
  })

  it('offers independent triage instead of direct rule publication', async () => {
    const user = userEvent.setup()
    renderWorkspace()

    await screen.findByText('误报')
    await user.click(screen.getByRole('button', { name: /复\s*核/ }))

    await waitFor(() => {
      expect(screen.getByText('独立复核生产反馈')).toBeInTheDocument()
    })
    expect(screen.getByText('复核结论')).toBeInTheDocument()
    expect(screen.getByText('复核依据')).toBeInTheDocument()
    expect(screen.queryByText('直接发布')).not.toBeInTheDocument()
  })
})
