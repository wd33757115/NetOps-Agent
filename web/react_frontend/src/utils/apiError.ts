// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

/** 统一 API 错误解析（P2：code + request_id） */

export interface StructuredApiErrorBody {
  code?: string
  message?: string
  details?: Record<string, unknown>
}

export interface UpstreamErrorEnvelope {
  success?: false
  error?: StructuredApiErrorBody | string
  request_id?: string
  detail?: unknown
}

export class ApiError extends Error {
  code?: string
  requestId?: string
  details?: Record<string, unknown>
  status?: number

  constructor(
    message: string,
    options?: {
      code?: string
      requestId?: string
      details?: Record<string, unknown>
      status?: number
    },
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = options?.code
    this.requestId = options?.requestId
    this.details = options?.details
    this.status = options?.status
  }
}

function readStructuredError(errorField: unknown): StructuredApiErrorBody | null {
  if (!errorField || typeof errorField !== 'object' || Array.isArray(errorField)) {
    return null
  }
  const obj = errorField as Record<string, unknown>
  return {
    code: typeof obj.code === 'string' ? obj.code : undefined,
    message: typeof obj.message === 'string' ? obj.message : undefined,
    details:
      obj.details && typeof obj.details === 'object' && !Array.isArray(obj.details)
        ? (obj.details as Record<string, unknown>)
        : undefined,
  }
}

export function parseUpstreamErrorEnvelope(data: unknown): {
  message: string
  code?: string
  requestId?: string
  details?: Record<string, unknown>
} | null {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return null
  }
  const body = data as UpstreamErrorEnvelope
  const structured = readStructuredError(body.error)
  if (structured?.message || structured?.code) {
    return {
      message: structured.message || structured.code || '请求失败',
      code: structured.code,
      requestId: typeof body.request_id === 'string' ? body.request_id : undefined,
      details: structured.details,
    }
  }
  if (typeof body.error === 'string' && body.error) {
    return {
      message: body.error,
      requestId: typeof body.request_id === 'string' ? body.request_id : undefined,
    }
  }
  if (body.detail != null) {
    return { message: String(body.detail) }
  }
  return null
}

export function resolveApiError(
  payload: unknown,
  fallbackMessage: string,
  status?: number,
): ApiError {
  const upstreamFromData =
    payload && typeof payload === 'object' && !Array.isArray(payload)
      ? parseUpstreamErrorEnvelope((payload as Record<string, unknown>).data)
      : null
  const upstreamDirect = parseUpstreamErrorEnvelope(payload)

  const envelope = payload && typeof payload === 'object' && !Array.isArray(payload)
    ? (payload as Record<string, unknown>)
    : null

  const parsed = upstreamFromData || upstreamDirect
  const bffMessage =
    envelope && typeof envelope.error === 'string' ? envelope.error : undefined
  const bffCode = envelope && typeof envelope.code === 'string' ? envelope.code : undefined
  const bffRequestId =
    envelope && typeof envelope.request_id === 'string' ? envelope.request_id : undefined

  return new ApiError(parsed?.message || bffMessage || fallbackMessage, {
    code: parsed?.code || bffCode,
    requestId: parsed?.requestId || bffRequestId,
    details: parsed?.details,
    status,
  })
}
