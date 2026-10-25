import React from 'react'
import type { AppFrontend } from '@netops/apps/types'

const Prefixes = React.lazy(() => import('./pages/PrefixesPage'))
const PrefixDetail = React.lazy(() => import('./pages/PrefixDetailPage'))
const Zones = React.lazy(() => import('./pages/ZonesPage'))
const Systems = React.lazy(() => import('./pages/SystemsPage'))
const Hosts = React.lazy(() => import('./pages/HostsPage'))
const Circuits = React.lazy(() => import('./pages/CircuitsPage'))
const CircuitDetail = React.lazy(() => import('./pages/CircuitDetailPage'))
const Resilience = React.lazy(() => import('./pages/ResiliencePage'))
const Impact = React.lazy(() => import('./pages/ImpactPage'))
const Providers = React.lazy(() => import('./pages/ProvidersPage'))
const Import = React.lazy(() => import('./pages/ImportPage'))
const Changes = React.lazy(() => import('./pages/ChangesPage'))

export default {
  routes: [
    { path: '', component: Prefixes },
    { path: 'prefixes/:prefixId', component: PrefixDetail },
    { path: 'zones', component: Zones },
    { path: 'systems', component: Systems },
    { path: 'hosts', component: Hosts },
    { path: 'circuits', component: Circuits },
    { path: 'circuits/:circuitId', component: CircuitDetail },
    { path: 'resilience', component: Resilience },
    { path: 'impact', component: Impact },
    { path: 'providers', component: Providers },
    { path: 'import', component: Import },
    { path: 'changes', component: Changes },
  ],
} satisfies AppFrontend
