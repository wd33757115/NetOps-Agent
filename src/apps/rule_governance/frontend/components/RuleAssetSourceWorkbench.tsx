import React from 'react'
import { useCanConfigureNetOps } from '@netops/context/AuthContext'
import { ruleAssetSourceApi } from '@netops/services/api'
import { OfflineFileJobWorkbench as Workbench, type OfflineWorkbenchOptions } from '@netops/components/offline/OfflineJobWorkbench'
import OfflinePatrolResultPanel from '@netops/components/devices/OfflinePatrolResultPanel'
import OfflineRuleAssetPipeline from './devices/OfflineRuleAssetPipeline'
import CheckItemCatalogWorkspace from './skills/parser/CheckItemCatalogWorkspace'

const SourceClassification: React.FC<{ jobId: string }> = ({ jobId }) => (
  <CheckItemCatalogWorkspace initialOfflineJobId={jobId} />
)

export const RuleAssetSourceWorkbench: React.FC<OfflineWorkbenchOptions> = (props) => {
  const canManageRules = useCanConfigureNetOps()
  return <Workbench {...props} workspace={{
    purpose: 'rule_asset_source', api: ruleAssetSourceApi, canManageRules,
    ResultPanel: OfflinePatrolResultPanel,
    ProductionPanel: SourceClassification, PipelinePanel: OfflineRuleAssetPipeline,
  }} />
}

export default RuleAssetSourceWorkbench
