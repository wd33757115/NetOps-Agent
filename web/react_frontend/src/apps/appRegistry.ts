import { canConfigureNetOps } from './permissions'
import type { AuthUser } from '../services/api'
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
    permission: 'netops.configure',
    name: '巡检规则工作台',
    route: appRoutes.rules,
    symbol: 'rules',
    enabled: true,
  },
  { id: 'device-configuration', name: '设备配置', route: '/admin/netops/devices', symbol: 'rules', enabled: true, permission: 'netops.configure' },
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

export const canOpenApp = (id: string, user: AuthUser | null) => {
  const app = findApp(id)
  return Boolean(app && (!('permission' in app) || canConfigureNetOps(user)))
}
