import React from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { businessHref, workbenchEnabled } from '../apps/appRoutes'
import DeviceOperationsWorkspace from '../components/devices/DeviceOperationsWorkspace'

export default function DevicesPage() {
  const location = useLocation()
  const href = location.pathname + location.search + location.hash
  const target = businessHref(href)
  if (location.pathname === '/devices') {
    if (!workbenchEnabled) return <DeviceOperationsWorkspace mode="patrol" />
    if (target !== href) return <Navigate to={target} replace />
    return <Navigate to={'/apps/patrol' + location.search + location.hash} replace />
  }
  return target !== href ? <Navigate to={target} replace /> : <DeviceOperationsWorkspace />
}
