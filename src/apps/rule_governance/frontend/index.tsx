import React from 'react'
import type { AppFrontend } from '@netops/apps/types'
const Rules = React.lazy(() => import('./pages/PatrolRulesAppPage'))
const Source = React.lazy(() => import('./pages/RuleAssetSourceJobDetailPage'))
export default { routes: [
  { path: '', component: Rules },
  { path: 'source-jobs/:jobId', component: Source },

] } satisfies AppFrontend
