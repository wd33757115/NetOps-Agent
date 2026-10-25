import React from 'react'
import type { AppFrontend } from '@netops/apps/types'

const List = React.lazy(() => import('./pages/OfficialDocumentListPage'))
const Create = React.lazy(() => import('./pages/OfficialDocumentCreatePage'))
const Editor = React.lazy(() => import('./pages/OfficialDocumentEditorPage'))

export default {
  routes: [
    { path: '', component: List },
    { path: 'new', component: Create },
    { path: ':documentId', component: Editor },
  ],
} satisfies AppFrontend
