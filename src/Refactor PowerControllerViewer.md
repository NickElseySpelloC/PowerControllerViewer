# Refactor PowerControllerViewer

## Current pain points

**Thread synchronisation** is the biggest structural problem. The app uses a Flask dev/Gunicorn model where each worker process independently runs a background `StateLoaderWorker` thread, coordinated via a file lock and a metadata JSON file. That's a lot of machinery for what amounts to "one process owns the cache at a time." The state timestamps aren't even consistently covered by the lock, so races exist. Flask + Gunicorn + threading + file locks + per-process caches is genuinely hard to reason about correctly.

**Data/presentation coupling** in `views.py` (`~1000 LOC`) is significant. The `build_*_homepage()` functions reach into raw JSON, do arithmetic (energy totals, price calculations), mutate data structures in-place, and format times — all mixed together with the logic of what gets passed to the template. A schema change in PowerController would require surgery here.

**Per-request housekeeping** (stat every state file, check config timestamp) on every HTTP request adds overhead that will accumulate as devices scale.

**matplotlib chart generation** happening inside `_state_lock` is a meaningful bottleneck — potentially seconds of blocked state access per chart.

* * *

## Proposed redesign architecture

The FastAPI/uvicorn/WebSocket approach you're thinking of is the right call. Here's how I'd structure it:

### Server: FastAPI + uvicorn (single process)

Since the app is I/O-bound and state updates are infrequent, a single uvicorn process with async handlers eliminates the multi-process synchronisation problem entirely. No more file locks, no more per-process caches, no more Gunicorn worker races. One process owns the state; WebSocket push means clients update in real time without polling.

### Layer separation

```
┌─────────────────────────────────────────────────────────┐
│  Ingestion layer  (POST /api/submit)                    │
│  – Validate JSON schema → emit internal StateEvent      │
└──────────────────────────┬──────────────────────────────┘
                           │ asyncio event / queue
┌──────────────────────────▼──────────────────────────────┐
│  State store  (in-memory, single source of truth)       │
│  – Keyed by device name                                 │
│  – Stores raw parsed state + derived "view model"       │
│  – Notifies subscribers on change                       │
└──────────┬───────────────┬────────────────────┬─────────┘
           │               │                    │
┌──────────▼──────┐  ┌─────▼──────┐  ┌─────────▼───────┐
│  View model     │  │  WebSocket │  │  Chart service  │
│  builders       │  │  broadcast │  │  (async, not    │
│  (pure fns,     │  │  to all    │  │   under lock)   │
│  no I/O)        │  │  clients   │  └─────────────────┘
└──────────┬──────┘  └────────────┘
           │
┌──────────▼──────────────────────────────────────────────┐
│  Web layer  (FastAPI routes + Jinja2 templates)         │
│  – Initial page load: render from view model            │
│  – Subsequent updates: WebSocket push → JS DOM patch    │
└─────────────────────────────────────────────────────────┘
```

### View model builders as pure functions

Instead of `build_power_homepage()` reaching into raw state JSON, introduce a thin view model layer:

```
# data/view_models.py  (pure, no I/O, easily unit-testable)
def power_view_model(raw_state: dict) -> PowerViewModel:
    ...  # all formatting, arithmetic, field extraction here

def lighting_view_model(raw_state: dict) -> LightingViewModel:
    ...
```

The templates only ever receive typed view models. Changing what PowerController sends only means updating `power_view_model()`, not hunting through `views.py`.

### Chart generation

Move out of the state lock entirely. Generate charts as a background async task triggered by a state update, writing to disk. The template just references the static path. Or better: drop server-side matplotlib in favour of a lightweight JS chart (Chart.js) fed by the WebSocket — consistent with the design direction and removes a heavy dependency.

### Housekeeping

Replace per-request housekeeping with a periodic `asyncio` task (e.g. every 30s), so it doesn't add latency to HTTP requests.

* * *

## What stays the same

- The `/api/submit` ingest contract (so PowerController/LightingControl don't need to change)
- The multi-device concept and state file types
- The template/CSS design you're converging on

* * *

## Suggested file structure

```
src/
  main.py           – FastAPI app, startup, shutdown
  state_store.py    – In-memory state, subscriber notification
  ingest.py         – /api/submit handler + schema validation
  websocket.py      – WS connection manager + broadcast
  routes.py         – GET routes (thin, just renders template from view model)
  view_models/
    power.py
    lighting.py
    temp_probes.py
    metering.py
  charts.py         – async chart generation (or drop for JS charts)
  config.py         – config loading/validation
```

* * *

## Summary

The core rewrite is: **Flask → FastAPI, threads → asyncio, file locks → single-process in-memory state, build\_\*\_homepage() → pure view model functions.** That resolves the synchronisation issues structurally rather than patching them, and the view model split gives you the clean data/presentation boundary you're after.

Happy to start planning the implementation whenever you're ready.

> PS: One important point - the templates/temp\_probes.html page is specifically designed to render on an older model iPad Air (gen 2 I think) that doesn't support the latest iOS and so won't support some of the 'modern' css features like clamp(), hence the "fallback for older browsers" stuff in the css  

Good callout — I'll save that constraint now so it doesn't get lost when we start the implementation.

Noted and saved. When we get to the redesign I'll keep that page on a conservative CSS baseline — explicit sizing, no viewport math functions, and I'll preserve any existing fallback patterns rather than "cleaning them up."