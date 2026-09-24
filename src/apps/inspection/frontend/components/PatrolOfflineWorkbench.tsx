import React from 'react'
import InspectionDetailPage from '../InspectionDetailPage'
import { useCanConfigureNetOps } from '@netops/context/AuthContext'
import { offlinePatrolApi } from '@netops/services/api'
import { createPatrolReanalysis } from '@netops/services/patrolRuleSource'
import { OfflineFileJobWorkbench as Workbench, type OfflineWorkbenchOptions } from '@netops/components/offline/OfflineJobWorkbench'
import OfflinePatrolResultPanel from '@netops/components/devices/OfflinePatrolResultPanel'
import PatrolReportDialog from '@netops/components/devices/PatrolReportDialog'
import OfflineCurrentAuthorityPanel from '@netops/components/devices/OfflineCurrentAuthorityPanel'

export const PatrolOfflineWorkbench: React.FC<OfflineWorkbenchOptions> = (props) => {
  const canManageRules = useCanConfigureNetOps()
  return <Workbench {...props} workspace={{
    purpose: 'offline_patrol', api: offlinePatrolApi, canManageRules,
    DetailSurface: InspectionDetailPage,
    reanalyze: createPatrolReanalysis, ResultPanel: OfflinePatrolResultPanel,
    ReportDialog: PatrolReportDialog, CurrentAuthorityPanel: OfflineCurrentAuthorityPanel,
  }} />
}

export default PatrolOfflineWorkbench
