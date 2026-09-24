import { Navigate, useLocation } from 'react-router-dom'
export default function DevicesPage() { const { search, hash } = useLocation(); return <Navigate to={'/apps/device-management' + search + hash} replace /> }
