import { canConfigureNetOps } from './permissions'
import type { AuthUser } from '../services/api'
import { packagedApps } from './generated'

export const appRegistry = [
  ...packagedApps.map(app => ({ id: app.aliases[0] || app.id, name: app.name, route: app.route, symbol: app.icon,
    enabled: app.enabled, managedAppId: app.id, showOnDesktop: app.id !== 'assistant' })),
] as const
export const findApp = (id: string) =>
  appRegistry.find((app) => app.id === id && app.enabled)

export const canOpenApp = (id: string, user: AuthUser | null) => {
  const app = findApp(id)
  return Boolean(app && ('managedAppId' in app ? user?.app_ids?.includes(app.managedAppId) : (!('permission' in app) || canConfigureNetOps(user))))
}
