# Recruiter Web Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a loopback-only Web console that starts the existing BOSS login flow and explicitly downloads one recruiter resume without exposing resume content.

**Architecture:** `web.runtime` owns serial background task state and delegates authentication and downloading to injected services. `web.app` owns aiohttp request validation and JSON responses; `web.assets` owns the accessible local UI. A new resume download service holds the platform-to-file workflow shared by Click and HTTP entry points.

**Tech Stack:** Python 3.10+, Click, aiohttp, existing AuthManager/recruiter adapters, vanilla HTML/CSS/JavaScript, pytest.

---

## File Structure

- Create `src/boss_agent_cli/commands/recruiter/resume_download_service.py`: reusable single-resume workflow and domain errors.
- Modify `src/boss_agent_cli/commands/recruiter/download_resume.py`: retain compliance and Click presentation; delegate to service.
- Create `src/boss_agent_cli/web/runtime.py`: task states, serialization and dependency-injected operations.
- Create `src/boss_agent_cli/web/app.py`: loopback app, origin/token guard, endpoints.
- Create `src/boss_agent_cli/web/assets.py`: accessible responsive console page.
- Create `src/boss_agent_cli/commands/web.py`: `boss web` CLI command.
- Modify `src/boss_agent_cli/commands/register.py`, `src/boss_agent_cli/commands/schema.py`, `pyproject.toml`: command registration, schema contract and `aiohttp` runtime dependency.
- Create `tests/test_resume_download_service.py`, `tests/test_web_runtime.py`, `tests/test_web_app.py`, `tests/test_web_command.py`: unit and HTTP coverage.
- Modify `tests/test_recruiter_download_resume.py`: preserve CLI-to-service integration behavior.

### Task 1: Extract the reusable download workflow

**Files:**
- Create: `src/boss_agent_cli/commands/recruiter/resume_download_service.py`
- Modify: `src/boss_agent_cli/commands/recruiter/download_resume.py`
- Test: `tests/test_resume_download_service.py`

- [ ] Write a failing service test that injects a recruiter platform and verifies `view_geek` receives all three IDs and returns only export metadata.
- [ ] Run `uv run pytest tests/test_resume_download_service.py -q`; expect import failure before implementation.
- [ ] Implement `ResumeDownloadService.download()` with injected platform factory, parser and exporter; map platform and export failures into typed, redacted domain errors.
- [ ] Refactor the Click command to retain literal `require_compliance_allowed(ctx, "recruiter-download-resume")`, options and JSON envelope while presenting service outcomes.
- [ ] Run `uv run pytest tests/test_resume_download_service.py tests/test_recruiter_download_resume.py -q`; expect all tests passing.

### Task 2: Implement task runtime and HTTP contract

**Files:**
- Create: `src/boss_agent_cli/web/runtime.py`
- Create: `src/boss_agent_cli/web/app.py`
- Test: `tests/test_web_runtime.py`
- Test: `tests/test_web_app.py`

- [ ] Write failing tests for login single-flight behavior, research-mode blocking, local token/origin rejection and metadata-only download responses.
- [ ] Run `uv run pytest tests/test_web_runtime.py tests/test_web_app.py -q`; expect import failure before implementation.
- [ ] Implement typed runtime task snapshots, background login execution, one download lock and an injected downloader. Never retain resume text in snapshots.
- [ ] Implement aiohttp routes for `/`, `/api/state`, `/api/login`, `/api/resume-download`; require same-origin `Origin` and launch token for POST routes.
- [ ] Run the two test modules; expect all tests passing.

### Task 3: Add the accessible local console and command

**Files:**
- Create: `src/boss_agent_cli/web/assets.py`
- Create: `src/boss_agent_cli/commands/web.py`
- Modify: `src/boss_agent_cli/commands/register.py`
- Modify: `src/boss_agent_cli/commands/schema.py`
- Modify: `pyproject.toml`
- Test: `tests/test_web_command.py`

- [ ] Write failing command tests for `boss web --help`, loopback defaults and no automatic browser launch without explicit flag.
- [ ] Run `uv run pytest tests/test_web_command.py -q`; expect import failure before implementation.
- [ ] Implement the responsive semantic form, status polling, disabled controls and metadata-only result view. Use no external assets and do not render candidate content.
- [ ] Implement `boss web`, defaulting host to `127.0.0.1`; register command/schema and add `aiohttp` as a direct project dependency.
- [ ] Run all Web tests and the recruiter download tests; expect all passing.

### Task 4: Verify behaviour and UI

**Files:**
- Test: `tests/test_web_runtime.py`
- Test: `tests/test_web_app.py`
- Test: `tests/test_web_command.py`

- [ ] Run `uv run pytest -q`, `uv run ruff check src tests` and `uv run mypy src/boss_agent_cli`.
- [ ] Start `uv run boss web --port 8765`, inspect the local page at desktop and mobile viewport widths, and verify no layout overflow or content overlap.
- [ ] Submit the login action with a fake injected authentication dependency in HTTP tests; confirm the real manual login action remains the only route to actual credentials.
- [ ] Commit relevant implementation, tests and documentation using a Chinese commit message of at most 50 characters.
