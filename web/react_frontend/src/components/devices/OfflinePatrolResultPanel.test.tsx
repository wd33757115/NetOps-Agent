import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import type { OfflinePatrolAnalysisSummary } from '../../services/api'
import OfflinePatrolResultPanel, { formatOfflineEntityKey } from './OfflinePatrolResultPanel'

const report: OfflinePatrolAnalysisSummary = {
  title: 'Q2-Q3 巡检报告',
  generated_at: '2026-07-15T00:00:00Z',
  assessment: {
    code: 'abnormal_partial',
    status: '发现重大网络风险',
    trust: '中',
    basis: '识别 2 项当前异常或故障事件',
    conclusion_allowed: true,
  },
  metrics: {
    parser_ready_count: 22,
    parser_eligible_count: 26,
    parser_ready_rate: 22 / 26,
    template_missing_count: 4,
    observation_count: 3140,
    change_count: 465,
  },
  risk_summary: {
    total: 2,
    by_severity: { major: 2 },
    by_type: { interface_link_down: 2 },
    correlated_evidence_count: 2,
  },
  event_summary: {
    fact_count: 26,
    fault_fact_count: 4,
    recovery_fact_count: 22,
    resolved_prior_fault_count: 2,
    state_improvement_count: 20,
    fault_by_type: { interface_link_down: 2, rx_power_low: 2 },
    recovery_by_type: { interface_link_up: 20, rx_power_low_recovery: 2 },
  },
  risks: [{
    event_type: 'interface_link_down',
    severity: 'major',
    device_id: 'SW1',
    device_name: 'SW1',
    entity_key: '["WGE1/0/15"]',
    current_value: 'DOWN',
    evidence_chain: {
      analysis_attempt_id: 'analysis-1',
      analysis_revision: 'goal-acceptance:q2-q3',
      workflow_run_id: 'workflow-1',
      current_snapshot_id: 'snapshot-1',
      current_observation_id: 'observation-1',
      parser_template_id: 'parser-1',
      observation_contract_id: 'contract-1',
      change_id: 'change-1',
      change_policy_id: 'policy-1',
      event_rule_id: 'rule-1',
      event_id: 'event-1',
    },
  }],
  correlated_evidence: [{
    event_type: 'rx_power_low',
    severity: 'warning',
    device_id: 'SW1',
    entity_key: '["Twenty-FiveGigE1/0/15"]',
    current_value: -36.96,
    unit: 'dbm',
    evidence_chain: { event_id: 'optical-event-1' },
  }],
  trend: {
    available: true,
    continuity_evidence: 'sha256_exact_match',
    period_count: 3,
    comparisons: [
      { change_count: 81, event_count: 4, command_count: 44, parser_ready_rate: 0.91 },
      { change_count: 465, event_count: 26, command_count: 54, parser_ready_rate: 0.84 },
    ],
    new_evidence_count: 4,
    persistent_evidence_count: 2,
    resolved_evidence_count: 4,
    new_evidence_groups: [{
      severity: 'major',
      event_type: 'interface_link_down',
      risk_name: '接口链路中断',
      device_name: 'SW1',
      count: 1,
      objects: 'WGE1/0/15',
      current_values: 'DOWN',
      criterion: 'UP → DOWN',
    }],
    persistent_evidence_groups: [{
      severity: 'major',
      event_type: 'transceiver_rx_power_low',
      risk_name: '接收光功率低于设备告警阈值',
      device_name: 'SW1',
      count: 2,
      objects: 'Twenty-FiveGigE1/0/42、43',
      current_values: '-36.96dBm',
      criterion: '当前值 ≤ 设备告警阈值',
    }],
    resolved_evidence_groups: [{
      severity: 'info',
      event_type: 'transceiver_rx_power_low_recovery',
      risk_name: '接收光功率恢复',
      device_name: 'SW1',
      count: 1,
      objects: 'Twenty-FiveGigE1/0/16',
      current_values: '-2.89dBm',
      criterion: '恢复至设备低告警阈值之外',
    }],
  },
  data_quality: { status: 'degraded' },
  evidence: {
    analysis: {
      attempt_id: 'analysis-1',
      revision: 'goal-acceptance:q2-q3',
      source_change_count: 465,
    },
  },
}

describe('OfflinePatrolResultPanel', () => {
  it('shows current risks, recovery facts, quality limits and evidence', async () => {
    const user = userEvent.setup()
    const downloadReport = vi.fn()
    const downloadWorkbook = vi.fn()
    render(
      <OfflinePatrolResultPanel
        report={report}
        loading={false}
        isMobile={false}
        onRetry={vi.fn()}
        onDownloadReport={downloadReport}
        onDownloadWorkbook={downloadWorkbook}
        onOpenRuleAssets={vi.fn()}
      />,
    )

    expect(screen.getByText('发现重大网络风险')).toBeVisible()
    await user.click(screen.getByText('解析覆盖与技术诊断'))
    expect(screen.getByText('22/26 · 84.6%')).toBeVisible()
    expect(screen.getAllByText('WGE1/0/15').length).toBeGreaterThan(0)
    expect(screen.getByText(/2 项信号与主故障相关/)).toBeVisible()
    expect(screen.getByText(/3 个连续周期 · 新增 4 · 持续 2 · 已恢复 4/)).toBeVisible()
    expect(screen.getByText(/命令数由 44 增至 54/)).toBeVisible()
    expect(screen.getByText('关闭已跟踪故障')).toBeVisible()
    expect(screen.getByText('状态改善')).toBeVisible()
    expect(screen.getByText('goal-acceptance:q2-q3')).toBeVisible()
    expect(screen.getByText(/未覆盖领域不能据此判定健康/)).toBeVisible()
    await user.click(screen.getByText('下载巡检报告'))
    await user.click(screen.getByText('下载巡检明细 Excel'))
    expect(downloadReport).toHaveBeenCalledOnce()
    expect(downloadWorkbook).toHaveBeenCalledOnce()

    const expanders = screen.getAllByRole('button', { name: 'Expand row' })
    await user.click(expanders[0])
    expect(await screen.findByText('snapshot-1')).toBeVisible()
    expect(screen.getByText('contract-1')).toBeVisible()
    expect(screen.getByText('rule-1')).toBeVisible()
    expect(screen.getByText('analysis-1')).toBeVisible()
  })

  it('never presents validation-only samples as healthy devices', () => {
    render(<OfflinePatrolResultPanel purpose="rule_asset_source" report={{ ...report, risks: [],
      assessment: { ...report.assessment, code: 'rule_replay_only', conclusion_allowed: false, status: '仅用于规则验证' } }}
      loading={false} isMobile={false} onRetry={vi.fn()} onDownloadReport={vi.fn()} onDownloadWorkbook={vi.fn()}
      onOpenRuleAssets={vi.fn()} onLoadComparisonEvidence={vi.fn()} />)
    expect(screen.getByRole('button', { name: /下载验证报告/ })).toBeVisible()
    expect(screen.queryByText('分析期风险')).not.toBeInTheDocument()
    expect(screen.queryByText(/未发现.*风险/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /下载巡检报告/ })).not.toBeInTheDocument()
  })

  it('does not turn incomplete zero-risk data green', () => {
    render(<OfflinePatrolResultPanel report={{ ...report, risks: [],
      assessment: { ...report.assessment, conclusion_allowed: false }, data_quality: { status: 'incomplete' } }}
      loading={false} isMobile={false} onRetry={vi.fn()} onDownloadReport={vi.fn()} onDownloadWorkbook={vi.fn()}
      onOpenRuleAssets={vi.fn()} onLoadComparisonEvidence={vi.fn()} />)
    expect(screen.getByText('尚不能形成完整风险结论，请查看覆盖缺口和证据')).toBeVisible()
    expect(screen.queryByText('本次已覆盖范围内未发现风险')).not.toBeInTheDocument()
  })

  it('formats composite entity keys without exposing JSON punctuation', () => {
    expect(formatOfflineEntityKey('["WGE1/0/15"]')).toBe('WGE1/0/15')
    expect(formatOfflineEntityKey('plain-interface')).toBe('plain-interface')
  })
})
