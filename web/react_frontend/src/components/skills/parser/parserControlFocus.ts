export type ParserWorkspaceKey = 'today' | 'parser-assets' | 'rule-assets' | 'governance'

export type ParserSubWorkspaceKey =
  | 'jobs'
  | 'templates'
  | 'aliases'
  | 'signatures'
  | 'profiles'
  | 'command-profiles'
  | 'rule-generation'
  | 'backfills'
  | 'asset-chain'
  | 'runtime-assets'
  | 'observation-contracts'
  | 'change-policies'
  | 'event-rules'
  | 'replay-reports'
  | 'agent-loop-attempts'
  | 'approval-audit'
  | 'datasets'

export type ParserFocusKind =
  | 'parser-template'
  | 'observation-contract'
  | 'change-policy'
  | 'event-rule'
  | 'change'
  | 'snapshot'

export interface ParserControlFocus {
  kind: ParserFocusKind | null
  id: string | null
  workspace: ParserWorkspaceKey
  subWorkspace: ParserSubWorkspaceKey | null
  fallbackReason?:
    | 'unsupported_focus'
    | 'missing_focus_id'
    | 'invalid_workspace'
    | 'workspace_mismatch'
    | 'invalid_subtab'
    | null
  fallbackMessage?: string | null
  focusHint?: string | null
}

const focusMap: Record<ParserFocusKind, { workspace: ParserWorkspaceKey; subWorkspace: ParserSubWorkspaceKey }> = {
  'parser-template': {
    workspace: 'parser-assets',
    subWorkspace: 'templates',
  },
  snapshot: {
    workspace: 'parser-assets',
    subWorkspace: 'templates',
  },
  'observation-contract': {
    workspace: 'rule-assets',
    subWorkspace: 'observation-contracts',
  },
  'change-policy': {
    workspace: 'rule-assets',
    subWorkspace: 'change-policies',
  },
  'event-rule': {
    workspace: 'rule-assets',
    subWorkspace: 'event-rules',
  },
  change: {
    workspace: 'rule-assets',
    subWorkspace: 'change-policies',
  },
}

export const parserWorkspaceTabs: Record<ParserWorkspaceKey, ParserSubWorkspaceKey[]> = {
  today: ['jobs', 'rule-generation', 'backfills'],
  'parser-assets': ['templates', 'aliases', 'signatures', 'profiles', 'command-profiles'],
  'rule-assets': ['asset-chain', 'runtime-assets', 'observation-contracts', 'change-policies', 'event-rules'],
  governance: ['replay-reports', 'agent-loop-attempts', 'approval-audit', 'datasets'],
}

const isWorkspace = (value: string | null): value is ParserWorkspaceKey =>
  value === 'today' || value === 'parser-assets' || value === 'rule-assets' || value === 'governance'

export const isSubWorkspace = (workspace: ParserWorkspaceKey, value: string | null): value is ParserSubWorkspaceKey =>
  value !== null && parserWorkspaceTabs[workspace].includes(value as ParserSubWorkspaceKey)

const normalizeSubWorkspace = (workspace: ParserWorkspaceKey, candidate: string | null): ParserSubWorkspaceKey | null =>
  isSubWorkspace(workspace, candidate) ? (candidate as ParserSubWorkspaceKey) : null

export const resolveParserControlFocus = (searchParams: URLSearchParams): ParserControlFocus => {
  const requestedFocus = searchParams.get('focus')
  const focus = requestedFocus as ParserFocusKind | null
  const id = searchParams.get('id')
  const requestedWorkspace = searchParams.get('workspace')
  const subWorkspace = searchParams.get('subtab')

  const rawWorkspace = isWorkspace(requestedWorkspace) ? requestedWorkspace : null
  const mappedFocus = focus ? focusMap[focus] : undefined
  const kind = mappedFocus ? focus : null
  const fallbackWorkspace = rawWorkspace || 'today'
  let fallbackReason: ParserControlFocus['fallbackReason'] = null
  let fallbackMessage: string | null = null

  if (requestedFocus && !mappedFocus) {
    fallbackReason = 'unsupported_focus'
    fallbackMessage = `无法识别 focus=${requestedFocus}，已回退到可用工作区。`
  } else if (mappedFocus && !id) {
    fallbackReason = 'missing_focus_id'
    fallbackMessage = `focus=${requestedFocus} 缺少 id，已打开推荐资产列表。`
  } else if (requestedWorkspace && !rawWorkspace) {
    fallbackReason = 'invalid_workspace'
    fallbackMessage = `无法识别 workspace=${requestedWorkspace}，已回退到可用工作区。`
  }

  const fallback: ParserControlFocus = {
    kind: null,
    id: id || null,
    workspace: fallbackWorkspace,
    subWorkspace: normalizeSubWorkspace(fallbackWorkspace, subWorkspace),
    fallbackReason,
    fallbackMessage,
  }

  if (!kind) {
    if (!fallbackReason && subWorkspace && !fallback.subWorkspace) {
      fallback.fallbackReason = 'invalid_subtab'
      fallback.fallbackMessage = `subtab=${subWorkspace} 不属于 ${fallbackWorkspace}，已打开默认视图。`
    }
    return fallback
  }

  const mapped = focusMap[kind]
  const finalWorkspace = mapped.workspace
  const normalizedSubWorkspace = normalizeSubWorkspace(finalWorkspace, subWorkspace)
  if (rawWorkspace && rawWorkspace !== mapped.workspace) {
    fallbackReason = 'workspace_mismatch'
    fallbackMessage = `${kind} 属于 ${mapped.workspace}，已忽略冲突的 workspace=${rawWorkspace}。`
  } else if (subWorkspace && !normalizedSubWorkspace) {
    fallbackReason = 'invalid_subtab'
    fallbackMessage = `${kind} 无法定位到 subtab=${subWorkspace}，已打开 ${mapped.subWorkspace}。`
  }
  return {
    kind,
    id: id || null,
    workspace: finalWorkspace,
    subWorkspace: normalizedSubWorkspace || mapped.subWorkspace,
    fallbackReason,
    fallbackMessage,
    focusHint: kind === 'snapshot'
      ? 'Snapshot ID 用于保留证据上下文；模板表会打开，但精确模板定位需要 parser_template_id。'
      : kind === 'change'
        ? 'Change ID 用于保留证据上下文；策略表会打开，但精确策略定位需要 change_policy_id。'
        : null,
  }
}

export const hasFocusMatched = (
  focus: ParserControlFocus,
  candidate: {
    id?: string | null
    versionId?: string | null
    policyId?: string | null
    ruleId?: string | null
    sourceJobId?: string | null
    versionStatusId?: string | null
  },
) => {
  if (!focus.id) return false
  const normalizedId = focus.id
  return (
    candidate.id === normalizedId
    || candidate.versionId === normalizedId
    || candidate.policyId === normalizedId
    || candidate.ruleId === normalizedId
    || candidate.sourceJobId === normalizedId
    || candidate.versionStatusId === normalizedId
  )
}
