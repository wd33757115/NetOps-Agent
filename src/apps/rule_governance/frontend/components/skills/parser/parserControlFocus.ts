export type ParserWorkspaceKey =
  | 'transfer'
  | 'production'
  | 'inbox'
  | 'inventory'
  | 'generation'
  | 'release'
  | 'runtime'
  | 'audit'

export type ParserSubWorkspaceKey =
  | 'rule-packages'
  | 'asset-production-units'
  | 'source-jobs'
  | 'catalog-management'
  | 'assessment-policies'
  | 'published-assets'
  | 'coverage-backfill'
  | 'audit-log'
  | 'work-items'
  | 'jobs'
  | 'templates'
  | 'aliases'
  | 'signatures'
  | 'profiles'
  | 'command-profiles'
  | 'rule-generation'
  | 'asset-bundles'
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
  | 'rule-gaps'

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
  legacyRedirect?: boolean
  productAction?: 'parser' | 'rule-generation' | 'bundle-review' | 'backfill' | null
}

const focusMap: Record<ParserFocusKind, { workspace: ParserWorkspaceKey; subWorkspace: ParserSubWorkspaceKey }> = {
  'parser-template': {
    workspace: 'inventory',
    subWorkspace: 'published-assets',
  },
  snapshot: {
    workspace: 'inventory',
    subWorkspace: 'published-assets',
  },
  'observation-contract': {
    workspace: 'inventory',
    subWorkspace: 'published-assets',
  },
  'change-policy': {
    workspace: 'inventory',
    subWorkspace: 'published-assets',
  },
  'event-rule': {
    workspace: 'inventory',
    subWorkspace: 'published-assets',
  },
  change: {
    workspace: 'inventory',
    subWorkspace: 'published-assets',
  },
}

export const parserWorkspaceTabs: Record<ParserWorkspaceKey, ParserSubWorkspaceKey[]> = {
  transfer: ['rule-packages'],
  production: [
    'asset-production-units',
    'source-jobs',
    'catalog-management',
  ],
  inbox: ['work-items', 'jobs', 'backfills'],
  inventory: ['published-assets'],
  generation: ['rule-gaps', 'rule-generation', 'agent-loop-attempts', 'datasets'],
  release: ['asset-bundles', 'replay-reports'],
  runtime: ['coverage-backfill', 'rule-gaps'],
  audit: ['audit-log'],
}

const isWorkspace = (value: string | null): value is ParserWorkspaceKey =>
  value === 'transfer'
  || value === 'production'
  || value === 'inbox'
  || value === 'inventory'
  || value === 'generation'
  || value === 'release'
  || value === 'runtime'
  || value === 'audit'

const normalizeWorkspace = (
  value: string | null,
  subWorkspace: string | null,
): ParserWorkspaceKey | null => {
  if (isWorkspace(value)) return value
  if (value === 'offline' || value === 'asset-production') return 'production'
  if (value === 'today') return 'inbox'
  if (value === 'parser-assets') return 'inventory'
  if (value === 'rule-assets') {
    return subWorkspace === 'asset-chain' || subWorkspace === 'runtime-assets'
      ? 'runtime'
      : 'inventory'
  }
  if (value === 'governance') {
    if (subWorkspace === 'approval-audit') return 'audit'
    if (subWorkspace === 'replay-reports') return 'release'
    return 'generation'
  }
  return null
}

export const isSubWorkspace = (workspace: ParserWorkspaceKey, value: string | null): value is ParserSubWorkspaceKey =>
  value !== null && parserWorkspaceTabs[workspace].includes(value as ParserSubWorkspaceKey)

const legacySubWorkspaceAliases: Partial<Record<ParserWorkspaceKey, Record<string, ParserSubWorkspaceKey>>> = {
  production: {
    'assessment-policies': 'catalog-management',
    'check-items': 'catalog-management',
  },
  inventory: {
    templates: 'published-assets',
    aliases: 'published-assets',
    signatures: 'published-assets',
    profiles: 'published-assets',
    'command-profiles': 'published-assets',
    'observation-contracts': 'published-assets',
    'change-policies': 'published-assets',
    'event-rules': 'published-assets',
  },
  runtime: {
    'asset-chain': 'coverage-backfill',
    'runtime-assets': 'coverage-backfill',
  },
  audit: {
    'approval-audit': 'audit-log',
  },
}

const normalizeSubWorkspace = (workspace: ParserWorkspaceKey, candidate: string | null): ParserSubWorkspaceKey | null =>
  isSubWorkspace(workspace, candidate)
    ? (candidate as ParserSubWorkspaceKey)
    : candidate
      ? legacySubWorkspaceAliases[workspace]?.[candidate] || null
      : null

const legacyProductDestination = (
  workspace: ParserWorkspaceKey | null,
  subWorkspace: string | null,
): {
  workspace: ParserWorkspaceKey
  subWorkspace: ParserSubWorkspaceKey
  action: ParserControlFocus['productAction']
} | null => {
  if (workspace === 'generation') {
    if (subWorkspace === 'rule-gaps') {
      return {
        workspace: 'runtime',
        subWorkspace: 'rule-gaps',
        action: null,
      }
    }
    if (subWorkspace === 'datasets') {
      return {
        workspace: 'production',
        subWorkspace: 'source-jobs',
        action: null,
      }
    }
    return {
      workspace: 'production',
      subWorkspace: 'asset-production-units',
      action: 'rule-generation',
    }
  }
  if (workspace === 'release') {
    return {
      workspace: 'production',
      subWorkspace: 'asset-production-units',
      action: 'bundle-review',
    }
  }
  if (workspace === 'inbox') {
    if (subWorkspace === 'backfills') {
      return {
        workspace: 'runtime',
        subWorkspace: 'coverage-backfill',
        action: 'backfill',
      }
    }
    return {
      workspace: 'production',
      subWorkspace: 'asset-production-units',
      action: 'parser',
    }
  }
  return null
}

export const resolveParserControlFocus = (searchParams: URLSearchParams): ParserControlFocus => {
  const requestedFocus = searchParams.get('focus')
  const focus = requestedFocus as ParserFocusKind | null
  const id = searchParams.get('id')
  const requestedWorkspace = searchParams.get('workspace')
  const subWorkspace = searchParams.get('subtab')

  const rawWorkspace = normalizeWorkspace(requestedWorkspace, subWorkspace)
  const mappedFocus = focus ? focusMap[focus] : undefined
  const kind = mappedFocus ? focus : null
  const legacyDestination = mappedFocus
    ? null
    : legacyProductDestination(rawWorkspace, subWorkspace)
  const fallbackWorkspace = legacyDestination?.workspace || rawWorkspace || 'production'
  const fallbackSubWorkspace = legacyDestination?.subWorkspace || subWorkspace
  const normalizedFallbackSubWorkspace = normalizeSubWorkspace(fallbackWorkspace, fallbackSubWorkspace)
  const aliasedSubWorkspace = Boolean(
    subWorkspace
    && normalizedFallbackSubWorkspace
    && subWorkspace !== normalizedFallbackSubWorkspace,
  )
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
    subWorkspace: normalizedFallbackSubWorkspace,
    fallbackReason,
    fallbackMessage,
    focusHint: searchParams.get('command') || searchParams.get('q') || null,
    legacyRedirect: Boolean(legacyDestination || aliasedSubWorkspace),
    productAction: legacyDestination?.action || null,
  }

  if (!kind) {
    if (!fallbackReason && subWorkspace && !fallback.subWorkspace && !legacyDestination) {
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
