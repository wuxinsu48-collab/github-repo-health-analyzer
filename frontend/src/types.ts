export interface ConnectionTestResponse {
  ok: boolean
  message: string
  details: Record<string, unknown>
}

export interface AiConfig {
  base_url: string
  api_key: string
  model: string
}

export interface AnalyzeRequest {
  repo_url: string
  github_token?: string
  ai?: AiConfig
}

export interface RuleScore {
  rule_score: number
  dimension_scores: Record<string, number>
  risk_flags: string[]
}

export interface AiAssessment {
  ai_score: number
  confidence: 'low' | 'medium' | 'high'
  summary: string
  strengths: string[]
  risks: string[]
  recommendations: string[]
  dimension_comments: Record<string, string>
  score_rationale?: string
}

export interface ReportPayload {
  evidence: {
    repo: {
      full_name: string
      html_url: string
      description: string
      stars: number
      forks: number
      watchers: number
      subscribers: number
      open_issues: number
      created_at: string
      updated_at: string
      pushed_at: string
      archived: boolean
      disabled: boolean
      fork: boolean
      license: { name?: string; spdx_id?: string } | null
      default_branch: string
      size: number
      topics: string[]
      homepage: string
    }
    languages: Record<string, number>
    community: Record<string, boolean>
    commits: Array<Record<string, unknown>>
    releases: Array<Record<string, unknown>>
    tree: string[]
    readme_excerpt: string
    config_summary: {
      has_ci: boolean
      has_tests: boolean
      has_docker: boolean
      manifests: string[]
    }
  }
  rule_score: RuleScore
  ai_assessment: AiAssessment | null
  ai_error: string | null
  final_score: number
}

export interface ReportResponse {
  id: number
  repo_full_name: string
  repo_url: string
  created_at: string
  final_score: number
  rule_score: number
  ai_score: number | null
  payload: ReportPayload
}

export interface RecentReport {
  id: number
  repo_full_name: string
  repo_url: string
  created_at: string
  final_score: number
  rule_score: number
  ai_score: number | null
}

