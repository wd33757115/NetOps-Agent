import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { message } from 'antd'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parserApi, type ParserJobItem } from '../../../services/api'
import ParserGenerationStep from './ParserGenerationStep'

const failedReadyJob: ParserJobItem = {
  id: 'parser-job-1',
  job_type: 'CREATE_TEMPLATE',
  vendor: 'Cisco',
  model: 'N9K',
  profile: 'N9K',
  canonical_command: 'show vpc',
  status: 'failed',
  source_mode: 'parser_validation',
  pipeline_version: 'simple_v2',
  parser_spec_status: 'confirmed',
  parser_spec_json: {
    entity: 'vpc_peer',
    primary_keys: ['peer_link'],
    fields: {
      peer_link: { required: true, type: 'text' },
    },
  },
  sample_count: 2,
  attempts: 1,
  model_calls_used: 1,
  last_error: 'first generation failed',
  generation_readiness: {
    ready: true,
    blockers: [],
    validation_mode: 'generation_sample_only',
    role_counts: { generation: 2 },
  },
  samples: [],
  created_at: '2026-07-26T10:00:00Z',
  updated_at: '2026-07-26T10:01:00Z',
}

describe('ParserGenerationStep', () => {
  beforeEach(() => {
    vi.spyOn(parserApi, 'templates').mockResolvedValue({
      items: [],
      total: 0,
      limit: 100,
      offset: 0,
    })
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
  })

  afterEach(() => {
    cleanup()
    message.destroy()
    vi.restoreAllMocks()
  })

  it('starts semantic analysis automatically for a new command asset', async () => {
    const collectingJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-new',
      status: 'collecting',
      parser_spec_status: 'missing',
      parser_spec_json: undefined,
      generation_readiness: {
        ready: false,
        blockers: ['parser_spec_missing', 'sample_roles_unassigned'],
        role_counts: { unassigned: 2 },
      },
      last_error: undefined,
      agent_state_json: {},
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(collectingJob)
    const suggest = vi.spyOn(parserApi, 'suggestJobSpec').mockResolvedValue({
      success: true,
      job_id: collectingJob.id,
      status: 'queued',
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={collectingJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await waitFor(() => expect(suggest).toHaveBeenCalledWith(collectingJob.id))
    expect(await screen.findByText('Parser 自动生成流程')).toBeVisible()
    expect(screen.getByText('高级修订')).toBeVisible()
  })

  it('dispatches automatically after the LLM semantic plan is adopted', async () => {
    const readyJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-ready',
      status: 'collecting',
      last_error: undefined,
      agent_state_json: {
        parser_spec_suggestion: {
          status: 'ready',
          result: { parser_spec: failedReadyJob.parser_spec_json, sample_suggestions: [] },
        },
      },
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(readyJob)
    const dispatch = vi.spyOn(parserApi, 'dispatchJob').mockResolvedValue({
      success: true,
      dispatched: [readyJob.id],
      failed: [],
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={readyJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await waitFor(() => expect(dispatch).toHaveBeenCalledWith(readyJob.id))
  })

  it('shows semantic analysis progress instead of claiming that samples are missing', async () => {
    const analyzingJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-analyzing',
      status: 'collecting',
      parser_spec_status: 'missing',
      parser_spec_json: undefined,
      last_error: undefined,
      generation_readiness: {
        ready: false,
        blockers: [
          'parser_spec_missing',
          'parser_spec_not_confirmed',
          'sample_roles_unassigned',
          'generation_sample_missing',
        ],
        role_counts: { unassigned: 1 },
      },
      agent_state_json: {
        parser_spec_suggestion: {
          status: 'running',
        },
      },
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(analyzingJob)
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={analyzingJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('正在分析样本并生成语义方案')).toBeVisible()
    expect(screen.queryByText('需要补充包含实际记录的样本')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '导入补充样本' })).not.toBeInTheDocument()
  })

  it('retries and dispatches a failed ready job from one visible action', async () => {
    vi.spyOn(parserApi, 'job').mockResolvedValue(failedReadyJob)
    const retry = vi.spyOn(parserApi, 'retryJob').mockResolvedValue({
      success: true,
      job_id: failedReadyJob.id,
      status: 'pending',
    })
    const dispatch = vi.spyOn(parserApi, 'dispatchJob').mockResolvedValue({
      success: true,
      dispatched: [failedReadyJob.id],
      failed: [],
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={failedReadyJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    const buttons = await screen.findAllByRole('button', { name: /重新生成/ })
    const button = buttons.find((item) => item.textContent?.trim() === '重新生成')
    expect(button).toBeDefined()
    if (!button) throw new Error('TextFSM retry action is missing')
    expect(button).toBeEnabled()
    await userEvent.click(button)

    await waitFor(() => expect(retry).toHaveBeenCalledWith(failedReadyJob.id))
    expect(dispatch).toHaveBeenCalledWith(failedReadyJob.id)
    expect(retry.mock.invocationCallOrder[0]).toBeLessThan(
      dispatch.mock.invocationCallOrder[0],
    )
  })

  it('allows an engineer to revise ParserSpec after generation has completed', async () => {
    const succeededJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-succeeded',
      status: 'succeeded',
      last_error: undefined,
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(succeededJob)
    const suggestSpec = vi.spyOn(parserApi, 'suggestJobSpec').mockResolvedValue({
      success: true,
      job_id: succeededJob.id,
      status: 'queued',
    })
    const updateSpec = vi.spyOn(parserApi, 'updateJobSpec').mockResolvedValue({
      success: true,
      job: {
        ...succeededJob,
        status: 'failed',
        failure_code: 'parser_spec_revised',
      },
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={succeededJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await userEvent.click(await screen.findByText('高级修订'))
    const reanalyze = screen.getByRole('button', { name: /重新分析/ })
    expect(reanalyze).toBeEnabled()
    await userEvent.click(reanalyze)
    await waitFor(() => expect(suggestSpec).toHaveBeenCalledWith(succeededJob.id))
    const editor = screen.getByLabelText('ParserSpec JSON')
    expect(editor).toBeEnabled()
    fireEvent.change(editor, {
      target: {
        value: JSON.stringify({
          entity: 'vpc_domain',
          primary_keys: ['domain_id'],
          fields: {
            domain_id: { required: true, type: 'text' },
          },
        }),
      },
    })
    await userEvent.click(screen.getByRole('button', { name: /保存修订/ }))

    await waitFor(() => expect(updateSpec).toHaveBeenCalledWith(
      succeededJob.id,
      expect.objectContaining({
        confirmed: true,
      }),
    ))
  })

  it('presents a revised ParserSpec as awaiting regeneration instead of a generation failure', async () => {
    const revisedJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-spec-revised',
      failure_code: 'parser_spec_revised',
      last_error: 'ParserSpec was revised; regenerate TextFSM with the new specification',
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(revisedJob)
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={revisedJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('ParserSpec 已更新')).toBeVisible()
    expect(screen.getByText('待重新生成')).toBeVisible()
    expect(screen.queryByText('本次生成未通过')).not.toBeInTheDocument()
  })

  it('blocks wasteful retries and summarizes a header-only sample failure', async () => {
    const insufficientSampleJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-header-only',
      last_error: 'Skill 脚本失败: model_requested_review:insufficient_sample: zero interface data rows',
      agent_state_json: {
        stop_reason: 'model_requested_review:insufficient_sample: zero interface data rows',
      },
      generation_readiness: {
        ready: false,
        blockers: ['generation_samples_without_data', 'header_only_samples_unverified'],
        role_counts: { generation: 4 },
      },
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(insufficientSampleJob)
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={insufficientSampleJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('样本不足，无法验证 Parser')).toBeVisible()
    expect(screen.getByText(/当前样本只有表头或空结果/)).toBeVisible()
    expect(screen.getByRole('button', { name: '导入补充样本' })).toBeEnabled()
    expect(screen.getByText('查看技术诊断')).toBeVisible()
    expect(screen.queryByText(/Skill 脚本失败/)).not.toBeInTheDocument()
  })

  it('regenerates after the previous candidate was rejected', async () => {
    const rejectedCandidateJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-rejected-candidate',
      status: 'succeeded',
      last_error: undefined,
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(rejectedCandidateJob)
    vi.spyOn(parserApi, 'templates').mockResolvedValue({
      items: [],
      total: 0,
      limit: 100,
      offset: 0,
    })
    const retry = vi.spyOn(parserApi, 'retryJob').mockResolvedValue({
      success: true,
      job_id: rejectedCandidateJob.id,
      status: 'pending',
    })
    const dispatch = vi.spyOn(parserApi, 'dispatchJob').mockResolvedValue({
      success: true,
      dispatched: [rejectedCandidateJob.id],
      failed: [],
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={rejectedCandidateJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('没有可确认的候选结果')).toBeVisible()
    expect(screen.queryByText('自动生成条件已满足')).not.toBeInTheDocument()
    const button = screen.getByRole('button', { name: /重新生成/ })
    expect(button).toBeEnabled()
    await userEvent.click(button)

    await waitFor(() => expect(retry).toHaveBeenCalledWith(rejectedCandidateJob.id))
    expect(dispatch).toHaveBeenCalledWith(rejectedCandidateJob.id)
  })
})
