import { useChatStore } from '../store/useChatStore'
// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { canConfigureNetOps } from '../apps/permissions'
import { useDesktopStore } from '../store/useDesktopStore'
import { authApi, AuthUser } from '../services/api'
import {
  REFRESH_TOKEN_KEY,
  TOKEN_KEY,
  USER_KEY,
  clearAuthStorage,
  getAccessToken,
  saveAuthSession,
  type AuthSession,
} from '../config/api'

interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  isAuthenticated: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refreshProfile: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  const refreshProfile = useCallback(async () => {
    const token = getAccessToken()
    if (!token) {
      useDesktopStore.getState().reset()
      useChatStore.getState().reset()
      setUser(null)
      return
    }
    try {
      const me = await authApi.me()
      setUser(me)
      localStorage.setItem(USER_KEY, JSON.stringify(me))
    } catch {
      clearAuthStorage()
      useDesktopStore.getState().reset()
      useChatStore.getState().reset()
      setUser(null)
    }
  }, [])

  useEffect(() => {
    const cached = localStorage.getItem(USER_KEY)
    if (cached) {
      try {
        setUser(JSON.parse(cached) as AuthUser)
      } catch {
        localStorage.removeItem(USER_KEY)
      }
    }
    refreshProfile().finally(() => setLoading(false))
  }, [refreshProfile])

  useEffect(() => {
    const onFocus = () => { void refreshProfile() }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [refreshProfile])

  const login = useCallback(async (username: string, password: string) => {
    const session = (await authApi.login(username, password)) as AuthSession & { user: AuthUser }
    useChatStore.getState().reset()
    useDesktopStore.getState().reset()
    saveAuthSession(session)
    setUser(session.user)
  }, [])

  const logout = useCallback(async () => {
    useDesktopStore.getState().reset()
      useChatStore.getState().reset()
    try {
      await authApi.logout()
    } catch {
      // ignore
    }
    clearAuthStorage()
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({
      user,
      loading,
      isAuthenticated: !!user && !!getAccessToken(),
      login,
      logout,
      refreshProfile,
    }),
    [user, loading, login, logout, refreshProfile]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return ctx
}

export { REFRESH_TOKEN_KEY, TOKEN_KEY, USER_KEY }

/** Presentation-only check, default-deny outside an authenticated provider. */
export function useCanConfigureNetOps(): boolean {
  return canConfigureNetOps(useContext(AuthContext)?.user)
}

export function useIsAdmin(): boolean {
  return useContext(AuthContext)?.user?.role === 'admin'
}
