import { useEffect, useMemo } from 'react'
import { useQuery, useQueryClient } from 'react-query'
import { parserApi, type ParserRuntimeRuleAssetBundle } from '../../../services/api'
import {
  buildRuleAssetChainRows,
  canSubmitRuleCandidate,
  extractObservationTypesFromContracts,
  hasRevisionRequest,
  isAgentLoopRevisionJob,
  ruleJobActionKey,
  ruleJobNextAction,
  runtimeObservationSchemas,
} from './parserWorkspaceModel'

interface ParserControlFilters {
  workspace: string
  jobId: string | null
  templateStatus?: string
  templateSearch: string
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
}

const visibleInterval = (milliseconds: number) => () => (
  typeof document !== 'undefined' && document.visibilityState === 'hidden'
    ? false
    : milliseconds
)

const DYNAMIC_POLLING = {
  refetchInterval: visibleInterval(5000),
  refetchIntervalInBackground: false,
  staleTime: 2000,
}
const RULE_ASSET_POLLING = {
  refetchInterval: visibleInterval(15000),
  refetchIntervalInBackground: false,
  staleTime: 10000,
}
const GOVERNANCE_POLLING = {
  refetchInterval: visibleInterval(30000),
  refetchIntervalInBackground: false,
  staleTime: 20000,
}
const STATIC_QUERY = {
  refetchOnWindowFocus: true,
  staleTime: 30000,
}

const WORKSPACE_QUERY_KEYS: Record<string, string[]> = {
  today: [
    'parser-jobs', 'parser-backfills', 'parser-rule-generation-jobs',
    'parser-dispatch-control',
  ],
  'parser-assets': [
    'parser-templates', 'parser-aliases', 'parser-device-profiles',
    'parser-identity-signatures', 'parser-command-profiles',
    'parser-command-profile-bindings',
  ],
  'rule-assets': [
    'parser-observation-contracts', 'parser-change-policies', 'parser-event-rules',
    'parser-runtime-assets',
  ],
  governance: [
    'parser-replay-reports', 'parser-approval-audit',
    'parser-agent-loop-attempts', 'parser-datasets',
  ],
}

export const useParserControlData = (filters: ParserControlFilters) => {
  const queryClient = useQueryClient()
  const todayActive = filters.workspace === 'today'
  const parserAssetsActive = filters.workspace === 'parser-assets'
  const ruleAssetsActive = filters.workspace === 'rule-assets'
  const governanceActive = filters.workspace === 'governance'

  useEffect(() => {
    const activeKeys = new Set(WORKSPACE_QUERY_KEYS[filters.workspace] || [])
    Object.values(WORKSPACE_QUERY_KEYS).flat().forEach((queryKey) => {
      if (!activeKeys.has(queryKey)) void queryClient.cancelQueries(queryKey)
    })
  }, [filters.workspace, queryClient])

  const { data: coverage, error: coverageError } = useQuery(
    'parser-coverage', parserApi.coverage, RULE_ASSET_POLLING,
  )
  const { data: templates = [], isLoading: templatesLoading, error: templatesError } = useQuery(
    'parser-templates', parserApi.templates, { ...STATIC_QUERY, enabled: parserAssetsActive },
  )
  const { data: jobs = [], isLoading: jobsLoading, error: jobsError } = useQuery(
    'parser-jobs', parserApi.jobs, { ...DYNAMIC_POLLING, enabled: todayActive },
  )
  const { data: backfills = [], isLoading: backfillsLoading, error: backfillsError } = useQuery(
    'parser-backfills', parserApi.backfills, { ...DYNAMIC_POLLING, enabled: todayActive },
  )
  const {
    data: ruleGenerationJobs = [],
    isLoading: ruleGenerationJobsLoading,
    error: ruleGenerationJobsError,
  } = useQuery(
    'parser-rule-generation-jobs', parserApi.ruleGenerationJobs,
    { ...DYNAMIC_POLLING, enabled: todayActive },
  )
  const {
    data: observationContracts = [],
    isLoading: observationContractsLoading,
    error: observationContractsError,
  } = useQuery(
    'parser-observation-contracts', parserApi.observationContracts,
    { ...RULE_ASSET_POLLING, enabled: ruleAssetsActive },
  )
  const {
    data: changePolicies = [],
    isLoading: changePoliciesLoading,
    error: changePoliciesError,
  } = useQuery(
    'parser-change-policies', parserApi.changePolicies,
    { ...RULE_ASSET_POLLING, enabled: ruleAssetsActive },
  )
  const { data: replayReports = [], isLoading: replayReportsLoading, error: replayReportsError } = useQuery(
    'parser-replay-reports', parserApi.replayReports,
    { ...GOVERNANCE_POLLING, enabled: governanceActive },
  )
  const { data: approvalAudit = [], isLoading: approvalAuditLoading, error: approvalAuditError } = useQuery(
    'parser-approval-audit', parserApi.approvalAudit,
    { ...GOVERNANCE_POLLING, enabled: governanceActive },
  )
  const {
    data: agentLoopAttempts = [],
    isLoading: agentLoopAttemptsLoading,
    error: agentLoopAttemptsError,
  } = useQuery(
    'parser-agent-loop-attempts', parserApi.agentLoopAttempts,
    { ...GOVERNANCE_POLLING, enabled: governanceActive },
  )
  const { data: dispatchControl } = useQuery(
    'parser-dispatch-control', parserApi.dispatchControl,
    { ...DYNAMIC_POLLING, enabled: todayActive },
  )
  const { data: jobDetail, isLoading: jobLoading } = useQuery(
    ['parser-job', filters.jobId], () => parserApi.job(filters.jobId as string),
    {
      enabled: Boolean(filters.jobId),
      refetchInterval: filters.jobId ? visibleInterval(5000) : false,
      refetchIntervalInBackground: false,
    },
  )
  const { data: aliases = [] } = useQuery(
    'parser-aliases', parserApi.aliases, { ...STATIC_QUERY, enabled: parserAssetsActive },
  )
  const { data: deviceProfiles = [] } = useQuery(
    'parser-device-profiles', parserApi.deviceProfiles, { ...STATIC_QUERY, enabled: parserAssetsActive },
  )
  const { data: identitySignatures = [] } = useQuery(
    'parser-identity-signatures', parserApi.identitySignatures,
    { ...STATIC_QUERY, enabled: parserAssetsActive },
  )
  const datasetScopeKey = filters.linkedDatasetIds.join(',')
  const { data: datasets = [], error: datasetsError } = useQuery(
    ['parser-datasets', datasetScopeKey],
    () => parserApi.datasets(filters.linkedDatasetIds),
    { ...GOVERNANCE_POLLING, enabled: governanceActive },
  )
  const { data: eventRules = [], error: eventRulesError } = useQuery(
    'parser-event-rules', parserApi.eventRules, { ...RULE_ASSET_POLLING, enabled: ruleAssetsActive },
  )
  const { data: commandProfiles = [], error: commandProfilesError } = useQuery(
    'parser-command-profiles', parserApi.commandProfiles,
    { ...STATIC_QUERY, enabled: parserAssetsActive },
  )
  const { data: commandProfileBindings = [], error: commandProfileBindingsError } = useQuery(
    'parser-command-profile-bindings', parserApi.commandProfileBindings,
    { ...STATIC_QUERY, enabled: parserAssetsActive },
  )

  const runtimeSchemas = useMemo(
    () => runtimeObservationSchemas(observationContracts),
    [observationContracts],
  )
  const runtimeObservationTypes = useMemo(() => {
    const inferred = extractObservationTypesFromContracts(
      observationContracts.filter((item) => item.status === 'active'),
    )
    if (inferred.length) return inferred
    return Array.from(new Set(
      changePolicies
        .filter((item) => item.version_status === 'active' && item.observation_type)
        .map((item) => item.observation_type),
    )).sort()
  }, [observationContracts, changePolicies])
  const ruleAssetChainRows = useMemo(
    () => buildRuleAssetChainRows(observationContracts, changePolicies, eventRules),
    [changePolicies, eventRules, observationContracts],
  )
  const { data: runtimeAssets, isLoading: runtimeAssetsLoading } =
    useQuery<ParserRuntimeRuleAssetBundle>(
      [
        'parser-runtime-assets',
        runtimeSchemas.map((item) => item.join(':')).join('|'),
        runtimeObservationTypes.join('|'),
      ],
      () => parserApi.resolveRuntimeAssets({
        observation_schemas: runtimeSchemas,
        observation_types: runtimeObservationTypes,
        include_event_rules: true,
      }),
      {
        ...RULE_ASSET_POLLING,
        enabled: ruleAssetsActive
          && Boolean(runtimeSchemas.length || runtimeObservationTypes.length || eventRules.length),
      },
    )

  const filteredTemplates = useMemo(() => templates.filter((item) => {
    const text = `${item.vendor} ${item.profile} ${item.canonical_command} ${item.category}`
      .toLowerCase()
    return (!filters.templateStatus || item.status === filters.templateStatus)
      && (!filters.templateSearch
        || text.includes(filters.templateSearch.trim().toLowerCase()))
  }), [filters.templateSearch, filters.templateStatus, templates])

  const filteredJobs = useMemo(() => jobs.filter((item) => {
    const text = `${item.vendor} ${item.profile} ${item.canonical_command}`.toLowerCase()
    return (!filters.jobStatus || item.status === filters.jobStatus)
      && (!filters.jobType || item.job_type === filters.jobType)
      && (!filters.jobSearch || text.includes(filters.jobSearch.trim().toLowerCase()))
  }), [filters.jobSearch, filters.jobStatus, filters.jobType, jobs])

  const filteredEventRules = useMemo(() => eventRules.filter((item) => {
    const text = `${item.rule_name} ${item.event_type} ${item.severity} ${item.version_status}`
      .toLowerCase()
    return (!filters.eventRuleStatus || item.version_status === filters.eventRuleStatus)
      && (!filters.eventRuleSearch
        || text.includes(filters.eventRuleSearch.trim().toLowerCase()))
  }), [eventRules, filters.eventRuleSearch, filters.eventRuleStatus])

  const eventRuleStatusCounts = useMemo(() => eventRules.reduce((acc, item) => {
    const status = item.version_status || 'unknown'
    acc[status] = (acc[status] || 0) + 1
    return acc
  }, {} as Record<string, number>), [eventRules])
  const ruleJobStatusCounts = useMemo(() => ruleGenerationJobs.reduce((acc, item) => {
    const status = item.status || 'unknown'
    acc[status] = (acc[status] || 0) + 1
    return acc
  }, {} as Record<string, number>), [ruleGenerationJobs])
  const ruleJobActionCounts = useMemo(() => ({
    submit: ruleGenerationJobs.filter(canSubmitRuleCandidate).length,
    approval: ruleGenerationJobs.filter((item) => item.status === 'pending_approval').length,
    active: ruleGenerationJobs.filter((item) => item.status === 'active').length,
    revision: ruleGenerationJobs.filter((item) =>
      hasRevisionRequest(item.validation_report_json) || isAgentLoopRevisionJob(item)).length,
  }), [ruleGenerationJobs])
  const activeRuntimeCounts = useMemo(() => ({
    contracts: observationContracts.filter((item) => item.status === 'active').length,
    policies: changePolicies.filter((item) => item.version_status === 'active').length,
    eventRules: eventRules.filter((item) => item.version_status === 'active').length,
  }), [changePolicies, eventRules, observationContracts])
  const ruleJobAssetTypeOptions = useMemo(() => {
    const values = new Set<string>()
    ruleGenerationJobs.forEach((item) => {
      if (item.asset_type) values.add(item.asset_type)
      if (item.job_type) values.add(item.job_type)
    })
    return Array.from(values).sort().map((value) => ({ value, label: value }))
  }, [ruleGenerationJobs])
  const filteredRuleGenerationJobs = useMemo(() => ruleGenerationJobs.filter((item) => {
    const actionKey = ruleJobActionKey(item)
    const assetType = item.asset_type || item.job_type || ''
    const text = [
      item.id, item.job_type, item.asset_type, item.status, item.source_ref,
      item.asset_ref, item.asset_id, item.asset_version_id, item.created_by,
      ruleJobNextAction(item),
    ].filter(Boolean).join(' ').toLowerCase()
    return (!filters.ruleJobStatus || item.status === filters.ruleJobStatus)
      && (!filters.ruleJobAction || actionKey === filters.ruleJobAction)
      && (!filters.ruleJobAssetType || assetType === filters.ruleJobAssetType)
      && (!filters.ruleJobSearch
        || text.includes(filters.ruleJobSearch.trim().toLowerCase()))
  }), [
    filters.ruleJobAction, filters.ruleJobAssetType, filters.ruleJobSearch,
    filters.ruleJobStatus, ruleGenerationJobs,
  ])

  const workspaceErrors = useMemo(() => {
    const groups: Record<string, Array<{ source: string; error: unknown }>> = {
      today: [
        { source: '任务队列', error: jobsError },
        { source: '历史回填', error: backfillsError },
        { source: '规则资产任务', error: ruleGenerationJobsError },
      ],
      'parser-assets': [
        { source: '模板资产', error: templatesError },
        { source: '采集命令集', error: commandProfilesError },
        { source: '命令集绑定', error: commandProfileBindingsError },
      ],
      'rule-assets': [
        { source: 'ObservationContract', error: observationContractsError },
        { source: 'ChangePolicy', error: changePoliciesError },
        { source: 'EventRule', error: eventRulesError },
      ],
      governance: [
        { source: '历史回放', error: replayReportsError },
        { source: '审批审计', error: approvalAuditError },
        { source: 'Agent Loop', error: agentLoopAttemptsError },
        { source: '验证数据集', error: datasetsError },
      ],
    }
    return (groups[filters.workspace] || []).filter((item) => Boolean(item.error))
  }, [
    agentLoopAttemptsError, approvalAuditError, backfillsError, changePoliciesError,
    commandProfileBindingsError, commandProfilesError, datasetsError, eventRulesError,
    filters.workspace, jobsError, observationContractsError, replayReportsError,
    ruleGenerationJobsError, templatesError,
  ])

  const refresh = () => {
    [
      'parser-coverage', 'parser-templates', 'parser-jobs', 'parser-aliases',
      'parser-device-profiles', 'parser-identity-signatures', 'parser-datasets',
      'parser-event-rules', 'parser-command-profiles', 'parser-command-profile-bindings',
      'parser-rule-generation-jobs', 'parser-observation-contracts',
      'parser-change-policies', 'parser-replay-reports', 'parser-approval-audit',
      'parser-agent-loop-attempts', 'parser-runtime-assets', 'parser-backfills',
    ].forEach((key) => queryClient.invalidateQueries(key))
    queryClient.invalidateQueries('parser-dispatch-control')
    if (filters.jobId) queryClient.invalidateQueries(['parser-job', filters.jobId])
  }

  return {
    coverage,
    coverageError,
    workspaceErrors,
    templates,
    templatesLoading,
    jobs,
    jobsLoading,
    backfills,
    backfillsLoading,
    ruleGenerationJobs,
    ruleGenerationJobsLoading,
    observationContracts,
    observationContractsLoading,
    changePolicies,
    changePoliciesLoading,
    replayReports,
    replayReportsLoading,
    approvalAudit,
    approvalAuditLoading,
    agentLoopAttempts,
    agentLoopAttemptsLoading,
    dispatchControl,
    jobDetail,
    jobLoading,
    aliases,
    deviceProfiles,
    identitySignatures,
    datasets,
    eventRules,
    commandProfiles,
    commandProfileBindings,
    runtimeSchemas,
    runtimeObservationTypes,
    ruleAssetChainRows,
    runtimeAssets,
    runtimeAssetsLoading,
    filteredTemplates,
    filteredJobs,
    filteredEventRules,
    eventRuleStatusCounts,
    ruleJobStatusCounts,
    ruleJobActionCounts,
    activeRuntimeCounts,
    ruleJobAssetTypeOptions,
    filteredRuleGenerationJobs,
    refresh,
  }
}
