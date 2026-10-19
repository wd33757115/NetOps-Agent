import { lazy } from 'react'
import type { AppFrontend } from '@netops/apps/types'

// Routes under /apps/hello-demo. '' is the application's home page.
export default {
  routes: [{ path: '', component: lazy(() => import('./Page')) }],
} satisfies AppFrontend
