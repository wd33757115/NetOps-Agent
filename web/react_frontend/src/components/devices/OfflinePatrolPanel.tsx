/** Compatibility exports for historical imports; each workspace now owns its adapter. */
import React from 'react'
import type { OfflinePatrolJobPurpose } from '../../services/api'
import type { OfflineWorkbenchOptions } from '../offline/OfflineJobWorkbench'
import PatrolOfflineWorkbench from './PatrolOfflineWorkbench'
import RuleAssetSourceWorkbench from '../../../../../src/apps/rule_governance/frontend/components/RuleAssetSourceWorkbench'

export * from '../offline/OfflineJobWorkbench'
export type OfflineFileJobWorkbenchProps = OfflineWorkbenchOptions & { purpose: OfflinePatrolJobPurpose }
export const OfflineFileJobWorkbench: React.FC<OfflineFileJobWorkbenchProps> = ({ purpose, ...props }) => (
  purpose === 'rule_asset_source' ? <RuleAssetSourceWorkbench {...props} /> : <PatrolOfflineWorkbench {...props} />
)
export default PatrolOfflineWorkbench
