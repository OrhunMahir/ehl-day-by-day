# Work streams — who builds what, and where the streams meet

Written Sat 2026-10-10 for the three people on the team and for their coding agents. The streams are named after
their owners: J = Julien, I = Ivan, O = Orhun. The short table is in
[AGENTS.md](../AGENTS.md), section "Who does what"; this file is the detail. If you are a coding agent, read
"How the agent works today", then the section of your human's stream, then "Where the streams meet".

The work is split by files. Each file has exactly one stream that may edit it. That is what lets three people and
their agents work at the same time without merge conflicts and without undoing each other's decisions.

## How the agent works today

Everything submitted to Atira is the folder `agent/`. One run goes like this:

1. `agent.py` reads the run (`run.py`: the brief with the bid rules, each website's address and login, the answer
   schema, the expiry time) and opens one browser tab per website (`browsers.py`, `browser.py`).
2. A **lead model** (`Orchestrator` in `agent.py`) reads the brief. It cannot browse. It has three tools:
   - `investigate(site, task)` sends a **browsing sub-agent** (`BrowsingAgent` in `browsing.py`) to one website with
     a task in plain language. The sub-agent drives the browser through a text view of the page (`observe.js`,
     `Tab` in `browser.py`), takes notes, and returns one text report. Several sites run in parallel.
   - `calculate(expression)` does exact decimal arithmetic (`calc.py`).
   - `submit_bid(answer, hints)` checks the answer against the schema, then sends the submission agent
     (`submission.py`) to enter the bid in the tender website's form, fetch the signing code from the mailbox, sign
     and confirm. It reads the page like a browsing sub-agent, but sets all fields of a form step in one call.
3. The lead model decides what to ask next from the reports, works out the bid, and submits.

The run's time is split in `agent.py`. Every run, scored or not, works with 20 minutes: investigations end at
40 % (minute 8), from 45 % (minute 9) the lead is made to call `submit_bid`, and entering and signing the bid gets
the rest. After each submission attempt the agent asks the platform whether the run is still open; the platform
closes a run the moment it accepts a bid, so an open run means "not accepted yet" and the lead submits again.

What is weak in this design, and why the streams exist:

- The reports are free text. A value that was read wrongly, or from the wrong record, is not caught anywhere.
- The lead model applies about 28 bid rules by itself, one calculator call at a time. Money is scored to the cent,
  and 75 of 100 points sit in the positions.
- The final run uses different websites. Anything that only works because of how the public websites look fails
  there.

## Stream J — sites and reading (Julien)

**Goal.** A sub-agent that is sent to any of the websites with a task comes back with a report that is complete,
exact (values as shown, with units and dates) and says where each value was read. This must hold on software the
agent has never seen.

**Files only this stream edits.** `agent/browser.py`, `agent/observe.js`, `agent/browsing.py`,
`agent/browsers.py`, `agent/tests/test_browser*.py`, `docs/log/julien.md`.

**What the other streams rely on (do not change without telling stream O first).**
- `Tab(site_id, page)` and `BrowsingAgent(model, tabs, credentials, task, context, name=..., deadline=...,
  max_steps=..., log=...)`, as `agent.py` calls them today.
- `await BrowsingAgent.run()` returns one string: the report, then the notes taken while browsing.
- Stream O also uses these internals; rename or change them only together with O: `Tab._locate`,
  `Tab.aid_frames`, `Tab._absolute`, `Tab.observe`, `BrowsingAgent._login`, `BrowsingAgent._report`.

**Work, in this order.**
1. Run one sub-agent per website on your machine with a survey task and compare its report with what you see in
   the browser yourself. Every difference is a bug in this stream.
2. Documents: PDF and ZIP files, documents inside embedded frames and nested viewers. `pypdf` is a declared
   dependency since Sat 14:30; before that every PDF came back as "could not be read" in the sandbox.
3. Lists: paging, "load more", searches that must be narrowed before they show results.
4. Versions: a site that opens an old revision first; the report must say which revision a value comes from and
   which one the site marks as current.
5. Sessions and errors: a session that expires and returns to the sign-in page, error pages that work on a retry,
   popups and dialogs.
6. Look-alike records: the report must name the exact reference, sender and date of what it read, so a record for
   another tender is recognisable as such.
7. Generalisation check: no selector, text, URL or page order that only exists on the public websites. The sign-in
   helper in `browsing.py` finds fields by their type, which is fine; keep it that way.
8. Only when the reports are reliable: fewer steps and tokens per report.

**Done when** a report contains every value the task asked for, as shown on the site, each with its source (page
or document, date, sender, revision), and says plainly what it could not find.

**Not this stream.** Bid rules, arithmetic, the lead model's prompts, the submission flow. Also the pitch and the
development story belong to Julien, outside the code.

## Stream I — bid engine (Ivan)

**Goal.** One function that turns collected facts into the answer object, correct to the cent, with no model
involved. The same facts always give the same bid.

**Files only this stream edits (all new).** `agent/facts.py`, `agent/engine/`, `agent/tests/test_engine*.py`,
`docs/log/ivan.md`.

**What the other streams rely on.**
- `facts.py`: typed records (dataclasses) for everything the rules need. Every fact carries where it came from:
  website, document or record, date, sender, revision. This file is the contract between the streams; a change is
  announced in the first line of the commit message.
- One entry point, proposed as `engine.build_bid(facts) -> dict`, returning an object that matches the answer
  schema in [challenge-atira.md](challenge-atira.md), section "Answer schema".

**Rules for the code.**
- `Decimal` only, never `float`. Components stay unrounded; round half-up once at the end (rule 27).
- No model calls, no network, no browser imports. The engine must run in a unit test in milliseconds.
- Tests use invented numbers only. Nothing read from the websites goes into code or tests.
- Missing facts must not crash: a position whose facts are incomplete is left out, and an optional breakdown field
  that is unknown is omitted (omitted means "unanswered", `0.00` means "a known zero"; they score differently).
- If any position has no matching material, the decision is `no_bid` with no positions and total 0.

**Work, in this order.**
1. Read the bid rules in [challenge-atira.md](challenge-atira.md) and the full text with `atira challenge rfq`.
   Write each rule down as one or more cases with invented numbers; these become the tests.
2. `facts.py`, first version, covering the core rules only.
3. Core engine: final quantity (latest clarification, withdrawn clarifications, set-to-piece conversion); price
   condition valid on the scenario date at the highest quantity scale reached; discount (latest approval for the
   exact reference, conditional on a minimum order value); lead time (stock shared between positions of the same
   material, no partial allocation); total; arithmetic consistency.
4. Authority and time as functions over facts: only the latest record not after the scenario date, for the exact
   tender reference, from an authorised sender.
5. Advanced pricing layers, one at a time, each with its tests: which parts the discount applies to, currency
   conversion, metal-index surcharge with validity windows, packaging by carton, shared setup pool, project bonus
   in bands allocated by largest remainders, stroke derived from usable travel.

This stream needs no website, no browser and no test run, so it can start at once. Tell stream O as soon as
`facts.py` covers the core rules; O can then start filling it from the reports.

**Done when** `uv run python -m unittest discover -s tests` passes in `agent/` and every rule has at least one
test.

**Not this stream.** Reading websites, prompts, form filling.

## Stream O — lead, submission, runs (Orhun)

**Goal.** The run as a whole: the lead model asks the right questions, the answers become facts, the engine prices
them, and one signed bid is submitted before the clock ends, every time.

**Files only this stream edits.** `agent/agent.py`, `agent/submission.py`, `agent/llm.py`, `agent/models.py`, `agent/run.py`,
`agent/calc.py`, `agent/agent.json`, `agent/pyproject.toml`, `agent/uv.lock`, `agent/tests/test_kit.py`,
`README.md`, `AGENTS.md`, `CLAUDE.md`, `.gitignore`, `docs/` (except the other two push logs).

**Work, in this order.**
1. Baseline: one sandbox test run of the unchanged agent and its score breakdown. It shows where points are lost
   and sets the priorities of streams J and I.
2. Time budget: in place (see "How the agent works today"). The shares are a first guess from one run in which
   the first round of investigations took 4 to 7 minutes and each lead decision 1 to 1.5 minutes; tune them once
   a run has shown how long entering and signing the bid takes.
3. Deadline guard: the lead is forced to call `submit_bid` at 50 % and again after every attempt the platform did
   not accept. Still open: the submission sub-agent is told by `browsing.py` to "finish" 90 seconds before its
   end, which fits an investigation but not a submission (stream J's file; the task text asks it not to stop).
4. Reports to facts: have the lead model put what the sub-agents report into the records of `facts.py`, and call
   the engine instead of the calculator, as soon as stream I delivers the first version.
5. Submission: `submission.py` fills a whole form step per model call. Open: it has not yet been seen to reach
   the signature on the website; the signing code from the mailbox and the receipt check are untested.
6. `llm.py`: when a provider's environment variable is missing it falls back to the provider's public address.
   Model calls must only go through the platform's proxy, so remove these fallbacks.
7. Dependencies requested by the other streams (`pypdf` for stream J is in).
8. `tests/test_kit.py`: three tests still target the starter kit's `solve()` and fail; rewrite them for the
   current `agent.py`.
9. Test runs for all streams, every `atira submit`, Entire and the repository.

**Not this stream.** How a page is read (`browser.py`, `observe.js`, `browsing.py`) and how a price is calculated
(`engine/`).

## Where the streams meet

```
websites ──J──> report (text) ──O──> facts (facts.py, owned by I) ──I──> answer object ──O──> signed bid
```

- **J to O:** the report string from `BrowsingAgent.run()`.
- **O to I:** the records in `facts.py`. I defines them, O fills them.
- **I to O:** `engine.build_bid(facts)` returns the answer object.
- You need a change in a file of another stream: describe it to your human, who asks the owner. Do not edit it,
  not even for a one-line fix; the owner may be changing the same lines.
- You need a new dependency: ask stream O. `pyproject.toml` and `uv.lock` have one owner so the lock file never
  conflicts.
- Local runs with the scouting logins (`eval "$(atira scout --env)"`, with `atira models proxy` running) are free
  and unlimited for everyone. Sandbox test runs are limited to four at a time for the whole team (three more can wait) and go through
  stream O; ask for one when you need a score.
- Everyone works on their own branch (`stream-j`, `stream-i`, `stream-o`); `main` changes only through pull
  requests. The rules are in AGENTS.md, section "Branches".
- Every push adds an entry to your own push log (`docs/log/<name>.md`, format in AGENTS.md). Read the others'
  new entries after every pull.

## Order of the whole thing (proposal)

1. Now: baseline score (O); core rules as tests and `facts.py` first version (I); per-site report checks (J).
2. Next: core engine wired into the run (I and O); documents and paging reliable (J); second test run.
3. Then: advanced pricing layers (I); submission and deadline guard hardened (O); generalisation check (J).
4. Sunday morning: no new features. The Atira final uses our latest submission that passed review, so the last
   submission before the end must be the most stable one, not the newest experiment. EHL submission is Sunday 12:00.
