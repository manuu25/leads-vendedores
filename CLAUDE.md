# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A tool for capturing **leads de vendedores particulares (FSBO)** — private individuals selling property without an agency — from CustoJusto.pt. Built for a real estate consultant building a prospecting list.

It started as a single CLI script and grew into a small full-stack app: a multi-engine scraper, optional AI field-extraction, a FastAPI backend, and two interchangeable web UIs. Code, comments, and all user-facing text are in **Portuguese (pt-PT)** — keep that convention.

There is no test suite and no git repository. There is no README; this file is the entry point.

## Layout

Python backend (repo root):
- [custojusto_leads.py](custojusto_leads.py) — the original standalone CLI scraper, now **also imported as a library**. Owns the CustoJusto-specific knowledge and shared parsing helpers (`extract_jsonld`, `extract_next_data`, `clean_price`, the regexes) plus the `BASE`/`HEADERS` constants. Still runnable on its own (see CLI below).
- [sources/](sources/) — **multi-source framework**. Each site is an adapter exposing a `Source` (see [sources/base.py](sources/base.py)); the registry is in [sources/__init__.py](sources/__init__.py). Adapters (verified live 2026-07): `custojusto` (full, incl. phone), `olx` (reads listing cards `data-cy="l-card"` → title/price/locality; rentals filtered by `PRICE_FLOOR`; phone gated behind login), `imovirtual` (reads the listing's embedded JSON `searchAds.items` via a `collect` override → title/price/area/rooms/location/photos/`isPrivateOwner`; **agency-dominated, few private sellers**), `idealista` (via **Apify** — DataDome blocks direct scraping; opt-in, credit-limited; returns phone + photos but is **agency-dominated → ~0 FSBO**), `google` (discovery), `facebook` (**Facebook Marketplace via Apify** — the best FSBO source for Madeira: Marketplace is dominated by private sellers; opt-in, credit-limited). Shared portal detail parsing lives in [sources/_common.py](sources/_common.py) (`generic_parse_detail`); OLX/Imovirtual hide the phone, so `telefone` mostly comes from CustoJusto (Idealista/Apify has it but for agencies; Facebook has it only when the seller writes it in the listing text — otherwise contact is via the Marketplace link/Messenger).
- [discovery.py](discovery.py) — Google **discovery via a search API** (Serper.dev), used by the `google` source. Does not scrape Google SERPs directly.
- [scrapers.py](scrapers.py) — `Scraper` class: a fetch chain with fallback across three engines. Source-agnostic — reused for every adapter.
- [extractor.py](extractor.py) — optional AI field extraction via OpenRouter, used only to fill gaps.
- [app.py](app.py) — FastAPI backend that orchestrates a multi-source scrape and serves results.
- [templates/index.html](templates/index.html) — self-contained PicoCSS dashboard served by `app.py` at `/`.

Alternative frontend:
- [frontend/](frontend/) — an Astro static site (port 4321) that consumes the same FastAPI API. This is an **alternative** UI to `templates/index.html`, not an addition — both talk to the same `/api/*` endpoints. Pick one.

## Commands

```bash
# Install (repo root)
pip install -r requirements.txt
playwright install chromium        # required for the playwright engine

# Run the web app (backend + built-in dashboard) — open http://localhost:8000
uvicorn app:app --reload --port 8000

# Optional: run the Astro frontend instead of templates/index.html
cd frontend && npm install && npm run dev   # http://localhost:4321 (talks to :8000)

# Run the scraper standalone from the CLI (no server, no AI)
python custojusto_leads.py --regiao portugal --categoria moradias --max-paginas 3
python custojusto_leads.py --regiao lisboa --categoria apartamentos --com-contacto
python custojusto_leads.py --debug   # dumps debug_listagem.html / debug_detalhe.html to tune selectors
```

CLI output is `leads_<regiao>_<categoria>_<YYYYMMDD>.csv` (utf-8-sig) + `.xlsx`, deduped by `id` (override the prefix with `--out`). There is no test or lint command for either side.

## Configuration (`.env`)

Copy `.env.example` → `.env` (gitignored). `app.py` loads it via `python-dotenv` **before** reading any key. Keys stay server-side — `/api/status` only returns booleans, never the values.

- `CONTEXT_DEV_API_KEY` — enables the paid `context` engine. Without it, that engine is silently skipped (`Scraper.available_engines()`).
- `OPENROUTER_API_KEY` + `OPENROUTER_MODEL` — enables AI extraction (`extractor.is_configured()`).
- `SCRAPE_COUNTRY` — geolocation passed to context.dev (default `pt`).
- `SEARCH_API_KEY` (+ `SEARCH_COUNTRY`) — enables the `google` discovery source (Serper.dev).
- `APIFY_TOKEN` (+ `APIFY_IDEALISTA_*`, `APIFY_FB_*`) — enables the Apify-backed sources: `idealista` and `facebook` (Facebook Marketplace). `APIFY_FB_LOCATION` is a numeric FB Marketplace **location id** (default Funchal `110189845667755`), `APIFY_FB_MAX` caps items, `APIFY_FB_DETAILS=1` fetches description+photos.

The Astro frontend has its own `frontend/.env` with `PUBLIC_API_BASE` (default `http://localhost:8000`).

## Architecture

### Two-phase scrape (the core, unchanged since the CLI days)

Both the CLI (`custojusto_leads.scrape`) and the web app (`app.run_scrape`) perform the same two phases using the same functions from `custojusto_leads.py`:

1. **Listagem** (`build_listing_url` → `parse_listing`): walks paginated result pages. The particulares filter is `?f=p` (active unless profissionais are included); pagination is `?o=N`; venda adds a `-venda` suffix to the category. `parse_listing` collects every `<a>` whose path matches `AD_URL_RE` (`/{regiao}/imobiliario/{categoria}/{slug}-{id}`), skipping sponsored links (`utm_medium=spotlight`, `utm_source=li`). Pagination stops early when a page yields no new ads.

2. **Detalhe** (`parse_detail`, only when contact extraction is requested): visits each ad and merges three extraction sources **in priority order** — **JSON-LD** (`extract_jsonld`, most stable for price/name/locality), **`__NEXT_DATA__`** (`extract_next_data`, the Next.js payload; advertiser name/phone/type via regex over the flattened JSON), then **visible-text regex fallbacks** (`PRICE_RE`, `TIPOLOGIA_RE`, `AREA_RE`, `PHONE_RE`). This is a Next.js site, so the JSON blobs are the reliable data; the regexes are last-resort. Every field write is guarded by `not rec.get(...)` so higher-priority sources win.

Every record carries `fonte_url` + `data_recolha` for traceability (RGPD — see below). The CLI dedups by `id` (`exportar()`); the web app dedups by `(fonte, id)` and then by repeated `telefone` across sources, with a fixed column order (`COLUNAS` in `app.py`, which includes the multi-source fields `fonte` and `fotos`).

### Multi-source framework (`sources/`)

The web app is **multi-source**: it runs the same two-phase flow across several sites and merges the results. Each site is a `Source` adapter ([sources/base.py](sources/base.py)) registered in [sources/__init__.py](sources/__init__.py). Two adapter shapes:

- **Portal sources** (`custojusto`, `olx`, `imovirtual`, `idealista`) provide `build_listing_url` + `parse_listing` + `parse_detail`. The generic page loop (`collect_pages`) and the generic detail parser (`generic_parse_detail` in `sources/_common.py`, JSON-LD → `__NEXT_DATA__` → text → photos) do the heavy lifting, so a new portal is mostly a URL pattern + a listing-link regex.
- **Non-portal sources** provide a `collect(params, fetch, log)` override: `google` uses `discovery.py` to find ad URLs and **routes each to the owning portal adapter** via `sources.route_url` (by domain); `idealista` and `facebook` call **Apify** actors (`run-sync-get-dataset-items`) and map the returned dataset — no HTML fetching.

`Source.gate()` disables a source at runtime (missing key, ToS opt-out). `Source.needs_detail=False` tells `run_scrape` to skip the per-ad detail phase because the listing already yields complete records (Imovirtual JSON, Idealista/Apify) — saves fetches. `Source.verified=False` marks adapters whose mapping is **best-effort and must be confirmed against live output** (Idealista/Apify, google, facebook). As of 2026-07, `custojusto`/`olx`/`imovirtual` are verified against live data. Non-portal sources use a `collect(params, fetch, log)` override instead of `build_listing_url`/`parse_listing`: Imovirtual (JSON), Idealista (Apify), Google (discovery), Facebook (Playwright login). `run_scrape` in `app.py` iterates the selected sources, honoring `gate()` and `needs_detail`, and logging the verified/needs-tuning state.

Config for Apify/Idealista: `APIFY_TOKEN`, `APIFY_IDEALISTA_ACTOR` (`username~actor`, currently `lukass~idealista-scraper`), `APIFY_IDEALISTA_MAX` (cost cap), optional `APIFY_IDEALISTA_INPUT` (JSON, to override the input for a different actor). The adapter calls `run-sync-get-dataset-items` once in **structured mode** (`country`/`operation`/`propertyType`/`district=regiao`) — lukass's `startUrl` mode fails deterministically (proxy 595 ECONNRESET at Idealista's OAuth), so the `/com-particular/` private-seller URL filter is unusable with this actor. It maps the `lukass` output (`price`/`size`/`rooms`/`contacts.phone1`→phone/`photos`, locality from the title's "… in X"), classifies any listing with a `contacts.commercialName` as `profissional`, and filters those out when `so_particulares`. **Reality check (verified live, Funchal): Idealista is agency-dominated — 12/12 were agencies, 0 FSBO.** So Idealista/Apify yields ~0 private sellers (same as Imovirtual); it returns the phone but mostly for agencies. `district="Madeira"` wrongly matches mainland "Madeirã"; use a municipality (`Funchal`). Set `APIFY_DUMP=<path>` to dump raw items. Cost guardrails: `maxItems` + `endPage` capped; live testing so far cost <$0.10.

**Adding a source:** create `sources/<site>.py` with a module-level `SOURCE = Source(...)` (and `DOMAINS = (...)` if you want Google discovery to route to it), then add the module to `_MODULES` in `sources/__init__.py` and a checkbox in the dashboard. Reuse `generic_parse_detail` unless the site needs custom detail logic.

### The fetch chain (`scrapers.py`) — how HTML is obtained

`custojusto_leads.py`'s own `get()` is plain `requests` and is used only in CLI mode. The web app fetches through `Scraper`, which tries engines **in order until one returns HTML**:

1. `requests` — fast, but usually gets 403 on this Next.js site.
2. `playwright` — real local headless Chromium; bypasses anti-bot, slower. The browser is opened once and reused across requests, so **always use `Scraper` as a context manager** (`with Scraper(...) as s:`) — `close()` tears down the browser.
3. `context` — the hosted context.dev API (paid, ~1 credit/request, most robust). Skipped if no key.

The user picks the order/subset per request via the UI (`engines` param). `fetch()` never lets a failing engine break the chain — it catches, logs to stderr, and falls through to the next.

### AI extraction (`extractor.py`) — gap-filler only

Deterministic extraction (JSON-LD + `__NEXT_DATA__` + regex) is always the first source because it's free and reliable for price/locality. When AI is enabled *and* a record is still missing `telefone` or `anunciante`, `app.run_scrape` sends the ad's visible text to OpenRouter (`extract_with_llm`) and merges the result with `merge_llm` — which, like everything else, only writes into empty fields. The model is instructed to return strict JSON and never invent values.

### Backend (`app.py`)

FastAPI. `run_scrape` is synchronous and CustoJusto-facing, so it runs in a worker thread (`asyncio.to_thread`) to avoid blocking the event loop. The last scrape is held in memory (`_LAST`, guarded by a lock) purely so the export buttons work — it is **not a database**. Routes: `GET /` (dashboard), `GET /api/status` (which keys are set), `POST /api/scrape`, `GET /api/export?fmt=csv|xlsx`. CORS is open to the Astro dev ports (4321/4322).

## Conventions to preserve when editing

- **Selectors are brittle by nature.** When extraction breaks, run the CLI with `--debug` first to capture live HTML, then adjust `AD_URL_RE`, the JSON key heuristics in `parse_detail`, or the text regexes. Don't guess at site structure — inspect the dumped HTML.
- **Politeness is intentional, not incidental.** `polite_sleep()` / `polite_sleep_between()` add random 1.5–3.5s pauses; the CLI `get()` retries with backoff on 403/429; `HEADERS` mimic a normal browser. Preserve this rhythm — do not add concurrency, remove sleeps, or attempt to bypass anti-bot/captcha protections. (Getting blocked is the intended signal to fall through to a stronger engine, not to hammer harder.)
- **Secrets never reach the browser.** context.dev / OpenRouter keys come only from env vars and are used server-side. Keep `/api/status` returning booleans only, and never embed a key in the frontend or in `custojusto_leads.py`.
- **RGPD/GDPR.** The tool collects personal data (names, phone numbers) of EU individuals. The `fonte_url` + `data_recolha` columns exist for legal compliance and must remain. The module docstring in `custojusto_leads.py` documents the legal posture; keep it in sync with any behavior change.
- **Per-site ToS, now multi-source.** Each source has its own Terms/robots. `idealista` and `facebook` scrape via **Apify** (offloads the ToS/anti-bot exposure to Apify's infrastructure and, crucially, does **not** risk the consultant's own Facebook account) — both are opt-in, gated on `APIFY_TOKEN`, and cost credits per run (keep the `maxItems`/`resultsLimit` caps). Personal data still falls under RGPD regardless of source.
- **FSBO reality (verified live 2026-07).** Where private sellers actually are: **CustoJusto** (FSBO + phone — the core), **OLX** (FSBO, but phone gated behind login), **Facebook Marketplace** (FSBO-rich, esp. Madeira; contact via Messenger link, phone only when in the text). Where they are **not**: **Imovirtual** and **Idealista** are agency-dominated (~0 FSBO) — don't spend Apify credits on Idealista expecting private sellers.
- **`custojusto_leads.py` is now a shared library.** `app.py` and `scrapers.py` import from it (`build_listing_url`, `parse_listing`, `parse_detail`, `BASE`, `HEADERS`). Changing those signatures or the record dict shape affects both the CLI and the web app — update both call sites.
