import React from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { businessHref } from '../apps/appRoutes'

/** Canonicalize old bookmarks without losing evidence query strings or hashes. */
export default function BusinessRoute({
  children,
}: {
  children: React.ReactNode
}) {
  const location = useLocation()
  const href = location.pathname + location.search + location.hash
  const target = businessHref(href)
  return target !== href ? <Navigate to={target} replace /> : <>{children}</>
}
