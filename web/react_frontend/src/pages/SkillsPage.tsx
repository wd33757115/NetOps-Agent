// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useCallback, useMemo, useState } from 'react'
import { Tabs } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'
import GrokShellLayout from '../components/Layout/GrokShellLayout'
import { useIsMobile } from '../hooks/useBreakpoint'
import SkillManageTab from '../components/skills/SkillManageTab'
import WorkflowPluginList from '../components/skills/WorkflowPluginList'
import TemplateMarketTab from '../components/skills/TemplateMarketTab'
import DevGuideTab from '../components/skills/DevGuideTab'
import WorkflowWizard, { WorkflowWizardInitial } from '../components/skills/WorkflowWizard'
import WorkflowWizardShell from '../components/skills/WorkflowWizardShell'

const SkillsPage: React.FC = () => {
  const isMobile = useIsMobile()
  const navigate = useNavigate()
  const { section } = useParams<{ section?: string }>()
  const activeTab = ['skills', 'workflows', 'market', 'guide'].includes(section || '')
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
      key: 'guide',
      label: '开发指南',
      children: <DevGuideTab />,
    },
  ]

  return (
    <GrokShellLayout
      title="Skills 和连接器"
      subtitle="管理 Skill、编排 Workflow 插件、模板市场与聊天触发"
    >
      <Tabs
        activeKey={activeTab}
        onChange={(key) => navigate(key === 'skills' ? '/skills' : `/skills/${key}`)}
        items={tabItems}
        size={isMobile ? 'small' : 'middle'}
        className="grok-skills-tabs"
      />
    </GrokShellLayout>
  )
}

export default SkillsPage
