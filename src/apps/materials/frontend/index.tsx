import { lazy } from 'react'
import type { AppFrontend } from '@netops/apps/types'

export default {
  routes: [
    { path: '', component: lazy(() => import('./Page')) },
    { path: 'docsets/:id', component: lazy(() => import('./DocsetReader')) },
  ],
} satisfies AppFrontend
