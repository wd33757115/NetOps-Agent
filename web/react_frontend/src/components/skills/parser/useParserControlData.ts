import { useEffect, useMemo } from 'react'
import { useQuery, useQueryClient } from 'react-query'
import {
  parserApi,
  type ParserChangePolicy,
  type ParserEventRule,
  type ParserObservationContract,
  type ParserPageParams,
  type ParserRuntimeRuleAssetBundle,
} from '../../../services/api'
import {
  buildRuleAssetChainRows,
  ruleJobNextAction,
} from './parserWorkspaceModel'

export type ParserPagedResource =
  | 'workItems'
  | 'templates'
  | 'jobs'
  | 'backfills'
  | 'ruleJobs'
  | 'contracts'
  | 'policies'
  | 'eventRules'
  | 'aliases'
  | 'deviceProfiles'
  | 'identitySignatures'
  | 'commandProfiles'
  | 'commandProfileBindings'
  | 'replayReports'
  | 'approvalAudit'
  | 'agentLoopAttempts'

export interface ParserPageState {
  page: number
  pageSize: number
}

interface ParserControlFilters {
  workspace: string
  subWorkspace: string
  jobId: string | null
  workItemStatus?: string
  workItemType?: string
  workItemSearch: string
  templateStatus?: string
  templateSearch: string
  backfillTemplateSearch: string
  jobStatus?: string
  jobType?: string
  jobSearch: string
  eventRuleStatus?: string
  eventRuleSearch: string
  ruleJobStatus?: string
  ruleJobAction?: string
  ruleJobAssetType?: string
  ruleJobSearch: string
  linkedDatasetIds: string[]
  pages: Record<ParserPagedResource, ParserPageState>
}

const visibleInterval = (milliseconds: number) => () => (
  typeof document !== 'undefined' && document.visibilityState === 'hidden'
    ? false
    : milliseconds
)

const DYNAMIC_POLLING = {
  refetchInterval: visibleInterval(10000),
  refetchIntervalInBackground: false,
  staleTime: 5000,
  keepPreviousData: true,
}
const RULE_ASSET_POLLING = {
  refetchInterval: visibleInterval(30000),
  refetchIntervalInBackground: false,
  staleTime: 20000,
  keepPreviousData: true,
}
const GOVERNANCE_POLLING = {
  refetchInterval: visibleInterval(60000),
  refetchIntervalInBackground: false,
  staleTime: 45000,
  keepPreviousData: true,
}
const STATIC_QUERY = {
  refetchOnWindowFocus: true,
  staleTime: 30000,
  keepPreviousData: true,
}

const WORKSPACE_QUERY_KEYS: Record<string, string[]> = {
  inbox: [
    'parser-work-items', 'parser-jobs', 'parser-backfills', 'parser-active-template-options',
    'parser-dispatch-control',
  ],
  inventory: [
    'parser-templates', 'parser-aliases', 'parser-device-profiles',
    'parser-identity-signatures', 'parser-command-profiles', 'parser-command-profile-bindings',
    'parser-active-command-profile-options',
    'parser-observation-contracts', 'parser-change-policies', 'parser-event-rules',
  ],
  generation: ['parser-rule-generation-jobs', 'parser-agent-loop-attempts', 'parser-datasets'],
  release: ['parser-replay-reports'],
  runtime: ['parser-rule-generation-jobs', 'parser-runtime-assets'],
  audit: ['parser-approval-audit'],
}

const pageParams = (
  state: ParserPageState,
  params: ParserPageParams = {},
): ParserPageParams => ({
  ...params,
  limit: state.pageSize,
  offset: (state.page - 1) * state.pageSize,
})

export const useParserControlData = (filters: ParserControlFilters) => {
  const queryClient = useQueryClient()
  const inboxActive = filters.workspace === 'inbox'
  const inventoryActive = filters.workspace === 'inventory'
  const generationActive = filters.workspace === 'generation'
  const releaseActive = filters.workspace === 'release'
  const runtimeActive = filters.workspace === 'runtime'
  const auditActive = filters.workspace === 'audit'

  useEffect(() => {
    const activeKeys = new Set(WORKSPACE_QUERY_KEYS[filters.workspace] || [])
    Object.values(WORKSPACE_QUERY_KEYS).flat().forEach((queryKey) => {
      if (!activeKeys.has(queryKey)) void queryClient.cancelQueries(queryKey)
    })
  }, [filters.workspace, queryClient])

  const { data: coverage, error: coverageError } = useQuery(
    'parser-coverage', parserApi.coverage, RULE_ASSET_POLLING,
  )

  const workItemParams = pageParams(filters.pages.workItems, {
    status: filters.workItemStatus,
    task_type: filters.workItemType,
    search: filters.workItemSearch.trim() || undefined,
  })
  const workItemsQuery = useQuery(
    ['parser-work-items', workItemParams],
    () => parserApi.workItems(workItemParams),
    { ...DYNAMIC_POLLING, enabled: inboxActive && filters.subWorkspace === 'work-items' },
  )

  const templateParams = pageParams(filters.pages.templates, {
    status: filters.templateStatus,
    search: filters.templateSearch.trim() || undefined,
  })
  const templatesQuery = useQuery(
    ['parser-templates', templateParams],
    () => parserApi.templates(templateParams),
    { ...STATIC_QUERY, enabled: inventoryActive && filters.subWorkspace === 'templates' },
  )
  const activeTemplateOptionsParams: ParserPageParams = {
    status: 'active',
    search: filters.backfillTemplateSearch.trim() || undefined,
    limit: 50,
    offset: 0,
  }
  const activeTemplateOptionsQuery = useQuery(
    ['parser-active-template-options', activeTemplateOptionsParams],
    () => parserApi.templates(activeTemplateOptionsParams),
    {
      ...STATIC_QUERY,
      enabled: inboxActive && filters.subWorkspace === 'backfills',
    },
  )

  const jobParams = pageParams(filters.pages.jobs, {
    status: filters.jobStatus,
    job_type: filters.jobType,
    search: filters.jobSearch.trim() || undefined,
  })
  const jobsQuery = useQuery(
    ['parser-jobs', jobParams],
    () => parserApi.jobs(jobParams),
    { ...DYNAMIC_POLLING, enabled: inboxActive && filters.subWorkspace === 'jobs' },
  )

  const backfillParams = pageParams(filters.pages.backfills)
  const backfillsQuery = useQuery(
    ['parser-backfills', backfillParams],
    () => parserApi.backfills(backfillParams),
    { ...DYNAMIC_POLLING, enabled: inboxActive && filters.subWorkspace === 'backfills' },
  )

  const ruleJobParams = pageParams(filters.pages.ruleJobs, {
    status: filters.ruleJobStatus,
    action: filters.ruleJobAction,
    asset_type: filters.ruleJobAssetType,
    search: filters.ruleJobSearch.trim() || undefined,
  })
  const ruleJobsQuery = useQuery(
    ['parser-rule-generation-jobs', ruleJobParams],
    () => parserApi.ruleGenerationJobs(ruleJobParams),
    {
      ...DYNAMIC_POLLING,
      enabled: (generationActive && filters.subWorkspace === 'rule-generation')
        || (runtimeActive && filters.subWorkspace === 'asset-chain'),
    },
  )

  const contractParams = pageParams(filters.pages.contracts)
  const contractsQuery = useQuery(
    ['parser-observation-contracts', contractParams],
    () => parserApi.observationContracts(contractParams),
    { ...RULE_ASSET_POLLING, enabled: inventoryActive && filters.subWorkspace === 'observation-contracts' },
  )
  const policyParams = pageParams(filters.pages.policies)
  const policiesQuery = useQuery(
    ['parser-change-policies', policyParams],
    () => parserApi.changePolicies(policyParams),
    { ...RULE_ASSET_POLLING, enabled: inventoryActive && filters.subWorkspace === 'change-policies' },
  )
  const eventRuleParams = pageParams(filters.pages.eventRules, {
    status: filters.eventRuleStatus,
    search: filters.eventRuleSearch.trim() || undefined,
  })
  const eventRulesQuery = useQuery(
    ['parser-event-rules', eventRuleParams],
    () => parserApi.eventRules(eventRuleParams),
    { ...RULE_ASSET_POLLING, enabled: inventoryActive && filters.subWorkspace === 'event-rules' },
  )

  const replayParams = pageParams(filters.pages.replayReports)
  const replayQuery = useQuery(
    ['parser-replay-reports', replayParams],
    () => parserApi.replayReports(replayParams),
    { ...GOVERNANCE_POLLING, enabled: releaseActive && filters.subWorkspace === 'replay-reports' },
  )
  const auditParams = pageParams(filters.pages.approvalAudit)
  const auditQuery = useQuery(
    ['parser-approval-audit', auditParams],
    () => parserApi.approvalAudit(auditParams),
    { ...GOVERNANCE_POLLING, enabled: auditActive && filters.subWorkspace === 'approval-audit' },
  )
  const attemptParams = pageParams(filters.pages.agentLoopAttempts)
  const attemptsQuery = useQuery(
    ['parser-agent-loop-attempts', attemptParams],
    () => parserApi.agentLoopAttempts(attemptParams),
    { ...GOVERNANCE_POLLING, enabled: generationActive && filters.subWorkspace === 'agent-loop-attempts' },
  )

  const { data: dispatchControl } = useQuery(
    'parser-dispatch-control', parserApi.dispatchControl,
    { ...DYNAMIC_POLLING, enabled: inboxActive },
  )
  const { data: jobDetail, isLoading: jobLoading } = useQuery(
    ['parser-job', filters.jobId], () => parserApi.job(filters.jobId as string),
    {
      enabled: Boolean(filters.jobId),
      refetchInterval: filters.jobId ? visibleInterval(5000) : false,
      refetchIntervalInBackground: false,
    },
  )

  const aliasParams = pageParams(filters.pages.aliases)
  const aliasesQuery = useQuery(
    ['parser-aliases', aliasParams], () => parserApi.aliases(aliasParams),
    { ...STATIC_QUERY, enabled: inventoryActive && filters.subWorkspace === 'aliases' },
  )
  const deviceProfileParams = pageParams(filters.pages.deviceProfiles)
  const deviceProfilesQuery = useQuery(
    ['parser-device-profiles', deviceProfileParams],
    () => parserApi.deviceProfiles(deviceProfileParams),
    { ...STATIC_QUERY, enabled: inventoryActive && filters.subWorkspace === 'profiles' },
  )
  const identitySignatureParams = pageParams(filters.pages.identitySignatures)
  const identitySignaturesQuery = useQuery(
    ['parser-identity-signatures', identitySignatureParams],
    () => parserApi.identitySignatures(identitySignatureParams),
    { ...STATIC_QUERY, enabled: inventoryActive && filters.subWorkspace === 'signatures' },
  )
  const datasetScopeKey = filters.linkedDatasetIds.join(',')
  const { data: datasets = [], error: datasetsError } = useQuery(
    ['parser-datasets', datasetScopeKey],
    () => parserApi.datasets(filters.linkedDatasetIds),
    {
      ...GOVERNANCE_POLLING,
      enabled: generationActive
        && (filters.subWorkspace === 'datasets' || filters.subWorkspace === 'rule-generation'),
    },
  )
  const commandProfileParams = pageParams(filters.pages.commandProfiles)
  const commandProfilesQuery = useQuery(
    ['parser-command-profiles', commandProfileParams],
    () => parserApi.commandProfiles(commandProfileParams),
    { ...STATIC_QUERY, enabled: inventoryActive && filters.subWorkspace === 'command-profiles' },
  )
  const commandProfileBindingParams = pageParams(filters.pages.commandProfileBindings)
  const commandProfileBindingsQuery = useQuery(
    ['parser-command-profile-bindings', commandProfileBindingParams],
    () => parserApi.commandProfileBindings(commandProfileBindingParams),
    { ...STATIC_QUERY, enabled: inventoryActive && filters.subWorkspace === 'command-profiles' },
  )
  const activeCommandProfileOptionsQuery = useQuery(
    'parser-active-command-profile-options',
    () => parserApi.commandProfiles({ status: 'active', limit: 100, offset: 0 }),
    { ...STATIC_QUERY, enabled: inventoryActive && filters.subWorkspace === 'command-profiles' },
  )

  const runtimeQuery = useQuery<ParserRuntimeRuleAssetBundle>(
    'parser-runtime-assets', parserApi.runtimeAssetHealth,
    { ...RULE_ASSET_POLLING, enabled: runtimeActive },
  )
  const runtimeAssets = runtimeQuery.data
  const runtimeSchemas = useMemo(() => (
    Object.keys(runtimeAssets?.observation_contracts || {}).map((key) => {
      const separator = key.indexOf(':')
      return [key.slice(0, separator), key.slice(separator + 1)] as [string, string]
    })
  ), [runtimeAssets])
  const runtimeObservationTypes = runtimeAssets?.observation_types || []
  const ruleAssetChainRows = useMemo(() => {
    const contracts = Object.values(runtimeAssets?.observation_contracts || {}).map((item) => ({
      ...item,
      id: String(item.id || item.contract_id || item.schema_id || ''),
      status: 'active',
    })) as ParserObservationContract[]
    const policies = Object.values(runtimeAssets?.change_policies || {}) as ParserChangePolicy[]
    const rules = (runtimeAssets?.event_rules || []) as ParserEventRule[]
    return buildRuleAssetChainRows(contracts, policies, rules)
  }, [runtimeAssets])

  const templates = templatesQuery.data?.items || []
  const workItems = workItemsQuery.data?.items || []
  const jobs = jobsQuery.data?.items || []
  const backfills = backfillsQuery.data?.items || []
  const ruleGenerationJobs = ruleJobsQuery.data?.items || []
  const observationContracts = contractsQuery.data?.items || []
  const changePolicies = policiesQuery.data?.items || []
  const eventRules = eventRulesQuery.data?.items || []
  const replayReports = replayQuery.data?.items || []
  const approvalAudit = auditQuery.data?.items || []
  const agentLoopAttempts = attemptsQuery.data?.items || []
  const aliases = aliasesQuery.data?.items || []
  const deviceProfiles = deviceProfilesQuery.data?.items || []
  const identitySignatures = identitySignaturesQuery.data?.items || []
  const commandProfiles = commandProfilesQuery.data?.items || []
  const commandProfileBindings = commandProfileBindingsQuery.data?.items || []

  const ruleJobStatusCounts = coverage?.rule_generation_status_counts || {}
  const eventRuleStatusCounts = eventRulesQuery.data?.status_counts || {}
  const ruleJobActionCounts = {
    submit: ruleJobStatusCounts.validating || 0,
    approval: ruleJobStatusCounts.pending_approval || 0,
    active: ruleJobStatusCounts.active || 0,
    revision: (ruleJobStatusCounts.failed || 0) + (ruleJobStatusCounts.draft || 0),
  }
  const activeRuntimeCounts = runtimeAssets?.counts || { observation_contracts: 0, change_policies: 0, event_rules: 0 }
  const ruleJobAssetTypeOptions = [
    'OBSERVATION_CONTRACT', 'CHANGE_POLICY', 'EVENT_RULE', 'RULE_ASSET_BUNDLE',
  ].map((value) => ({ value, label: value }))

  const workspaceErrors = useMemo(() => {
    const groups: Record<string, Array<{ source: string; error: unknown }>> = {
      inbox: [
        { source: '统一待办', error: workItemsQuery.error },
        { source: 'Parser 任务', error: jobsQuery.error },
        { source: '历史回填', error: backfillsQuery.error },
        { source: 'Active 模板选择器', error: activeTemplateOptionsQuery.error },
      ],
      inventory: [
        { source: '模板资产', error: templatesQuery.error },
        { source: '命令别名', error: aliasesQuery.error },
        { source: '设备档案', error: deviceProfilesQuery.error },
        { source: '身份规则', error: identitySignaturesQuery.error },
        { source: '采集命令集', error: commandProfilesQuery.error },
        { source: '命令集绑定', error: commandProfileBindingsQuery.error },
        { source: 'ObservationContract', error: contractsQuery.error },
        { source: 'ChangePolicy', error: policiesQuery.error },
        { source: 'EventRule', error: eventRulesQuery.error },
      ],
      generation: [
        { source: '规则资产任务', error: ruleJobsQuery.error },
        { source: 'Agent Loop', error: attemptsQuery.error },
        { source: '验证数据集', error: datasetsError },
      ],
      release: [
        { source: '历史回放', error: replayQuery.error },
      ],
      runtime: [
        { source: '规则资产任务', error: ruleJobsQuery.error },
        { source: '运行时健康', error: runtimeQuery.error },
      ],
      audit: [
        { source: '审批审计', error: auditQuery.error },
      ],
    }
    return (groups[filters.workspace] || []).filter((item) => Boolean(item.error))
  }, [
    activeTemplateOptionsQuery.error, aliasesQuery.error, attemptsQuery.error, auditQuery.error, backfillsQuery.error,
    commandProfileBindingsQuery.error, commandProfilesQuery.error, contractsQuery.error,
    datasetsError, deviceProfilesQuery.error, eventRulesQuery.error, filters.workspace,
    identitySignaturesQuery.error, jobsQuery.error, policiesQuery.error, replayQuery.error,
    ruleJobsQuery.error, runtimeQuery.error, templatesQuery.error, workItemsQuery.error,
  ])

  const refresh = () => {
    Object.values(WORKSPACE_QUERY_KEYS).flat().forEach((key) => queryClient.invalidateQueries(key))
    queryClient.invalidateQueries('parser-coverage')
    if (filters.jobId) queryClient.invalidateQueries(['parser-job', filters.jobId])
  }

  return {
    coverage,
    coverageError,
    workspaceErrors,
    workItems,
    workItemsLoading: workItemsQuery.isLoading,
    workItemTotal: workItemsQuery.data?.total || 0,
    templates,
    templatesLoading: templatesQuery.isLoading,
    templateTotal: templatesQuery.data?.total || 0,
    activeTemplateOptions: activeTemplateOptionsQuery.data?.items || [],
    activeTemplateOptionTotal: activeTemplateOptionsQuery.data?.total || 0,
    activeTemplateOptionsLoading: activeTemplateOptionsQuery.isLoading,
    jobs,
    jobsLoading: jobsQuery.isLoading,
    jobTotal: jobsQuery.data?.total || 0,
    backfills,
    backfillsLoading: backfillsQuery.isLoading,
    backfillTotal: backfillsQuery.data?.total || 0,
    ruleGenerationJobs,
    ruleGenerationJobsLoading: ruleJobsQuery.isLoading,
    ruleGenerationJobTotal: ruleJobsQuery.data?.total || 0,
    observationContracts,
    observationContractsLoading: contractsQuery.isLoading,
    observationContractTotal: contractsQuery.data?.total || 0,
    changePolicies,
    changePoliciesLoading: policiesQuery.isLoading,
    changePolicyTotal: policiesQuery.data?.total || 0,
    replayReports,
    replayReportsLoading: replayQuery.isLoading,
    replayReportTotal: replayQuery.data?.total || 0,
    approvalAudit,
    approvalAuditLoading: auditQuery.isLoading,
    approvalAuditTotal: auditQuery.data?.total || 0,
    agentLoopAttempts,
    agentLoopAttemptsLoading: attemptsQuery.isLoading,
    agentLoopAttemptTotal: attemptsQuery.data?.total || 0,
    dispatchControl,
    jobDetail,
    jobLoading,
    aliases,
    aliasesLoading: aliasesQuery.isLoading,
    aliasTotal: aliasesQuery.data?.total || 0,
    deviceProfiles,
    deviceProfilesLoading: deviceProfilesQuery.isLoading,
    deviceProfileTotal: deviceProfilesQuery.data?.total || 0,
    identitySignatures,
    identitySignaturesLoading: identitySignaturesQuery.isLoading,
    identitySignatureTotal: identitySignaturesQuery.data?.total || 0,
    datasets,
    eventRules,
    eventRuleTotal: eventRulesQuery.data?.total || 0,
    commandProfiles,
    commandProfilesLoading: commandProfilesQuery.isLoading,
    commandProfileTotal: commandProfilesQuery.data?.total || 0,
    activeCommandProfileOptions: activeCommandProfileOptionsQuery.data?.items || [],
    activeCommandProfileOptionTotal: activeCommandProfileOptionsQuery.data?.total || 0,
    commandProfileBindings,
    commandProfileBindingsLoading: commandProfileBindingsQuery.isLoading,
    commandProfileBindingTotal: commandProfileBindingsQuery.data?.total || 0,
    runtimeSchemas,
    runtimeObservationTypes,
    ruleAssetChainRows,
    runtimeAssets,
    runtimeAssetsLoading: runtimeQuery.isLoading,
    filteredTemplates: templates,
    filteredJobs: jobs,
    filteredEventRules: eventRules,
    eventRuleStatusCounts,
    ruleJobStatusCounts,
    ruleJobActionCounts,
    activeRuntimeCounts: {
      contracts: activeRuntimeCounts.observation_contracts,
      policies: activeRuntimeCounts.change_policies,
      eventRules: activeRuntimeCounts.event_rules,
    },
    ruleJobAssetTypeOptions,
    filteredRuleGenerationJobs: ruleGenerationJobs,
    ruleJobNextAction,
    refresh,
  }
}
