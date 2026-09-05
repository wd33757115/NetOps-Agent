import type { AuthUser } from '../services/api'
export const NETOPS_CONFIGURE = 'netops.configure'
export function canConfigureNetOps(user: Pick<AuthUser, 'role' | 'permissions'> | null | undefined): boolean {
  return user?.role === 'admin' || Boolean(user?.permissions?.includes(NETOPS_CONFIGURE))
}
