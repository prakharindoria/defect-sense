# Handoff: DefectSense rebrand + voice/chat assistant

Target repo: `Quality-Control-Defect-Dectection-Agent` (FastAPI + React/Vite, already wired end-to-end — auth, `/ws/live`, REST). No integration is missing; two jobs remain.

## 1. Finish the FORGE → DefectSense rebrand

`apps/web/src/brand.ts` (new) already drafted — copy it in, then point every hardcoded string at it:
```ts
export const PRODUCT_NAME = "DefectSense";
export const PRODUCT_DESCRIPTION = "AI-powered manufacturing quality control defect detection agent";
export const PRODUCT_LINE = "Wheel Assembly QC";
```
Files still saying FORGE / using literals instead of `brand.ts`:
- `apps/web/src/App.tsx`, `Login.tsx` — reference implementations are in this design project's `patch/apps/web/src/` (already import from brand.ts); diff against the real `apps/web/src/App.tsx` / `Login.tsx` and port the same import + JSX changes. `Dashboard.tsx`/`Station.tsx` in `patch/` also show the pattern but the real versions already read `user.display_name` etc. — just swap literal "DefectSense"/header strings for the brand import where present.
- `apps/api/main.py` — FastAPI `title="FORGE"`, `version`, docstring "FORGE API", log line `"FORGE"` → `"DefectSense"`. Keep the *cost model* and domain logic untouched.
- `apps/web/index.html` — `<title>`.
- Leave alone (internal, not user-facing): cookie name `forge_refresh`, demo password `forge2026`, Python package `forge/` module path — renaming these touches auth plumbing for no visible benefit. Flag to the user if they want those renamed too.

## 2. New feature: voice assistant + chatbot

Visual spec built in `DefectSense Web App.dc.html` (this project) — floating circular button, bottom-right, every page (mount at `Shell` level in `App.tsx`, sibling to `<Nav>`/`<main>`, not per-page). Click opens a 380px panel: message thread (user bubbles dark/right, bot bubbles light/left), text input, and a mic button that toggles a "Listening…" pulse state. Matches the existing daylight-cleanroom palette (`--ink-black` bubbles/button, `--soft` bot bubbles, `--accent-molten` mic when live).

Suggested implementation shape:
- **`apps/web/src/components/Assistant.tsx`** (new) — floating button + panel, local state `{open, listening, messages}`. Speech-to-text via the browser `SpeechRecognition`/`webkitSpeechRecognition` API (mic button starts/stops it, transcript fills the input); no new dependency needed for STT. Optional TTS reply via `speechSynthesis.speak()`.
- **Backend**: add `POST /api/v1/assistant/chat` in a new `apps/api/routers/assistant.py`, guarded by `require_any("inspection:read", "inspection:read_own_station")` so a shop-floor user can ask about their own station's units. Request `{message, correlation_id?}`; reuse the existing `_llm_service()` singleton in `main.py` (`forge/infrastructure/llm/service.py`) rather than adding a second LLM client. Ground answers by pulling context from `state.storage`/`_VIEWS` (recent inspections, current unit) before calling the model — same provenance discipline the rest of the app uses (label the reply as LLM-generated, never silently authoritative over a verdict).
- Keep the assistant **read-only**: it explains and narrates, it does not submit inspections, change dispositions, or halt the line — those stay button-driven actions with their own permission checks.
- Role scoping: shop-floor sees only their station's unit in context; QA/Admin get the fuller feed — mirror the `inspection:read` vs `inspection:read_own_station` split already used elsewhere.

Everything else (auth, live feed, RBAC, cost model, agent graph) is already integrated and working — no wiring needed there.
