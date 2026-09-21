import React from 'react'
import type { AppFrontend } from '@netops/apps/types'
const Inspection = React.lazy(() => import('./InspectionWorkspace'))
export default { routes: [{ path: '', component: Inspection }] } satisfies AppFrontend
