# AI Agent Deep Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the transitional AI review block with a real read-only AI Agent scoring graph that explores the cloned repository with safe tools and produces evidence-backed scores.

**Architecture:** Keep the existing deterministic `deep_analysis.py` flow as the "basic rule score". Add a separate `agent_scoring.py` service that orchestrates project classification, rubric selection, AI-led tool exploration, evidence curation, per-dimension judges, critic review, calibration, aggregation, and final report generation. `main.py` calls the new service while the cloned repository is still available, stores the rich result in SQLite, and forwards node/tool progress events to the existing job drawer.

**Tech Stack:** FastAPI, Pydantic, HTTPX OpenAI-compatible chat completions, LangGraph, existing safe repo tools, Vue 3 + TypeScript.

---

### Task 1: Contract Tests

**Files:**
- Create: `backend/tests/test_agent_scoring.py`
- Modify: `backend/tests/test_api.py`

- [ ] **Step 1: Write the failing service test**

Create a temp repository with `package.json`, `README.md`, `src/App.tsx`, and `tests/app.test.ts`. Use a fake LLM client that returns JSON for classifier, rubric, explorer decisions, judges, critic, calibration, and final report. Assert the result has `score`, `dimensions`, `exploration_steps`, `evidence_pool`, `critic_review`, `calibrated_dimensions`, and `final_report`.

- [ ] **Step 2: Run the new test and verify it fails**

Run: `python -m pytest backend/tests/test_agent_scoring.py -q`
Expected: failure because `app.services.agent_scoring` does not exist.

- [ ] **Step 3: Write the failing API integration test**

Patch `app.main.run_agent_deep_scoring` with a fake async function, submit `/api/analyze` with an AI config, and assert the payload contains `agent_deep_score`, compatible `deep_ai_assessment`, and `ai_review` progress events.

- [ ] **Step 4: Run the targeted API test and verify it fails**

Run: `python -m pytest backend/tests/test_api.py::<new_test_name> -q`
Expected: failure because the pipeline still calls the old single-call AI review.

### Task 2: AI Agent Scoring Service

**Files:**
- Create: `backend/app/services/agent_scoring.py`
- Modify: `backend/app/models.py`

- [ ] **Step 1: Add Pydantic models**

Add `AgentToolCall`, `AgentObservation`, `AgentDimensionScore`, `AgentCriticReview`, and `AgentDeepScore` models. Include score fields bounded to `0..100`, dimension `score/max_score`, evidence lists, `exploration_steps`, `evidence_pool`, and `final_report`.

- [ ] **Step 2: Implement OpenAI-compatible LLM helper**

Implement an injectable async client method using existing `build_chat_completions_url`. It must request JSON, disable DeepSeek thinking for `deepseek-*`, strip fenced JSON, and map HTTP/auth/base_url/model failures to user-friendly `ValueError`s.

- [ ] **Step 3: Implement graph nodes**

Implement `repo_indexer`, `project_classifier`, `rubric_selector`, `evidence_explorer_loop`, `evidence_curator`, `dimension_judges`, `critic_review`, `score_calibrator`, `aggregate_score`, and `final_report`. The explorer loop calls only `list_dir_safe`, `read_file_safe`, `grep_safe`, and `find_files_safe`, max steps default 18, and stops on `finish`.

- [ ] **Step 4: Emit progress events**

Each node emits a node event; each tool call emits `running` and `completed/failed` tool events with `target` and `dimension`. Event ids must be stable enough for replacement in the drawer.

- [ ] **Step 5: Run service tests and make them pass**

Run: `python -m pytest backend/tests/test_agent_scoring.py -q`
Expected: all tests pass.

### Task 3: Pipeline Integration

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/db.py` if needed

- [ ] **Step 1: Keep clone alive for AI Agent**

Move cleanup so the local repo is deleted only after both basic rule scoring and optional AI Agent scoring finish.

- [ ] **Step 2: Replace old AI call**

Use `run_agent_deep_scoring(config=request.ai, repo_root=local_repo, index=local_index, github_evidence=evidence, core_report=deep_report, progress=agent_progress)`.

- [ ] **Step 3: Store compatibility and rich fields**

Add `agent_deep_score` to payload and map its headline score/summary/reviews into `deep_ai_assessment` so the existing UI and recent reports still work.

- [ ] **Step 4: Run API tests**

Run: `python -m pytest backend/tests/test_api.py -q`
Expected: all API tests pass.

### Task 4: Frontend Report UI

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/style.css`

- [ ] **Step 1: Add types**

Add interfaces for `AgentDeepScore`, dimension scores, observations, critic review, and calibrated dimensions. Add `agent_deep_score?: AgentDeepScore | null` to `ReportPayload`.

- [ ] **Step 2: Replace transitional AI tab copy**

Show the AI Agent score, project classification, selected rubric, exploration steps, evidence pool summary, dimension judges, critic review, calibrated scores, and final recommendations.

- [ ] **Step 3: Improve empty/error states**

If no AI config was provided, show that the AI Agent tab was skipped. If `ai_error` exists, show the user-facing error.

- [ ] **Step 4: Run frontend build**

Run: `npm run build` in `frontend`.
Expected: TypeScript build passes.

### Task 5: Final Verification

**Files:**
- No planned production edits.

- [ ] **Step 1: Run full backend tests**

Run: `python -m pytest` in `backend`.
Expected: all tests pass.

- [ ] **Step 2: Run frontend build**

Run: `npm run build` in `frontend`.
Expected: build succeeds.

- [ ] **Step 3: Secret scan**

Run: `rg "github_pat_|sk-[A-Za-z0-9]" -n . --glob '!frontend/node_modules/**' --glob '!backend/.pytest_cache/**' --glob '!frontend/dist/**'`.
Expected: no pasted credentials in source.

- [ ] **Step 4: Local smoke**

Use the running app at `http://127.0.0.1:5173/` to verify the AI Agent tab and drawer can display node/tool events. Real-key testing should be done through the UI fields rather than shell commands, so credentials do not land in command history.
