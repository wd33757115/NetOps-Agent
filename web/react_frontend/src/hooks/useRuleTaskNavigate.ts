import { useCallback } from 'react'
import { useLocation, useNavigate, type NavigateOptions } from 'react-router-dom'
import { businessHref } from '../apps/appRoutes'

export function validPatrolReturn(value: string | null): string | null {
  if (!value || !/^\/apps\/patrol\?/.test(value)) return null
  return value
}

/** Keep the originating inspection through rule editing, without storing credentials or CLI. */
export function useRuleTaskNavigate() {
  const navigate = useNavigate()
  const location = useLocation()
  return useCallback((target: string, options?: NavigateOptions) => {
    const destination = businessHref(target)
    const origin = validPatrolReturn(new URLSearchParams(location.search).get('return_to'))
    if (origin && /^\/(apps\/patrol-rules|skills\/parser)([/?]|$)/.test(destination)) {
      const [pathname, query = ''] = destination.split('?')
      const params = new URLSearchParams(query)
      params.set('return_to', origin)
      navigate(`${pathname}?${params}`, options)
    } else navigate(destination, options)
  }, [location.search, navigate])
}
