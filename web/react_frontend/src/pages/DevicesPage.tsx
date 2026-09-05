import React from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { businessHref } from '../apps/appRoutes'
import DeviceOperationsWorkspace from '../components/devices/DeviceOperationsWorkspace'

export default function DevicesPage() {
  const location = useLocation()
  const href = location.pathname + location.search + location.hash
  const target = businessHref(href)
  return target !== href ? <Navigate to={target} replace /> : <DeviceOperationsWorkspace />
}
