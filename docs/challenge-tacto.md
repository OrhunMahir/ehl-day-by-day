# tacto challenge — "Negotiation Arena" (condensed brief)

Captured 2026-10-10 ~11:20 CEST from https://arena.tacto.ai/ and
https://github.com/tacto-ai/negotiation-arena-kit (`README.md`, `brief/brief.md`, `brief/submission.md`,
`starter-kit/README.md`, first 14k chars).
Status: **one of three EHL tracks; not selected yet.** The repo is the source of truth — re-read it before building.
Not read yet: `brief/api.md`, `data/README.md`, the rest of `starter-kit/README.md` (arena-sim, MCP server).
The brief still has open placeholders: how Live Final score and jury combine for places 1–3, and when the arena
goes offline Saturday night (announced on Discord https://discord.gg/hPwFfbWRV).

## The task in one paragraph

We are the procurement team of Hollerau Pumpenwerke, a fictional German pump maker (scenario date 2026-09-30).
Build an **AI buyer agent** that reads the company's data, negotiates **by e-mail** with tacto's hosted supplier
bots over an HTTP API, and closes the cheapest total deal — or walks away. Practice all weekend; on Sunday 12:00 a
Live Final on hidden cases ranks every team.

## How a case works

- A case = a trigger (price-increase letter, expiring contract, RFQ), a case file (need, goal, rates, must-haves,
  our alternative) and 1–3 supplier bots. RFQ cases run 2–3 suppliers in parallel threads.
- Round 0: each supplier sends a quote e-mail with a quote PDF.
- Our message: subject, body, up to **3 term sheets** (alternative packages, a value for every negotiable field),
  cited data IDs, optional attachments.
- Supplier reply: e-mail from the role in charge, its standing term sheet, a status (`open`, `final_offer`, `agreed`,
  `walked_away`, `no_bid`), signals, and a check of every cited fact (`verified`, `contradicted`, `irrelevant`,
  `duplicate`). A wrong figure or unknown ID costs trust.
- Only we close: accept a supplier's standing term sheet, or walk away. **Only term sheets bind.** Fields we did not
  negotiate close on the supplier's standard terms from the quote PDF.
- Limits: 10 messages per thread by default; **60 seconds per message** counted from the supplier's reply (supplier
  replies can take up to a minute and do not count). Three timeouts in a row end the thread.
- Suppliers have hard limits (cost floor, boss approval for big moves, no concession without a move from us) and a
  personality: they react to tone, relationship, urgency and threats. True data is not automatically rewarded; the
  skill is finding what this supplier cares about (e.g. payment timing instead of price) from notes, letters, news.

## Data

- Data package v1 in the repo's `data/` (also `/v1/data/package.zip`): CSV + SQLite, `facts.json` with every citable
  ID, contracts, letters, e-mails, call notes, scorecards, three years of orders/invoices, should-cost estimates,
  market indices (real public series frozen at 2026-09-30). Some notes are noise.
- Citable IDs look like `po:4500010001-10`, `idx:PPI-C2451@2026-08`, `sc:70110@2026Q3`, `ctr:4600070110#3.1`.
- **Sunday 08:00: second data release** (news, supplier letters, a memo). Hidden cases may cite it; rerun
  `uv run arena data`.

## Score

- Only the accepted term sheet scores, valued as **total cost C in EUR** over a fixed need and horizon: price,
  uncovered need at fallback price, payment interest, freight, lead time, quality, index clause, and (for
  `stabilise` / `secure_supply` goals) a penalty for leaving the supplier below a sustainable margin.
- **Case score = (C_open − C_deal) / (C_open − C_best)**, clipped to 0…1. C_best is fixed per case and hidden.
- No deal scores our alternative (0 if none); some cases are best left without a deal. A broken must-have scores 0.
- Every case states a category goal (`stabilise`, `secure_supply`, `quality_first`, `leverage`, `routine`) that
  sets the rates. `arena_kit.total_cost` computes C.

## Live Final (Sun 12:00 to ~12:58)

- 5 themed waves, **8-minute hard cap each**, all teams at once, one attempt per case, up to 10 parallel sessions
  per key. Wave score 0–100; max 500 points.
- The agent runs on **our own machine with our own LLM keys**, started before 12:00 and left alone:
  `uv run arena run --set final --agent llm --parallel 10`. No human in the loop.
- The agent must **not** read `/v1/events`, `/screen`, `/leaderboard`, `/results`.
- Hidden cases use new suppliers; up to a third combine supplier and case types never seen in practice.
- Top 10 by points pitch to the tacto jury from 13:00: 5 min + 2 min questions; every replay is open to the jury.

## Practice

- 10 hosted practice cases (data covers 19), a sandbox with 3 demo suppliers, a replay after every case showing
  every e-mail and the supplier's reasons in words. Best score per practice case counts on the practice board.
- **Quota: 200 practice sessions per team per rolling 24 h.**
- `arena-sim` runs the API locally with one scripted supplier (plumbing only, not realistic).
- Build page: https://arena.tacto.ai/live · results: https://arena.tacto.ai/results

## Getting started

1. Apply at https://arena.tacto.ai/apply (team name + contact e-mail), show the match code at the tacto desk. The
   API key is shown **once**; one key per team. Keep it out of git.
2. Needs Python 3.12, uv, git. Clone the kit, `cp .env.example .env`, `uv sync --all-extras`, `uv run arena me`,
   `uv run arena play case:P01`, `uv run arena board`.
3. Starter kit modules: `client.py` (API), `play.py` (runner incl. `play_waves`), `cost.py` (C), `docs.py` (PDF/XLSX
   to text), `data.py` (package + SQL), `agents/naive.py`, `agents/llm.py`, `sim.py`, `mcp_server.py`.
4. LLM: `LLM_PROVIDER=anthropic` or any OpenAI-compatible endpoint. Prefer a fast model because of the 60-second rule.

## Submission (ehl.gg, Sun 12:00)

- Repo with the agent and a run README, plus the **commit hash** that runs in the final. The jury and tum.ai must be
  able to open the repo; tum.ai runs a code agent over every submission. No keys or `.env` in the repo.
- Short description of the approach; pitch slides.
- Checklist for Sun: 08:00 new data, 09:00 pre-flight with one practice case on the final machine and network,
  11:30 runner started, logging to a file, laptop on power and not sleeping, enough LLM credits for an hour.

## Prize

1,500 EUR for 1st place, medals for top 3. Best 2 of the EHL finalist teams in this track go to the Grand Final.

## Rules

Not allowed: humans in the loop during the final, attacks on the service or other teams, more than one key, reading
the live feed. Everything else counts, including exploiting weaknesses in the supplier bots (replays are visible to
the jury).

## Assessment for team "Day by day" (Claude's reading, not from the source)

- Fast start: a working baseline agent in ~15 minutes, plain HTTP API, no browser automation. Lower engineering
  risk than the Atira track.
- The work is strategy and data mining, not plumbing: pick the right facts per supplier, propose multi-term-sheet
  trades, decide when to accept or walk. The kit's own tip: numbers from code, wording from the model.
- Tight feedback loop (exact score + replay with the supplier's reasons), but capped at 200 sessions per 24 h, so
  runs must be planned.
- Needs our own LLM budget; the EHL OpenAI credits (75 USD per person) cover this.
- Operational risk on Sunday: the final runs live on our laptop and network for an hour with no intervention.
- Generalization matters here too: a third of the hidden cases are unseen combinations.
