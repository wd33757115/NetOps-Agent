import type { AuthUser } from '../services/api'
export const NETOPS_CONFIGURE = 'netops.configure'
export function canConfigureNetOps(user: Pick<AuthUser, 'role' | 'permissions' | 'app_ids'> | null | undefined): boolean {
  return Boolean(user?.app_ids?.includes('rule-governance'))
}
