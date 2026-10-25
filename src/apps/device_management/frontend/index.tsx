import React from 'react'
import type { AppFrontend } from '@netops/apps/types'
const Devices = React.lazy(() => import('./DeviceInventoryWorkspace'))
export default { routes: [{ path: '', component: Devices }] } satisfies AppFrontend
