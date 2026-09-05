import { appRoutes, documentsEnabled } from './appRoutes'

export const appRegistry = [
  {
    id: 'patrol',
    name: '网络巡检',
    route: appRoutes.patrol,
    symbol: 'radar',
    enabled: true,
  },
  {
    id: 'patrol-rules',
    name: '巡检规则生成',
    route: appRoutes.rules,
    symbol: 'rules',
    enabled: true,
  },
  {
    id: 'official-document',
    name: '公文写作',
    route: appRoutes.documents,
    symbol: 'document',
    enabled: documentsEnabled,
  },
] as const
export const findApp = (id: string) =>
  appRegistry.find((app) => app.id === id && app.enabled)
