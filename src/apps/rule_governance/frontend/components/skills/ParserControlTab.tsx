import React, { Suspense } from 'react'
import { Alert, Button, Spin } from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { resolveParserControlFocus } from './parser/parserControlFocus'
import RuleAssetProductionWorkbench from './parser/RuleAssetProductionWorkbench'
import ParserValueMappingManager from './parser/ParserValueMappingManager'
import './parser/parser-workspace.css'
import RuleWorkspaceLayout from '../../RuleWorkspaceLayout'

const PublishedAssetsWorkspace = React.lazy(() => import('./parser/PublishedAssetsWorkspace'))
const CoverageBackfillWorkspace = React.lazy(() => import('./parser/CoverageBackfillWorkspace'))
const AuditTimelineWorkspace = React.lazy(() => import('./parser/AuditTimelineWorkspace'))
const RulePackagesWorkspace = React.lazy(() => import('./parser/RulePackagesWorkspace'))

type Workspace = 'production' | 'inventory' | 'runtime' | 'audit' | 'transfer'

/** One product router. Legacy URLs resolve here, without mounting retired controllers. */
const ParserControlTab: React.FC<{ standalone?: boolean }> = ({ standalone = false }) => {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const focus = resolveParserControlFocus(searchParams)
  const workspace: Workspace = ['inventory', 'runtime', 'audit', 'transfer'].includes(focus.workspace)
    ? focus.workspace as Workspace : 'production'
  const subtab = focus.subWorkspace
  const navigateWorkspace = (nextWorkspace: Workspace, nextSubtab?: string) => {
    const next = new URLSearchParams()
    next.set('workspace', nextWorkspace)
    if (nextSubtab) next.set('subtab', nextSubtab)
    // Preserve a bounded return link while clearing stale asset-focus parameters.
    const origin = searchParams.get('return_to')
    if (origin?.startsWith('/apps/patrol?')) next.set('return_to', origin)
    setSearchParams(next)
  }
  const content = <div className="parser-control-root">
    {!standalone && <nav className="rule-asset-primary-nav" aria-label="规则资产控制面">
      {([
        ['production', '制作工作台'], ['inventory', '已发布资产'],
        ['runtime', '覆盖与回填'], ['audit', '审计记录'],
        ['transfer', '规则包导入导出'],
      ] as const).map(([key, label]) => <button type="button" key={key}
        className={workspace === key ? 'is-active' : ''}
        aria-current={workspace === key ? 'page' : undefined}
        onClick={() => navigateWorkspace(key)}>{label}</button>)}
      <details className="rule-workspace-tools"><summary>管理工具</summary><ParserValueMappingManager /></details>
    </nav>}
    {focus.fallbackReason && <Alert type="warning" showIcon message="已打开兼容视图"
      description={focus.fallbackMessage}
      action={<Button onClick={() => navigateWorkspace(workspace, subtab || undefined)}>使用推荐视图</Button>} />}
    {searchParams.get('return_to')?.startsWith('/apps/patrol?') && <Alert type="info" showIcon
      message="来自巡检的能力补齐任务" description="验证发布后，返回原任务重新分析；原结论保留。"
      action={<Button onClick={() => navigate(searchParams.get('return_to')!)}>返回原巡检</Button>} />}
    <Suspense fallback={<Spin aria-label="工作区加载中" />}>
      {workspace === 'inventory' ? <PublishedAssetsWorkspace focusKind={focus.kind} focusId={focus.id} />
        : workspace === 'transfer' ? <RulePackagesWorkspace />
        : workspace === 'runtime' ? <CoverageBackfillWorkspace />
        : workspace === 'audit' ? <AuditTimelineWorkspace />
        : <RuleAssetProductionWorkbench
          showNavigation={!standalone}
          view={subtab === 'source-jobs' ? 'sources' : subtab === 'catalog-management' ? 'catalog' : 'tasks'}
          onViewChange={(view) => navigateWorkspace('production', view === 'sources' ? 'source-jobs'
            : view === 'catalog' || view === 'policies' ? 'catalog-management' : 'asset-production-units')}
          initialSearch={searchParams.get('command') || ''}
          focusAction={searchParams.get('action') || focus.productAction || undefined}
          focusOfflineJobId={searchParams.get('offline_job_id') || searchParams.get('source_job_id') || undefined}
          focusBundleId={searchParams.get('bundle_id') || undefined}
          focusCheckItemVersionId={searchParams.get('check_item_version_id') || undefined}
          focusChangeId={searchParams.get('change_id') || undefined}
        />}
    </Suspense>
  </div>
  return standalone ? <RuleWorkspaceLayout>{content}</RuleWorkspaceLayout> : content
}

export default ParserControlTab
