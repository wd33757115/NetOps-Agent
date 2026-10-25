import type { OfflinePatrolAnalysisSummary, OfflinePatrolJobPurpose } from '../../services/api'

/** Presentation follows the report contract; zero findings alone never means healthy. */
export function offlineResultPresentation(report: OfflinePatrolAnalysisSummary, purpose?: OfflinePatrolJobPurpose) {
  const validation = purpose === 'rule_asset_source'
    || ['parser_validation_only', 'rule_replay_only'].includes(report.assessment.code)
  const complete = report.data_quality.status === 'ready'
  const allowed = report.assessment.conclusion_allowed === true
  const hasRisks = report.risks.length > 0
  return {
    validation,
    tone: validation ? 'info' as const : hasRisks ? 'error' as const
      : allowed && complete ? 'success' as const : 'warning' as const,
    riskLabel: '分析期风险',
    emptyText: allowed && complete ? '本次已覆盖范围内未发现风险' : '尚不能形成完整风险结论，请查看覆盖缺口和证据',
    reportLabel: validation ? '下载验证报告' : '下载巡检报告',
    workbookLabel: validation ? '下载验证明细' : '下载巡检明细 Excel',
  }
}
