# GitHub Repository Health MVP Design

## Goal

Build a local web app that analyzes a public GitHub repository URL, stores each analysis as a SQLite report, and presents repository health metrics with visual charts and an AI-assisted Chinese assessment.

## MVP Scope

- User enters a public GitHub repository URL.
- User may enter an optional GitHub token for higher rate limits.
- User enters AI `base_url`, `api_key`, and model name.
- GitHub and AI settings each have a test button that returns clear success or failure details.
- Backend fetches repository metadata, languages, community profile, commits, releases, and a shallow file tree from GitHub REST API.
- Backend calculates deterministic rule-based scores.
- Backend builds a bounded evidence bundle for AI.
- AI returns structured Chinese assessment JSON.
- Final score uses `rule_score * 0.7 + ai_score * 0.3`.
- SQLite stores report records but never stores GitHub token or AI API key.
- Frontend shows a single-page dashboard with visual metrics and a simple recent reports list.

## Architecture

- `backend/`: FastAPI application.
- `frontend/`: Vue 3 + Vite + TypeScript + ECharts application.
- `backend/app/services`: isolated GitHub, scoring, AI, and report persistence logic.
- `backend/app/models.py`: Pydantic request and response schemas.
- `backend/app/db.py`: SQLite schema and report repository.
- `backend/tests`: pytest tests for core logic and API behavior.

## Data Flow

1. Frontend posts repository URL, optional GitHub token, and optional AI config to `/api/analyze`.
2. Backend parses `owner/repo` from the URL.
3. Backend fetches GitHub data through HTTPX.
4. Backend computes rule-based dimension scores.
5. Backend builds an evidence bundle with metadata, scores, language distribution, community files, README excerpt, tree summary, and config hints.
6. If AI config is present, backend calls an OpenAI-compatible chat completion endpoint and parses JSON.
7. Backend stores report JSON in SQLite.
8. Frontend displays charts, scores, text assessment, risks, recommendations, and recent reports.

## AI Scoring Rules

AI acts as a constrained reviewer. It receives structured evidence and must return JSON with:

- `ai_score`
- `confidence`
- `summary`
- `strengths`
- `risks`
- `recommendations`
- `dimension_comments`

If AI returns a score more than 10 points away from `rule_score`, backend clamps it into `rule_score +/- 10` unless the model returns a non-empty `score_rationale`.

## Error Handling

- Invalid repository URL returns `400`.
- GitHub 404 returns a clear "repository not found or inaccessible" message.
- GitHub 401/403 returns authentication or rate-limit oriented details.
- AI test and analysis failures return clear provider, auth, model, network, or response-format details.
- If AI analysis fails during repository analysis, backend still returns and stores the rule-based report with an AI error message.

## Testing

- Unit tests cover GitHub URL parsing.
- Unit tests cover rule scoring and final score composition.
- Unit tests cover AI JSON parsing and score clamping.
- API tests cover report creation and report listing with mocked services.
- Frontend verification uses TypeScript build.

