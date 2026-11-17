# 🌬️ BreatheEasy: Urban Air Quality Assistant

> **PromptWars Virtual, Main Challenge 06: Urban Air Quality Assistant**
> A GenAI assistant that helps families and communities get through severe urban air pollution with **specific actions they can take each day**, not just an AQI number.
>
> **Live demo:** <https://breatheeasy-aqi-assistant.onrender.com> (free tier: the first visit after a quiet spell can take ~1 minute to wake up)

---

## 1. Chosen vertical

**Urban air quality and family health (environment + public health).**

During winter smog, apps show "AQI 287" but leave the real questions unanswered:

- *Can my child walk to school at 7:30?*
- *When should I open the windows?*
- *Should Grandpa skip his evening walk? Which mask actually works?*
- *How do I warn my housing society?*

BreatheEasy answers these questions for **your** household, for **today**, hour by hour.

## 2. What it does

| Feature | What the user gets |
|---|---|
| 📍 **Location** | Search a city or use the browser's location. |
| 👨‍👩‍👧 **Household profile** | Nickname, age group, health considerations (asthma, heart/lung condition, pregnancy, allergies, outdoor work) and usual outdoor activities for up to 8 people, plus what the home has (purifier, N95 masks, AC). Stored **only in the browser**. |
| 📊 **Air right now + 24h forecast** | US AQI, PM2.5/PM10, trend, and an hour-by-hour forecast list. |
| 🕐 **Cleanest time to step out** | The cleanest 3-hour window in the next 24 hours. |
| 🧍 **Per-person plan** | Go / Caution / Avoid for each activity at its real time slot, mask advice (including *no masks for under-2s*) and health warnings. |
| 🏠 **Home actions** | When to ventilate, how to use the purifier and AC, what to avoid indoors (incense, sweeping…). |
| ✨ **AI briefing (Gemini)** | A short, warm daily briefing and a personal note for each family member, in **English or Hindi**. |
| 💬 **"Ask BreatheEasy"** | Free-text questions such as *"Can my son play cricket at 5 PM?"*, answered from the actual forecast. |
| 📣 **Community alert** | A ready-to-paste WhatsApp message (English/Hindi) for society and family groups, with one-tap share. |

## 3. Approach and logic

### Design principle: *rules decide, AI explains*

Health guidance must be **reliable and testable**, so the work is split in two:

```
 Open-Meteo forecast ──► Rule engine (deterministic) ──► Baseline plan ──► Gemini (explain & personalise) ──► UI
                               ▲  source of truth                │                    │
                               │                                 └──── fallback ◄─────┘ (if AI fails/disabled)
                        Household profile
```

1. **Data layer** (`app/air_client.py`): fetches hourly US AQI, PM2.5 and PM10 from the free **Open-Meteo Air Quality API** (CAMS model, no API key). Coordinates are rounded to a ~1 km grid, which shares the cache between neighbours and avoids sending exact home locations upstream. Responses are cached (TTL, LRU-bounded), and concurrent requests for the same area share **one** upstream call. Rate-limit and server errors are retried once.
   - **Browser fallback:** free hosts share one outbound IP, which can exhaust Open-Meteo's daily free quota. When that happens the server answers 502, and the browser fetches the same public forecast itself (coordinates rounded the same way) and resends it. The server accepts it only through a strict schema (numbers only, AQI 0–1000, at most 96 hours, local-time pattern, no extra fields) and parses it with the same function, so rules and AI work unchanged. The CSP `connect-src` allows only that one extra origin.
2. **Rule engine** (`app/rules.py`): the safety source of truth, based on US EPA AQI guidance for sensitive groups:
   - **Sensitivity score** per person: +1 for a sensitive age (infant, child, teen, senior), +1 for a medical risk (asthma, heart/lung condition, pregnancy), giving *normal / sensitive / high*.
   - **Thresholds**: "Go" up to AQI 100 (normal) or 50 (sensitive/high); "Caution" up to 150 / 100 / 75; above that, "Avoid".
   - **Activities**: fixed-time activities (school/office commute, outdoor work) are judged by the *worst* hour in their usual slot. Flexible activities (play, exercise, walk) get the *cleanest* hour in their allowed daytime slot.
   - **Masks**: N95/KN95 when the 12-hour peak exceeds the person's limit, child-size for kids, and **never** for infants under 2.
   - **Ventilation**: open windows for 15–20 minutes at the cleanest hour if it is ≤ AQI 100; otherwise keep them shut.
   - **Home actions** depend on severity and on what the home already has.
3. **GenAI layer** (`app/ai.py`, Google **Gemini**):
   - Receives a **compact, grounded JSON** of the baseline: city, AQI series, statuses and nicknames. It does **not** receive coordinates or the raw profile.
   - System instructions forbid contradicting the rule engine or inventing numbers, require medical caution, and treat all user text as *data, not instructions* (prompt-injection defence).
   - Uses **structured JSON output** with a schema, validates it with Pydantic, trims over-long fields and drops notes for unknown nicknames.
   - **Model fallback chain** (default `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite` → `gemini-3.8-flash`, configurable with `GEMINI_MODELS`). If a model is retired (404), rate-limited (429), overloaded (5xx) or slow, the next one is tried within one overall time budget, with a per-model cap. Lite models come first because rewriting already-computed data needs speed, not deep reasoning.
   - Stray markdown is stripped from responses, because the UI renders plain text.
   - If every model fails, the app falls back to the rule-based plan, so it **always works**.
4. **Community share** (`app/share.py`): deterministic English/Hindi alert text, so no AI call is needed.

## 4. How the solution works (flow)

1. The user picks a location and fills in the household (saved in `localStorage`).
2. `POST /api/plan` validates the input, fetches (or reuses cached) forecast data, builds the baseline, asks Gemini for the briefing, and returns baseline + AI briefing + share message.
3. The UI renders everything with `textContent` only (no `innerHTML`), so there is no XSS from AI or user text.
4. `POST /api/ask` sends a question; Gemini answers grounded in the same baseline. If it fails, the rule-based summary is returned instead.

### API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status and whether AI is enabled |
| GET | `/api/geocode?q=Delhi` | City search (Open-Meteo Geocoding) |
| POST | `/api/plan` | Daily plan: `{location, members[], home, language}` |
| POST | `/api/ask` | Q&A: plan body + `question` |

## 5. Run it locally

**Requirements:** Python 3.10+. Everything used is **free**: Open-Meteo needs no key, and the Gemini API key from Google AI Studio is free with no card.

```bash
git clone https://github.com/vamshimorlawar/breatheeasy-aqi-assistant.git
cd breatheeasy-aqi-assistant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # then paste your GEMINI_API_KEY (optional)
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. Without a Gemini key, the app still runs with the full rule-based plan, and the AI briefing shows as "turned off".

### Docker / hosting

The app ships as a small Docker image (`python:3.12-slim`, non-root user, no `.env` or tests inside). It is hosted on **Render's free tier** via [`render.yaml`](render.yaml): Render builds the `Dockerfile` from `main`, and `GEMINI_API_KEY` is entered in the Render dashboard (never in git).

```bash
docker build -t breatheeasy . && docker run -p 7860:7860 --env-file .env breatheeasy
```

### Tests and lint

```bash
pytest          # 106 tests: rule engine, data parsing, caching, AI guardrails, API, security
ruff check .    # lint, including bandit security rules (S)
```

## 6. Evaluation focus areas

| Area | What we did |
|---|---|
| **Problem alignment** | Daily, per-person, hour-specific actions for families, plus community alerts. Hindi support for Indian cities facing winter smog. |
| **Code quality** | Small single-purpose modules (`air_client`, `rules`, `ai`, `share`, `security`, `models`), type hints, Pydantic schemas, app factory with dependency injection, ruff lint and format. |
| **Security** | API key kept server-side in `.env` (git-ignored). Strict Pydantic validation (`extra="forbid"`, regex for nicknames and labels, size limits). Validation errors never reflect input back. Per-IP rate limiting with bounded memory. Strict CSP and security headers (`frame-ancestors 'none'`, `nosniff`, `no-referrer`). `no-store` on API responses. No `innerHTML`. Prompt-injection guardrails and output validation. No personal data stored on the server. Swagger UI disabled. |
| **Efficiency** | One LLM call per plan (structured JSON, fast "lite" models first, minimal thinking, capped tokens). Forecast cache with request coalescing. Compact prompt context. GZip. No frontend framework or build step; the whole repo is a few hundred KB. |
| **Testing** | 106 pytest tests covering AQI bands, sensitivity, thresholds, activity windows, infant mask safety, ventilation, caching and coordinate rounding, malformed upstream data, AI output validation, model fallback chain and timeouts, upstream retry, tampered browser-forecast payloads, API validation and XSS payloads, rate limiting and security headers. External services are mocked, so tests run offline. |
| **Accessibility** | Semantic HTML landmarks, skip link, labelled fieldsets, `aria-live` status and answers, focus management after actions, visible focus ring, 44 px touch targets, AQI colours **always paired with text**, forecast as a readable list rather than a picture-only chart, light and dark themes, reduced-motion support, and a Hindi output option. |

## 7. Assumptions

- **AQI scale:** the app uses the **US EPA AQI** returned by Open-Meteo, so values can differ from India's CPCB AQI shown in local apps. Guidance is based on the US EPA health categories.
- **Forecast source:** Open-Meteo uses CAMS model forecasts (~11 km resolution in Europe, ~40 km globally), not street-level sensors. It is suitable for daily planning, not for exact readings.
- **Activity time slots** are typical defaults: school commute 7–9 AM, office commute 8–10 AM, outdoor work 9 AM–6 PM, play 6 AM–7 PM, exercise and walks 5/6 AM–9 PM, all in local time.
- **Interface language:** the UI is in English. AI briefings, answers and community alerts are available in English and Hindi.
- **Gemini availability:** free-tier models can be congested at peak times, so a briefing may take 10–20 s or fall back to rule-based guidance. Safety advice never depends on the AI.
- **Browser-supplied forecast:** when the server's data quota is used up, the plan is built from forecast data the visitor's browser fetched. A visitor could edit that data, but it only changes their own plan, and it is schema- and range-checked before use.
- **Single instance:** caching and rate limiting are in memory, which is fine for one server. A multi-instance deployment would use Redis.
- **Not medical advice:** the app gives general precautions and always points users to a doctor for symptoms.

## 8. Project structure

```
app/
  main.py        FastAPI app factory, routes, error handling
  config.py      Settings from environment / .env
  models.py      Pydantic request/response schemas (validation)
  air_client.py  Open-Meteo client, parsing, TTL cache
  aqi.py         AQI bands
  rules.py       Deterministic health-guidance engine (source of truth)
  ai.py          Gemini advisor with guardrails and output validation
  share.py       Community alert messages (EN/HI)
  security.py    Rate limiter and security headers middleware
static/          Accessible HTML/CSS/JS front end (no build step)
tests/           pytest suite (offline, mocked externals)
```

## Credits

Air-quality and geocoding data: [Open-Meteo](https://open-meteo.com/) (CC BY 4.0, Copernicus CAMS). AI: Google Gemini. License: MIT.
