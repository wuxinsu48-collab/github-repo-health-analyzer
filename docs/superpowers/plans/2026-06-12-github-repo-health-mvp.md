# GitHub Repository Health MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local FastAPI + Vue 3 app that analyzes public GitHub repositories, stores report records in SQLite, and displays visual health metrics plus AI-assisted Chinese scoring.

**Architecture:** FastAPI owns GitHub collection, deterministic scoring, AI evidence construction, AI provider calls, and SQLite persistence. Vue 3 owns form state, connection tests, report history, charts, and dashboard presentation.

**Tech Stack:** Vue 3, Vite, TypeScript, ECharts, FastAPI, HTTPX, Pydantic, SQLite, pytest.

---

## File Structure

- `backend/app/main.py`: FastAPI routes and application factory.
- `backend/app/models.py`: Pydantic schemas shared by services and routes.
- `backend/app/db.py`: SQLite schema initialization and report repository.
- `backend/app/services/github.py`: GitHub REST client, URL parser, evidence fetcher.
- `backend/app/services/scoring.py`: deterministic score calculation and final score composition.
- `backend/app/services/ai.py`: OpenAI-compatible test call, evidence prompt, JSON parsing, score clamping.
- `backend/tests/test_scoring.py`: scoring behavior.
- `backend/tests/test_ai.py`: AI parsing and clamping behavior.
- `backend/tests/test_github.py`: repository URL parsing behavior.
- `backend/tests/test_api.py`: API behavior with mocked services.
- `frontend/src/App.vue`: single-page dashboard shell.
- `frontend/src/api.ts`: backend API client.
- `frontend/src/types.ts`: TypeScript response models.
- `frontend/src/main.ts`: Vue bootstrap.
- `frontend/src/style.css`: dashboard styling.

## Tasks

- [ ] Create backend package, dependencies, and tests.
- [ ] Implement URL parser, scoring, AI parsing, SQLite persistence, and FastAPI routes.
- [ ] Scaffold Vue 3 + Vite + TypeScript frontend.
- [ ] Implement connection forms, report creation, report history, ECharts charts, and score panels.
- [ ] Run pytest and frontend build.
- [ ] Start backend and frontend dev servers.

