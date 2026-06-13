import type { AnalysisJobResponse, AnalyzeRequest, ConnectionTestResponse, RecentReport, ReportResponse } from './types'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers ?? {}),
    },
  })

  if (!response.ok) {
    let message = `请求失败 HTTP ${response.status}`
    try {
      const data = await response.json()
      message = typeof data.detail === 'string' ? data.detail : message
    } catch {
      message = response.statusText || message
    }
    throw new Error(message)
  }

  return response.json() as Promise<T>
}

export function testGitHub(token: string): Promise<ConnectionTestResponse> {
  return request('/api/github/test', {
    method: 'POST',
    body: JSON.stringify({ token: token || null }),
  })
}

export function testAi(baseUrl: string, apiKey: string, model: string): Promise<ConnectionTestResponse> {
  return request('/api/ai/test', {
    method: 'POST',
    body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, model }),
  })
}

export function analyzeRepo(payload: AnalyzeRequest): Promise<ReportResponse> {
  return request('/api/analyze', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function startAnalyzeJob(payload: AnalyzeRequest): Promise<AnalysisJobResponse> {
  return request('/api/analyze/jobs', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getAnalyzeJob(jobId: string): Promise<AnalysisJobResponse> {
  return request(`/api/analyze/jobs/${jobId}`)
}

export function listReports(): Promise<RecentReport[]> {
  return request('/api/reports')
}

export function getReport(id: number): Promise<ReportResponse> {
  return request(`/api/reports/${id}`)
}
