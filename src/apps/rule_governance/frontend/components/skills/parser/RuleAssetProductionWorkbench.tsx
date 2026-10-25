import React from 'react'
import { Button, Space } from 'antd'
import {
  AppstoreOutlined,
  UploadOutlined,
} from '@ant-design/icons'

import CheckItemCatalogWorkspace from './CheckItemCatalogWorkspace'
import RuleAssetSourceJobsWorkspace from './RuleAssetSourceJobsWorkspace'
import RuleAssetBundleWorkspace from './RuleAssetBundleWorkspace'
import OfflineCommandAssetUnits from '../../devices/OfflineCommandAssetUnits'
import RuleProductionTasks from './RuleProductionTasks'

export type RuleAssetWorkbenchView = 'tasks' | 'commands' | 'sources' | 'catalog' | 'policies'

interface RuleAssetProductionWorkbenchProps {
  view: RuleAssetWorkbenchView
  onViewChange: (view: RuleAssetWorkbenchView) => void
  showNavigation?: boolean
  embedded?: boolean
  initialSearch?: string
  focusAction?: string
  focusOfflineJobId?: string
  focusRuleSeedJobId?: string
  focusBundleId?: string
  focusCheckItemVersionId?: string
  focusChangeId?: string
  datasetIds?: string[]
}

const RuleAssetProductionWorkbench: React.FC<RuleAssetProductionWorkbenchProps> = ({
  view,
  showNavigation = true,
  onViewChange,
  focusAction,
  focusOfflineJobId,
  focusBundleId,
  focusCheckItemVersionId,
  focusChangeId,
  initialSearch,
}) => {
  // `commands` used to be a second top-level page for the very same catalog.
  // Keep accepting it in URLs/callers so old deep links still work, but always
  // send sample commands to the unified check-item workspace.
  const currentView = view === 'policies' || view === 'commands' ? 'catalog' : view
  const catalogSourceScope = focusAction === 'classify_check_items'
    || focusAction === 'open_check_item_catalog'
  // Parser/TextFSM is command-evidence work. Keep it reachable from the
  // unified catalog only for an explicitly selected sample action; it does
  // not reintroduce a third top-level workspace.
  const showSourceParserFlow = focusAction === 'parser'
    && Boolean(focusOfflineJobId)
  // The legacy release workspace is folded into this workbench.  It must
  // still reach the bundle list when callers have no single bundle to focus.
  const showBundleReview = focusAction === 'bundle-review' || focusAction === 'release'

  return (
    <Space className="parser-production-workspace" direction="vertical" size={16} style={{ width: '100%' }}>
      {showNavigation && <nav className="rule-asset-view-switch" aria-label="巡检规则资产">
        <Button type="text" className={currentView === 'tasks' ? 'is-active' : ''} onClick={() => onViewChange('tasks')}>制作任务</Button>
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
          className={currentView === 'sources' ? 'is-active' : ''}
          icon={<UploadOutlined />}
          onClick={() => onViewChange('sources')}
        >
          样本导入
        </Button>
      </nav>}

      {currentView === 'sources' ? (
        <RuleAssetSourceJobsWorkspace />
      ) : showSourceParserFlow && focusOfflineJobId ? (
        <OfflineCommandAssetUnits
          jobId={focusOfflineJobId}
          initialSearch={initialSearch}
          focusAction="parser"
        />
      ) : showBundleReview ? (
        <RuleAssetBundleWorkspace
          initialSearch={focusCheckItemVersionId || focusOfflineJobId}
          initialBundleId={focusBundleId}
          initialCheckItemVersionId={focusCheckItemVersionId}
        />
      ) : currentView === 'tasks' ? <RuleProductionTasks /> : (
        <CheckItemCatalogWorkspace
          focusChangeId={focusChangeId}
          initialOfflineJobId={catalogSourceScope ? focusOfflineJobId : undefined}
        />
      )}
    </Space>
  )
}

export default RuleAssetProductionWorkbench
