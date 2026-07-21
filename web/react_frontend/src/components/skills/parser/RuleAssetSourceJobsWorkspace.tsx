import React from 'react'

import { OfflineFileJobWorkbench } from '../../devices/OfflinePatrolPanel'

/** Parser control owns these evidence jobs; they never execute a patrol task. */
const RuleAssetSourceJobsWorkspace: React.FC = () => (
  <OfflineFileJobWorkbench purpose="rule_asset_source" />
)

export default RuleAssetSourceJobsWorkspace
