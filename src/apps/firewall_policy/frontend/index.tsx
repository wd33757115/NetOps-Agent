import React from 'react'
import type { AppFrontend } from '@netops/apps/types'

export default {
  routes: [
    { path: '', component: React.lazy(() => import('./pages/FirewallPolicyPage')) },
    { path: 'block', component: React.lazy(() => import('./pages/IpBlockPage')) },
  ],
} satisfies AppFrontend
