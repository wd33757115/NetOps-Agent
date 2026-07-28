// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { Suspense, useCallback, useMemo, useState } from 'react'
import { Spin, Tabs } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'
import GrokShellLayout from '../components/layout/GrokShellLayout'
import { useIsMobile } from '../hooks/useBreakpoint'
import SkillManageTab from '../components/skills/SkillManageTab'
import WorkflowPluginList from '../components/skills/WorkflowPluginList'
import TemplateMarketTab from '../components/skills/TemplateMarketTab'
import DevGuideTab from '../components/skills/DevGuideTab'
import WorkflowWizard, { WorkflowWizardInitial } from '../components/skills/WorkflowWizard'
import WorkflowWizardShell from '../components/skills/WorkflowWizardShell'

const ParserControlTab = React.lazy(() => import('../components/skills/ParserControlTab'))

const workspaceFallback = (
  <div className="grok-page-loading is-compact" role="status" aria-label="工作区加载中">
    <Spin />
  </div>
)

const SkillsPage: React.FC = () => {
  const isMobile = useIsMobile()
  const navigate = useNavigate()
  const { section } = useParams<{ section?: string }>()
  const activeTab = ['skills', 'workflows', 'market', 'parser', 'guide'].includes(section || '')
    ? section!
    : 'skills'
  const [wizardOpen, setWizardOpen] = useState(false)
  const [wizardInitial, setWizardInitial] = useState<WorkflowWizardInitial | null>(null)

  const closeWizard = useCallback(() => {
    setWizardOpen(false)
    setWizardInitial(null)
  }, [])

  const openWizard = useCallback((initial?: WorkflowWizardInitial) => {
    setWizardInitial(initial || null)
    setWizardOpen(true)
  }, [])

  const workflowTabContent = useMemo(
    () => (
      <>
        <WorkflowPluginList
          onCreateWizard={() => openWizard()}
          onEditWizard={(initial) => openWizard(initial)}
        />
        <WorkflowWizardShell
          title={wizardInitial?.initialDsl ? 'Workflow 编辑向导' : 'Workflow 创建向导'}
          open={wizardOpen && activeTab === 'workflows'}
          onClose={closeWizard}
        >
          <WorkflowWizard
            open={wizardOpen && activeTab === 'workflows'}
            onClose={closeWizard}
            initial={wizardInitial}
            onSaved={() => navigate('/skills/workflows')}
          />
        </WorkflowWizardShell>
      </>
    ),
    [activeTab, closeWizard, navigate, openWizard, wizardInitial, wizardOpen],
  )

  const tabItems = [
    {
      key: 'skills',
      label: 'Skill 管理',
      children: <SkillManageTab />,
    },
    {
      key: 'workflows',
      label: 'Workflow 插件',
      children: workflowTabContent,
    },
    {
      key: 'market',
      label: '模板市场',
      children: <TemplateMarketTab />,
    },
    {
      key: 'parser',
      label: 'Parser 控制面',
      children: (
        <Suspense fallback={workspaceFallback}>
          <ParserControlTab />
        </Suspense>
      ),
    },
    {
      key: 'guide',
      label: '开发指南',
      children: <DevGuideTab />,
    },
  ]

  return (
    <GrokShellLayout
      title={activeTab === 'parser' ? '巡检规则资产' : 'Skills 和连接器'}
      subtitle={activeTab === 'parser'
        ? '从离线命令样本生成、验证、审批并发布巡检解析与事件规则'
        : '管理 Skill、编排 Workflow 插件、模板市场与聊天触发'}
    >
      {activeTab === 'parser' ? (
        <Suspense fallback={workspaceFallback}>
          <ParserControlTab />
        </Suspense>
      ) : (
        <Tabs
          activeKey={activeTab}
          onChange={(key) => navigate(key === 'skills' ? '/skills' : `/skills/${key}`)}
          items={tabItems}
          size={isMobile ? 'small' : 'middle'}
          className="grok-skills-tabs"
        />
      )}
    </GrokShellLayout>
  )
}

export default SkillsPage
