import React from 'react'
import { Button, Space } from 'antd'
import {
  AppstoreOutlined,
  DatabaseOutlined,
  UploadOutlined,
} from '@ant-design/icons'

import CheckItemCatalogWorkspace from './CheckItemCatalogWorkspace'
import RuleAssetSourceJobsWorkspace from './RuleAssetSourceJobsWorkspace'
import OfflineCommandAssetUnits from '../../devices/OfflineCommandAssetUnits'
import FocusedRuleAssetGeneration from './FocusedRuleAssetGeneration'
import RuleAssetBundleWorkspace from './RuleAssetBundleWorkspace'

export type RuleAssetWorkbenchView = 'commands' | 'sources' | 'catalog' | 'policies'

interface RuleAssetProductionWorkbenchProps {
  view: RuleAssetWorkbenchView
  onViewChange: (view: RuleAssetWorkbenchView) => void
  embedded?: boolean
  initialSearch?: string
  focusAction?: string
  focusOfflineJobId?: string
  focusRuleSeedJobId?: string
  focusBundleId?: string
  datasetIds?: string[]
}

const RuleAssetProductionWorkbench: React.FC<RuleAssetProductionWorkbenchProps> = ({
  view,
  onViewChange,
  initialSearch,
  focusAction,
  focusOfflineJobId,
  focusRuleSeedJobId,
  focusBundleId,
  datasetIds = [],
}) => {
  const currentView = view === 'policies' ? 'catalog' : view

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <nav className="rule-asset-view-switch" aria-label="巡检规则资产">
        <Button
          type="text"
          className={currentView === 'catalog' ? 'is-active' : ''}
          icon={<AppstoreOutlined />}
          onClick={() => onViewChange('catalog')}
        >
          检查项目录
        </Button>
        <Button
          type="text"
          className={currentView === 'commands' ? 'is-active' : ''}
          icon={<DatabaseOutlined />}
          onClick={() => onViewChange('commands')}
        >
          命令适配器
        </Button>
        <Button
          type="text"
          className={currentView === 'sources' ? 'is-active' : ''}
          icon={<UploadOutlined />}
          onClick={() => onViewChange('sources')}
        >
          样本任务
        </Button>
      </nav>

      {currentView === 'catalog' ? (
        <CheckItemCatalogWorkspace mode="check_items" />
      ) : currentView === 'sources' ? (
        <RuleAssetSourceJobsWorkspace />
      ) : focusAction === 'release' && focusBundleId ? (
        <RuleAssetBundleWorkspace
          initialSearch={focusOfflineJobId}
          initialBundleId={focusBundleId}
        />
      ) : focusOfflineJobId ? (
        <OfflineCommandAssetUnits
          jobId={focusOfflineJobId}
          initialSearch={initialSearch}
          focusAction={focusAction}
          generationContent={focusAction === 'rule-generation' ? (
            <FocusedRuleAssetGeneration
              seedJobId={focusRuleSeedJobId}
              datasetIds={datasetIds}
            />
          ) : undefined}
        />
      ) : (
        <CheckItemCatalogWorkspace mode="command_adapters" />
      )}
    </Space>
  )
}

export default RuleAssetProductionWorkbench
