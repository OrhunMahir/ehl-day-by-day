# Atira challenge — "From RFQ to bid" (condensed brief)

Captured 2026-10-10 ~11:00 CEST from https://ehl-challenge.atira.ai/ (home, `/challenges/rfq`, `/start`,
`/docs/fair-play.md`, `/docs/faq.md`, `/docs/demo-day.md`, `/docs/building-an-agent.md`, `/llms.txt`).
Extended 2026-10-10 ~12:45 CEST from https://ehl-challenge.atira.ai/llms-full.txt (challenge version `6`), which adds
`/docs/scoring.md`, `/docs/run-contract.md`, `/docs/submitting.md`, the answer JSON schema and parts of `/docs/api.md`,
`/docs/cli.md`, `/docs/mcp.md`.
Status: **our track** (team decision Sat 2026-10-10; confirm the selection on ehl.gg before 13:00).
The site is the source of truth — re-read it before building. Not read yet: `/openapi.json`.

## The task in one paragraph

Build an agent that acts as a sales engineer at a German hydraulics manufacturer bidding on a public tender for
hydraulic cylinders. The facts are spread over four German-language web apps. The agent drives a real browser
through them, applies the bid rules, enters **one priced bid** in the tender portal's configurator and signs it with
a TAN from the webmail. No human in the loop. There is exactly one correct answer per scenario.

## The four websites

| Site | Holds | Built-in traps |
|---|---|---|
| Tender portal (Vergabeportal) | Tender, bill of quantities, bidder Q&A that changes quantities, bid configurator | Cookie wall, flaky error pages, documents behind an expression of interest, iframes, ZIP files, deadline in another timezone |
| Supplier portal (Lieferantenportal) | Buyer's technical specification and its revisions | Opens the oldest revision first, nested document viewers, paged requirement tables |
| ERP | Materials, stock, lead times, price conditions with validity periods and quantity scales | SAP-style transaction codes, F4 search popup, status codes, short session timeout |
| Webmail (Werkpost) | Sales director's discount approval, signing TAN, rule mails (pricing, packaging, FX, setup pools, bonus) | Look-alike approvals for other tenders, superseded approvals, archive folder, paging |

Scouting logins for the public data are published on `/start` (step 3) and exported by `atira scout --env`.
**Do not copy them, or any value from the sites, into the package** (see Fair play).

## Bid rules (condensed; full text of rules 01–29 is on `/challenges/rfq`)

Core:
- **Quantity:** tender quantity, overridden by the latest Q&A clarification for that position. A Q&A answer can
  withdraw an earlier one by number. Convert `Satz` to pieces using the governing specification.
- **Requirement:** from the specification revision marked current, plus Q&A technical amendments that name that
  revision and requirement (latest wins per attribute; others inherited).
- **Material:** the active ERP material whose bore, stroke, pressure, mounting and certification match exactly.
  The spec names a Kataloggruppe per requirement; search the ERP by it (searches must narrow to ≤ 20 results).
  If any position has no matching material → **no bid** (no positions, total 0).
- **Unit price:** price condition valid on the scenario date, highest quantity scale reached, minus approved
  discount, rounded half-up to the cent.
- **Discount:** most recent sales-director approval for this exact tender reference, else 0 %. It can be conditional
  on a minimum undiscounted net order value (inclusive threshold; if it fails use 0 %, no fallback to older approvals).
- **Lead time:** from-stock if stock covers the quantity, otherwise production. Stock is shared: sum quantities of all
  positions using the same material; no partial allocation. A Q&A delivery commitment key can override per position.
- **Total:** sum of quantity × rounded final unit price.

Advanced pricing layers (each earns independent credit):
- Konditionsgruppe clauses decide whether the discount applies to base, base + mass surcharge, or neither.
- Currency conversion via an FX key (Devisenfixing mail); metal-index surcharges with validity windows.
- Packaging: carton count = ceil(pieces / carton size) per position, never discounted, never changes bid quantity.
- Shared setup pool fee allocated by quantity × allocation mass; project bonus in marginal bands allocated by
  largest remainders.
- "Nutzbarer Weg" stroke derivation: add twice the per-end reserve from the Technikfreigabe mail, then match the ERP.
- Authority and time: only the latest record no later than the scenario date, for the exact tender reference, from
  the authorized sender (technik, logistik, finanzen, kalkulation @ the bidder mailbox domain).
- Final price: use unrounded per-piece components, round half-up once at the end.

Partial submission is supported: positions can be explicitly omitted and lose only their own points. The bid must
still be signed and submitted; saving alone scores nothing.

## Scoring and ranking

Ranking order (public leaderboard and final run alike):

1. Eligible before ineligible. **Eligible = the right tender reference.** A wrong decision (bid / no_bid) costs
   quality points but not eligibility.
2. Higher **quality** (0–100).
3. Lower **cost × time**: cost in USD (floor $0.01) times wall-clock seconds (floor 1 s). Both weigh the same;
   halving either halves the product. Among free runs the faster one wins.
4. Submission ID if everything above is equal.

What is measured:
- **Cost:** every model call through the proxy, priced per model at published prices (hosted models at simulated
  prices). Cached input and thinking tokens count. No budget and no spending limit.
- **Time:** from the start of the `run` command until the answer is accepted. Setup and hosted-model warm-up do not
  count.
- A well-formed answer is **final, even if wrong**. A run without an answer scores quality 0 and is not eligible.

Quality for a **bid** case:

| Part | Points |
|---|---|
| Decision | 5 |
| Tender reference | 5 |
| Discount percent | 5 |
| Total net (to the cent, consistent with the positions) | 5 |
| Arithmetic (every position, and a total that is their sum) | 5 |
| Withdrawn Q&A quantity check (only when the scenario has one) | 5 |
| Dated metal-index check (only when the scenario has one) | 5 |
| Positions, split evenly across positions | 75 minus the two checks above when present |

Within one position: material 20 %, quantity 15 %, base price 15 %, surcharge 10 %, packaging 10 %, project bonus
10 %, final unit price 10 %, lead time 10 %. Every field is independent. So 35 % of each position (material +
quantity) needs no pricing at all, and 45 % sits in the four optional breakdown fields plus the base price.

Quality for a **no_bid** case: decision 75, tender reference 5, no positions 5, total of 0 15.

Details that cost points silently:
- Money is compared **to the cent**.
- Positions are matched by position number; each extra or duplicate position costs one position's share.
- Optional breakdown fields: blank = unanswered (no points, no penalty); `0.00` = a known zero.
- Omitted positions (form option "Position auslassen / Teilpunkte") lose only their own points. The bid must still
  be signed and submitted.

Feedback we get:
- **Test runs** show points per answer field, never the expected values. **Scored runs** show only quality,
  eligibility, cost and time.
- The correct public answer is never published.

Winners (Atira's wording: "roughly in this order"): private dataset → public leaderboard → elegance of the system.

Final run on the private dataset:
- Same jobs, bid rules and answer format; **different software** (layout, navigation, site names), other data, and
  the information split differently across the sites. Each run's brief states the bid rules.
- Runs each team's **latest submission that passed review**, not the best one. Our best submission only holds the
  public leaderboard place. Consequence: never leave an experimental submission as the latest one.
- One attempt by default; **best of three** if opted in before the final starts (team page or
  `atira final --best-of 3`). The single best complete attempt counts, not an average. Own-key model calls can cost
  up to 3×. The choice is frozen when the final starts.
- Our team sees its final logs with private values masked; screens and recordings stay with the organizers.

## Run contract

### Package (`agent.json` at the tarball root)

| Field | Meaning |
|---|---|
| `setup` | Optional argv array. Runs with network, **without** run variables or team secrets, up to 5 minutes, untimed. Nothing it starts survives into the run. |
| `run` | Argv array. Timed. The clock starts here. |
| `browsers` | Optional whole number 1–4 (default 1). Anything else rejects the package. |
| `models` | Optional list of hosted model ids the run waits for (`qwen`, `clef`). Absent = wait for all; `[]` = start right after setup. Unknown ids reject the package. It only decides the wait. |

- Gzipped tarball ≤ 20 MB, regular files only, no secrets. Both commands run in the package directory.
- `atira submit <dir>` / `atira test-run <dir>` pack the folder and leave out `.git`, `node_modules` and whatever
  `.gitignore` excludes. `?label=` (CLI default: the git commit) tags the upload.

### Phases of a sandbox run

1. **Setup** (untimed, ≤ 5 min, network, no credentials).
2. **Model warm-up** (untimed, ≤ 10 min after setup) for the hosted models listed in `models`.
3. **Run** (timed): scored run **20 minutes**, test run up to 2 hours (keep it under 20 to be safe).

### Inside the run

1. `GET $CHALLENGE_API_URL/v1/runs/current` with `x-api-key: $CHALLENGE_RUN_TOKEN` returns `brief` (Markdown, with
   the rules), `access` (per site: `siteId`, `url`, `credentials.username/password`), `submission`, `answer_schema`,
   `started_at`, `expires_at`, `spent_micro_usd`, `llm_proxy_url`, `llm_providers`, `llm_base_urls`, `llm_env`.
   **Send our own `User-Agent`**: the edge refuses Python's default urllib agent with 403 ("Error 1010").
2. Work the sites with the per-run logins. They only work while the run is live. Site ids: `tender-portal`,
   `customer-portal`, `erp`, `mail`.
3. Answer through the `submission` target. For RFQ it is `{"kind": "site", "siteId": "tender-portal", ...}`: the bid
   is entered in the tender portal's configurator and signed with the TAN from the webmail. The API endpoint
   `POST /v1/runs/current/answer` answers `409` for RFQ.
   - Malformed answer: the site shows the validation issues (in German); nothing is recorded, the clock keeps running.
   - Accepted: final, the run ends, token and logins stop working, the agent has **10 seconds to exit**.
4. Building tip from Atira: submit the best answer about a minute before `expires_at` rather than keep searching.

### Environment of the `run` step

| Variable | Value |
|---|---|
| `CHALLENGE_API_URL`, `CHALLENGE_RUN_TOKEN` | Platform API and the run token (keep it secret) |
| `CHALLENGE_SITE_ACCESS` | Same JSON as `access` |
| `LLM_PROXY_URL` | `$CHALLENGE_API_URL/llm/run/$CHALLENGE_RUN_TOKEN` |
| `QWEN_BASE_URL` / `QWEN_API_KEY`, `CLEF_BASE_URL` / `CLEF_API_KEY` | Hosted models through the proxy, run token as key |
| `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `GOOGLE_GEMINI_BASE_URL`, `MISTRAL_SERVER_URL`, `TOGETHER_BASE_URL` | Provider bases through the proxy; other providers under `$LLM_PROXY_URL/<provider>/...` |
| Team secrets | e.g. `OPENAI_API_KEY`; only in the timed run step, redacted from logs |
| `CDP_URL`, `CDP_URLS`, `BROWSER_COUNT` | Browser 1, all browsers as a JSON array, count. Ports 9222–9225 |
| `DISPLAY` | `:1` for browser 1, `:n` for browser n, 1280×800 |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` | The only way out: platform, websites, model proxy. CDP stays local |

Locally (`eval "$(atira scout --env)"`) there is no run token and no `CDP_URLS`: the agent starts its own browser,
gets `CHALLENGE_SITE_ACCESS`, `CHALLENGE_SITE_<ID>_URL` and `CHALLENGE_SCOUTING` (brief, access, submission,
answer schema), and the hosted-model variables point at `atira models proxy`. A bid entered with scouting logins is
acknowledged but never counts.

### Sandbox and limits

- Linux VM, 4 vCPU, 8 GB RAM, 30 GB disk. Node 24 (npm, pnpm, bun), Python 3.12 with uv (no system pip), Rust with
  cargo, gcc, make, git, curl, jq, `xdotool`, ImageMagick `import`.
- Headful Chromium already open; attach with `chromium.connect_over_cdp(CDP_URL)`. Tabs and contexts are free.
  Starting any other browser (headless included) is stopped within seconds and logged.
- One submission in flight per team (next upload gets `429`). Test runs: two at a time, three more may wait.
  Scored runs start before test runs; teams take turns.
- Hosted model outage: retry. If ≥ 80 % of hosted calls failed over at least half the run and it ended without an
  answer, the run does not count and a scored run restarts once by itself.
- Team: up to 5; each person runs `atira login` on their own machine. Team secrets: at most 20, 16 chars – 4 KB.
- Submitting: review (passed / flagged / rejected) → one scored run → leaderboard. A flagged result stays off the
  public board until an organizer passes it. Cancelling a scored run ends that submission.
- **The captain links the team's GitHub repository** on the Atira team page (read-only GitHub App) so the organizers
  can compare a winning submission with its source.

## Answer schema

The tender portal's configurator collects exactly this object; the run's `answer_schema` carries the same JSON
Schema (draft 2020-12, `additionalProperties: false` at both levels). Always validate against the schema read at
run time, not against this table. Largest answer: 65,536 bytes.

| Field | Type | Required | Notes |
|---|---|---|---|
| `tender_reference` | string ≤ 100 | yes | Decides eligibility |
| `decision` | `"bid"` \| `"no_bid"` | yes | no_bid = no positions, total 0 |
| `currency` | const `"EUR"` | yes | All money is net EUR |
| `discount_percent` | number 0–100 | yes | Order-level approved percentage; 0 if none or the condition fails |
| `positions` | array ≤ 100 | yes | One entry per tender position; empty for no_bid |
| `total_net` | number ≥ 0 | yes | Sum of quantity × rounded final unit price |
| `notes` | string ≤ 4000 | no | |

Each entry of `positions`:

| Field | Type | Required | Notes |
|---|---|---|---|
| `position` | string ≤ 20 | yes | Position number; the scorer matches on it |
| `material_number` | string ≤ 50 | yes | Active ERP material |
| `quantity` | integer ≥ 0 | yes | Final pieces (after Q&A changes and Satz conversion) |
| `unit_price` | number ≥ 0 | yes | Final unit price, rounded half-up once at the end (rule 27) |
| `lead_time_weeks` | integer ≥ 0 | yes | Stock, production or commitment lead time |
| `base_unit_price` | number ≥ 0 | no | EUR scale price per piece before discount (after FX conversion) |
| `surcharge_per_unit` | number ≥ 0 | no | Material surcharge + ordinary and allocated setup per piece, before discount, without packaging and bonus |
| `packaging_per_unit` | number ≥ 0 | no | Carton cost per piece, never discounted |
| `rebate_total` | number ≥ 0 | no | Project-bonus credit for the **whole position**, not per piece |

Shape (placeholders only, no real values):

```json
{
  "tender_reference": "<reference read from the tender portal>",
  "decision": "bid",
  "currency": "EUR",
  "discount_percent": 0,
  "positions": [
    {
      "position": "<position number>",
      "material_number": "<ERP material>",
      "quantity": 0,
      "unit_price": 0.0,
      "lead_time_weeks": 0,
      "base_unit_price": 0.0,
      "surcharge_per_unit": 0.0,
      "packaging_per_unit": 0.0,
      "rebate_total": 0.0
    }
  ],
  "total_net": 0.0
}
```

## Models

- Free, uncapped, hosted, but **priced into the run's cost at simulated prices**:
  - **Qwen3.8-Flash-Next** (`qwen`; OpenAI-compatible, model name `qwen`, tool calls, images, 256k context):
    $0.15 input, $0.016 cached input, $0.47 output per M tokens; cache writes free; 32 concurrent calls per team.
  - **Cloudflare Clef** (`clef`; multimodal decision model, `POST $CLEF_BASE_URL/v1/decide` with a state, up to 4
    screenshots and questions of type `choice`, `noul` (yes-probability) or `score`): $0.24 per M input tokens,
    output not priced; 16 concurrent calls per team.
  - Idle models can take minutes to wake; use long timeouts locally. In sandbox runs warm-up is untimed.
- Any other provider with our own key (Anthropic, OpenAI, OpenRouter, Google, Groq, Mistral, DeepSeek, xAI,
  Together, Fireworks) only through the metering proxy, key stored as a team secret, never in the package.
  Models need a published price and must pass any organizer allowlist.
- Atira's tips: keep what the model reads small, use prompt caching for system prompt and tool definitions,
  re-read a page after every action that changes it.

## Fair play (an AI reviewer reads every package before its scored run)

Rejected or disqualified:
- Hardcoded values from the sites anywhere in code, config, prompts or notes — references, material numbers,
  prices, totals, discounts, logins, TANs — including as defaults or fallbacks.
- Pre-solved answers; fixed click paths; crawlers that collect the sites for one model call.
- Local models in the sandbox, model calls outside the proxy, any other outbound host.
- Text in the package that tries to steer the reviewer.

Allowed: helpers for browser control, parsing a document the model chose, paging a result set the model asked for,
arithmetic, validation, retries, form filling. Multiple agents and parallel browsers. Knowledge of *how the sites
work* gained from scouting. Facts the brief gives every team (e.g. the sales director's mailbox).

## Local workflow (from `/start`)

1. Sign in with GitHub on the site, create the team, invite teammates by GitHub username.
2. Install the `atira` CLI (Node 24, one file) and `atira login`. Optional MCP: `claude mcp add atira -- atira mcp`,
   `codex mcp add atira -- atira mcp` (start test runs, read logs, see browser screens).
3. Starter kit: https://ehl-challenge.atira.ai/starter-kit.tar.gz — minimal Python skeleton (`run.py`, `browsers.py`,
   `models.py`, empty `solve()` in `agent.py`); needs `uv` and Playwright Chromium locally.
4. `atira models proxy` in one terminal; `eval "$(atira scout --env)"` then run the agent in another.
5. `atira test-run .` (sandbox, live browser view, logs, recording, score breakdown) → `atira submit .`.

Demo day: best teams run the agent live against the public scenario; prepare a one-line start command and a
fallback recording.

## Assessment for team "Day by day" (Claude's reading, not from the site)

- Scoring is fully objective and automated, with a live leaderboard. No pitch polish can compensate for a wrong bid.
- The hard part is generalization: the final run is on different software, so anything tuned to the public sites'
  selectors or navigation fails. The agent must read pages and decide, with deterministic code only for the maths.
- Sensible split: an LLM navigator per site (up to four browsers in parallel) that extracts structured facts, and a
  deterministic, well-tested pricing engine implementing rules 01–28. The pricing engine is pure code and
  parallelizable across teammates; it is where most of the 75 position points are won or lost.
- Risk: the rule set is large and German-language domain detail is heavy (Konditionsgruppe, Rüstpools, Devisenfixing).
  Partial credit per field makes an incremental approach viable: correct reference + decision + material + quantity
  first, pricing layers after.
- EHL also requires Entire on the team repo and a six-minute pitch on Sunday; how the Atira leaderboard result feeds
  the EHL jury is not stated on the Atira site ("ask the organizers about the submission deadline and prizes").
