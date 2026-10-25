import { api } from '../config/api'
import type { DesktopLayout } from '../components/desktop/desktopLayout'

export const desktopApi = {
  get: async (signal?: AbortSignal): Promise<DesktopLayout> =>
    (await api.get('/auth/me/desktop-layout/', { signal })).data,
  save: async (
    layout: DesktopLayout,
    signal?: AbortSignal,
  ): Promise<DesktopLayout> =>
    (await api.put('/auth/me/desktop-layout/', layout, { signal })).data,
}
