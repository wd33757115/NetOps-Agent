// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import { ApiError, resolveApiError } from '../utils/apiError'
import { appMessage } from '../utils/appMessage'

/** Django BFF 前缀：开发环境走 Vite 代理，生产环境由 Django 同源提供。 */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

export const TOKEN_KEY = 'access_token'
export const REFRESH_TOKEN_KEY = 'refresh_token'
export const USER_KEY = 'auth_user'

export interface BffEnvelope<T = unknown> {
  success: boolean
  data: T
  error: string | null
  code?: string | null
  request_id?: string | null
}

export interface AuthSession {
  access: string
  refresh: string
  role?: string
  thread_id?: string
  session_id?: string
  user: {
    id: number
    username: string
    email: string
    role: string
  }
}

export function isBffEnvelope(value: unknown): value is BffEnvelope {
  return (
    !!value &&
    typeof value === 'object' &&
    'success' in value &&
    'data' in value &&
    'error' in value
  )
}

export function saveAccessToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function getAccessToken(): string | null {
  return localStorage.getItem(TOKEN_KEY) || localStorage.getItem('access')
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

export function saveAuthSession(session: AuthSession) {
  saveAccessToken(session.access)
  localStorage.setItem(REFRESH_TOKEN_KEY, session.refresh)
  localStorage.setItem('refresh_token', session.refresh)
  localStorage.setItem(USER_KEY, JSON.stringify(session.user))
}

export function clearAuthStorage() {
  for (const key of Object.keys(localStorage)) {
    if (key.startsWith('official-document:')) localStorage.removeItem(key)
  }
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem('access')
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  localStorage.removeItem('refresh_token')
  localStorage.removeItem(USER_KEY)
}

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120000,
  headers: {
    'Content-Type': 'application/json'
  }
})

let refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const refresh = getRefreshToken()
  if (!refresh) return null
  if (!refreshPromise) {
    refreshPromise = axios
      .post<BffEnvelope<AuthSession>>(`${API_BASE_URL}/auth/refresh/`, { refresh })
      .then((res) => {
        const body = res.data
        if (isBffEnvelope(body) && body.success && body.data?.access) {
          saveAuthSession(body.data)
          return body.data.access
        }
        return null
      })
      .catch(() => null)
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = getAccessToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

api.interceptors.response.use(
  (response) => {
    if (response.config.responseType === 'blob') {
      return response
    }
    if (isBffEnvelope(response.data)) {
      if (!response.data.success) {
        return Promise.reject(
          resolveApiError(response.data, response.data.error || 'Request failed', response.status),
        )
      }
      response.data = response.data.data
    }
    return response
  },
  async (error: AxiosError<BffEnvelope | Blob | string | { error?: string; detail?: string }>) => {
    const status = error.response?.status
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean }
    let payload = error.response?.data
    if (original?.responseType === 'blob' && payload instanceof Blob) {
      try {
        const text = await payload.text()
        payload = JSON.parse(text) as BffEnvelope
        error.response!.data = payload
      } catch {
        /* 保留原始 Blob */
      }
    }
    const apiError =
      typeof payload === 'string' && payload.trimStart().startsWith('<')
        ? new ApiError('接口不存在或服务未启动', { status })
        : resolveApiError(payload, error.message, status)
    const serverMessage = apiError.message

    if (status === 401 && original && !original._retry && !original.url?.includes('/auth/login')) {
      original._retry = true
      const newToken = await refreshAccessToken()
      if (newToken) {
        original.headers.Authorization = `Bearer ${newToken}`
        return api(original)
      }
      clearAuthStorage()
      if (!window.location.pathname.startsWith('/login')) {
        appMessage.error('登录已过期，请重新登录')
        window.location.href = '/login'
      }
    } else if (status === 401) {
      clearAuthStorage()
    } else if (status === 403) {
      appMessage.error('没有权限执行此操作')
    } else if (status && status >= 500) {
      const suffix = apiError.requestId ? ` (request_id: ${apiError.requestId})` : ''
      appMessage.error(`服务器错误: ${serverMessage}${suffix}`)
    }

    return Promise.reject(apiError)
  }
)

export { ApiError }

/** WebSocket 连接地址：统一经 Django（8001）代理，携带 JWT 供 BFF 鉴权。 */
export function getChatWebSocketUrl(threadId?: string): string {
  const token = getAccessToken()
  const wsBase = import.meta.env.VITE_WS_BASE_URL
  if (wsBase) {
    const url = new URL(wsBase, window.location.origin)
    if (threadId) {
      url.searchParams.set('thread_id', threadId)
    }
    if (token) {
      url.searchParams.set('token', token)
    }
    return url.toString()
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = new URL(`${protocol}//${window.location.host}/ws/v1/chat`)
  if (threadId) {
    url.searchParams.set('thread_id', threadId)
  }
  if (token) {
    url.searchParams.set('token', token)
  }
  return url.toString()
}
