import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { message } from 'antd'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parserApi, type ParserJobItem, type ParserTemplateItem } from '../../../services/api'
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
    vi.spyOn(parserApi, 'jobAttempts').mockResolvedValue([])
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

  it('waits for the operator to inspect samples before semantic analysis', async () => {
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

    expect(await screen.findByText('Parser 生产工作流')).toBeVisible()
    expect(suggest).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /样本已确认，进入语义设计/ }))
    expect(await screen.findByText('设计并确认语义方案')).toBeVisible()
    await userEvent.click(screen.getByText('让 LLM 提出语义方案'))
    await waitFor(() => expect(suggest).toHaveBeenCalledWith(collectingJob.id, ''))
    expect(screen.getByText('高级修订')).toBeVisible()
  })

  it('requires explicit semantic confirmation before generation starts', async () => {
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

    expect(await screen.findByText('设计并确认语义方案')).toBeVisible()
    expect(dispatch).not.toHaveBeenCalled()
    await userEvent.click(screen.getByText('确认语义方案并生成 Parser'))
    await waitFor(() => expect(dispatch).toHaveBeenCalledWith(readyJob.id))
  })

  it('shows governed TextFSM value names as guidance without turning them into a gate', async () => {
    const succeededJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-naming-guidance',
      status: 'succeeded',
      last_error: undefined,
      agent_state_json: {
        parser_spec_suggestion: {
          status: 'ready',
          result: {
            parser_spec: failedReadyJob.parser_spec_json,
            sample_suggestions: [],
            field_naming_context: {
              semantic_profile_id: 'interface.optical_power',
              preferred_value_names: ['interface', 'rx_power_dbm', 'tx_power_dbm'],
              policy: {
                preference: 'prefer governed names when semantics match',
                fallback: 'retain source fields and map after parsing',
                validation: 'naming differences are never parser gates',
              },
            },
          },
        },
      },
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(succeededJob)
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

    fireEvent.click(await screen.findByText('语义设计'))
    fireEvent.click(await screen.findByText('技术依据与字段命名建议'))
    expect(await screen.findByText('推荐 TextFSM Value')).toBeVisible()
    expect(screen.getByText('interface')).toBeVisible()
    expect(screen.getByText('rx_power_dbm')).toBeVisible()
    expect(screen.getByText('tx_power_dbm')).toBeVisible()
    expect(screen.getByText(/存在真实差异时保留源字段/)).toBeVisible()
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
          task_id: 'semantic-task-12345678',
          heartbeat_at: '2026-08-02T10:00:15Z',
          deadline_at: '2026-08-02T10:08:00Z',
          elapsed_ms: 15000,
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

    expect(await screen.findAllByText('正在分析样本并生成语义方案')).toHaveLength(1)
    expect(screen.getByText('模型分析中')).toBeVisible()
    expect(screen.getByText('任务 semantic')).toBeVisible()
    expect(screen.getByText(/分析截止/)).toBeVisible()
    expect(screen.queryByText('需要补充包含实际记录的样本')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '导入补充样本' })).not.toBeInTheDocument()
  })

  it('shows durable generation stage, heartbeat and model-call budget', async () => {
    const runningJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-running',
      status: 'running',
      last_error: undefined,
      agent_state_json: {
        generation_progress: {
          stage: 'model_revising',
          attempt_number: 2,
          heartbeat_at: '2026-08-02T10:00:00Z',
          deadline_at: '2026-08-02T10:20:00Z',
          elapsed_ms: 125000,
          details: {
            model_calls_used: 2,
            model_call_limit: 3,
            diagnostic_code: 'primary_keys_valid',
          },
        },
      },
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(runningJob)
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={runningJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('模型定向修复')).toBeVisible()
    expect(screen.getByText('第 2 轮')).toBeVisible()
    expect(screen.getByText(/模型调用 2\/3/)).toBeVisible()
    expect(screen.getByText(/最近诊断 primary_keys_valid/)).toBeVisible()
  })

  it('retries and dispatches a failed ready job from one visible action', async () => {
    vi.spyOn(parserApi, 'job').mockResolvedValue(failedReadyJob)
    const retryAndDispatch = vi.spyOn(parserApi, 'retryAndDispatchJob').mockResolvedValue({
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

    await waitFor(() => expect(retryAndDispatch).toHaveBeenCalledWith(failedReadyJob.id))
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
    await waitFor(() => expect(suggestSpec).toHaveBeenCalledWith(succeededJob.id, ''))
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

  it('shows only one approval entry on the human confirmation step', async () => {
    const succeededJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-awaiting-review',
      status: 'succeeded',
      last_error: undefined,
    }
    const candidate: ParserTemplateItem = {
      id: 'template-awaiting-review',
      vendor: succeededJob.vendor,
      model: succeededJob.model,
      profile: succeededJob.profile,
      canonical_command: succeededJob.canonical_command,
      category: 'vpc',
      version: 'candidate-1',
      status: 'pending_approval',
      source_job_id: succeededJob.id,
      validation_json: {
        ai_review: {
          passed: true,
          summary: '解析结果与样本语义一致。',
        },
      },
      created_at: '2026-08-08T10:00:00Z',
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(succeededJob)
    vi.spyOn(parserApi, 'templates').mockResolvedValue({
      items: [candidate],
      total: 1,
      limit: 100,
      offset: 0,
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

    expect(await screen.findByText('人工最终确认')).toBeVisible()
    expect(screen.getAllByRole('button', { name: '审查并确认' })).toHaveLength(1)
    expect(screen.queryByText('候选正在等待人工确认')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '前往人工确认' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /返回上一步/ }))
    expect(await screen.findByText(/AI 复核记录/)).toBeVisible()
    expect(screen.getByText('候选正在等待人工确认')).toBeVisible()
    expect(screen.getByRole('button', { name: '前往人工确认' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '审查并确认' })).not.toBeInTheDocument()
  })

  it('keeps a new candidate visible when an older active template exists', async () => {
    const succeededJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-new-version',
      status: 'succeeded',
      last_error: undefined,
    }
    const active: ParserTemplateItem = {
      id: 'template-active-old',
      vendor: succeededJob.vendor,
      model: succeededJob.model,
      profile: succeededJob.profile,
      canonical_command: succeededJob.canonical_command,
      category: 'vpc',
      version: 'v1',
      status: 'active',
      created_at: '2026-08-07T10:00:00Z',
    }
    const candidate: ParserTemplateItem = {
      ...active,
      id: 'template-candidate-new',
      version: 'v2',
      status: 'pending_approval',
      source_job_id: succeededJob.id,
      created_at: '2026-08-08T10:00:00Z',
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(succeededJob)
    vi.spyOn(parserApi, 'templates').mockResolvedValue({
      items: [active, candidate],
      total: 2,
      limit: 100,
      offset: 0,
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={succeededJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('待人工确认')).toBeVisible()
    expect(screen.getByRole('button', { name: '审查并确认' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '查看完整审批证据' })).not.toBeInTheDocument()
  })

  it('offers one read-only evidence entry for a published Parser', async () => {
    const succeededJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-published',
      status: 'succeeded',
      last_error: undefined,
    }
    const active: ParserTemplateItem = {
      id: 'template-active',
      vendor: succeededJob.vendor,
      model: succeededJob.model,
      profile: succeededJob.profile,
      canonical_command: succeededJob.canonical_command,
      category: 'vpc',
      version: 'v1',
      status: 'active',
      source_job_id: succeededJob.id,
      created_at: '2026-08-08T10:00:00Z',
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(succeededJob)
    vi.spyOn(parserApi, 'templates').mockResolvedValue({
      items: [active],
      total: 1,
      limit: 100,
      offset: 0,
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={succeededJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('Parser 已发布')).toBeVisible()
    expect(screen.getByRole('button', { name: '查看完整审批证据' })).toBeEnabled()
    expect(screen.getAllByText('Parser 已发布')).toHaveLength(1)
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

    expect(await screen.findByText('ParserSpec 已更新，等待重新确认')).toBeVisible()
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

  it('does not misreport a replay failure containing compile_success as a syntax error', async () => {
    const replayFailedJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-replay-failed',
      failure_code: 'generation_validation_failed',
      last_error: JSON.stringify({
        compile_success: true,
        errors: ['duplicate_primary_keys'],
      }),
      agent_state_json: {
        generation_progress: {
          stage: 'failed',
          terminal_reason: 'generation_validation_failed',
          details: {
            stop_reason: 'generation_validation_failed',
          },
        },
      },
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(replayFailedJob)
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={replayFailedJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('样本重放或记录校验未通过')).toBeVisible()
    expect(screen.queryByText('生成的 TextFSM 未通过语法校验')).not.toBeInTheDocument()
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
    const retryAndDispatch = vi.spyOn(parserApi, 'retryAndDispatchJob').mockResolvedValue({
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

    await waitFor(() => expect(retryAndDispatch).toHaveBeenCalledWith(rejectedCandidateJob.id))
  })

  it('lets an engineer move backward and inspect every durable generation result', async () => {
    vi.spyOn(parserApi, 'job').mockResolvedValue(failedReadyJob)
    vi.mocked(parserApi.jobAttempts).mockResolvedValue([{
      id: 'attempt-1',
      job_id: failedReadyJob.id,
      attempt_number: 1,
      stage: 'template',
      action: 'llm_tool_round_1',
      model_provider: 'llm:native_tool_call',
      model_name: 'deepseek-v4-pro',
      textfsm_runtime_version: '2.1.0',
      dialect_version: 'platform-v3',
      dialect_hash: 'dialect-hash',
      template_sha256: 'candidate-sha',
      template_text: 'Value interface (\\S+)\n\nStart\n ^${interface} -> Record',
      validation_report_json: {
        generation: {
          passed: true,
          hard_gates: { compiled: true, executed: true },
          diagnostics: [],
          samples: [{
            sample_id: 'sample-1',
            role: 'generation',
            passed: true,
            record_count: 1,
            records: [{ interface: 'Eth1/1', status: 'up' }],
            errors: [],
          }],
        },
        ai_review: {
          passed: false,
          summary: '接口状态字段仍需修复。',
          diagnostics: [{ code: 'missing_records', message: '补齐状态记录。' }],
        },
      },
      status: 'needs_review',
      created_at: '2026-07-26T10:00:00Z',
    }])
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

    expect(await screen.findByText('TextFSM 生成轮次')).toBeVisible()
    expect(await screen.findByText('候选记录 1')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: /返回上一步/ }))
    expect(await screen.findByText('设计并确认语义方案')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: /返回上一步/ }))
    expect(await screen.findByText('先确认 CLI 样本')).toBeVisible()
  })

  it('lets an operator cancel an active Parser generation without losing the job', async () => {
    const runningJob: ParserJobItem = {
      ...failedReadyJob,
      id: 'parser-job-running',
      status: 'running',
      last_error: undefined,
    }
    vi.spyOn(parserApi, 'job').mockResolvedValue(runningJob)
    const cancelJob = vi.spyOn(parserApi, 'cancelJob').mockResolvedValue({
      success: true,
      job_id: runningJob.id,
      status: 'cancelled',
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ParserGenerationStep jobId={runningJob.id} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await userEvent.click(await screen.findByRole('button', { name: /取消生成/ }))
    await userEvent.click(await screen.findByRole('button', { name: '确认取消' }))

    await waitFor(() => expect(cancelJob).toHaveBeenCalledWith(runningJob.id))
  })
})
