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

export type AnalysisJobStepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

export interface AnalysisJobStep {
  id: string
  label: string
  status: AnalysisJobStepStatus
  detail: string
}

export interface AnalysisJobResponse {
  job_id: string
  status: 'running' | 'completed' | 'failed'
  steps: AnalysisJobStep[]
  report_id: number | null
  report: ReportResponse | null
  error: string | null
}

export interface RuleScore {
  rule_score: number
  dimension_scores: Record<string, number>
  risk_flags: string[]
}

export interface AiAssessment {
  ai_score: number
  score_adjustment?: number
  confidence: 'low' | 'medium' | 'high'
  summary: string
  strengths: string[]
  risks: string[]
  recommendations: string[]
  dimension_comments: Record<string, string>
  score_rationale?: string
}

export interface EvidenceItem {
  label: string
  path?: string | null
  excerpt: string
}

export interface ScoreDimension {
  score: number
  max_score: number
  reason: string
  evidence: EvidenceItem[]
}

export interface CoreScore {
  score: number
  dimensions: Record<string, ScoreDimension>
  summary: string
  risk_flags: string[]
}

export interface SuitabilityScores {
  learning: number
  secondary_development: number
  production: number
  notes: Record<string, string>
}

export interface CommunityReference {
  stars: number
  forks: number
  open_issues: number
  watchers: number
  pushed_at?: string | null
  archived: boolean
  disabled: boolean
  default_branch?: string | null
  license_name?: string | null
  topics: string[]
  recent_commits: number
  releases: number
}

export interface DeepAiAssessment {
  score: number
  confidence: 'low' | 'medium' | 'high'
  summary: string
  dimension_reviews: Record<string, string>
  strengths: string[]
  risks: string[]
  recommendations: string[]
}

export interface LocalIndex {
  tree: string[]
  file_count: number
  directory_count: number
  total_bytes: number
  extension_counts: Record<string, number>
  source_files: string[]
  test_files: string[]
  documentation_files: string[]
  manifest_files: string[]
  ci_files: string[]
  config_files: string[]
  security_files: string[]
  snippets: EvidenceItem[]
  security_findings: EvidenceItem[]
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
  local_index: LocalIndex
  core_score: CoreScore
  suitability: SuitabilityScores
  community_reference: CommunityReference
  analysis_trace: string[]
  summary: string
  strengths: string[]
  risks: string[]
  recommendations: string[]
  github_warning?: string | null
  deep_ai_assessment: DeepAiAssessment | null
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
