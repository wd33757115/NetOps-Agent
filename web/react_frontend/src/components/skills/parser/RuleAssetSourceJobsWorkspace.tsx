import React from 'react'
import { useSearchParams } from 'react-router-dom'

import { OfflineFileJobWorkbench } from '../../devices/OfflinePatrolPanel'

/** Parser control owns these evidence jobs; they never execute a patrol task. */
const RuleAssetSourceJobsWorkspace: React.FC = () => {
  const [searchParams] = useSearchParams()
  const supplementCommand = searchParams.get('action') === 'supplement-samples'
    ? searchParams.get('command')?.trim()
    : ''
  return (
    <OfflineFileJobWorkbench
      purpose="rule_asset_source"
      initialJobType={supplementCommand ? 'parser_validation' : undefined}
      initialJobName={supplementCommand ? `${supplementCommand} 补充样本` : undefined}
    />
  )
}

export default RuleAssetSourceJobsWorkspace
