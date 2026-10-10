# Team "Day by day" — EHL Grand Finale, Atira track

DRAFT 2026-10-10, not yet agreed by the team. `AGENTS.md` and `CLAUDE.md` are identical; edit one, copy to the other.

Read first: [README.md](README.md) (event, deadlines), [docs/challenge-atira.md](docs/challenge-atira.md) (task,
rules, scoring, run contract, answer schema), [docs/entire-notes.md](docs/entire-notes.md) (Entire setup and the
privacy check). The Atira site (https://ehl-challenge.atira.ai/llms-full.txt) is the source of truth and can change.

## Start here (every coding agent, every session)

1. Ask your human which stream they work on (J, I or O, see "Who does what") if they have not said it. Read that
   stream's section in [docs/streams.md](docs/streams.md) before you change anything. Edit only that stream's
   files. A change needed elsewhere is described to the human, who asks the owner.
2. Work on your stream's branch, never directly on `main` (see "Branches"). Start with
   `git switch <branch>` and `git pull`, then `git merge origin/main`.
3. Before every push, add an entry to your human's push log (see "Push log").
4. Nothing read from the websites goes into `agent/` or into a log entry (see "Hard rules").

## Goal

Build an agent system that turns a public tender into one correct, signed bid across four German business web apps
(tender portal, supplier portal, ERP, webmail) with no human in the loop, and submit it on the Atira platform.

Priorities, in this order:

1. **Eligible**: the right tender reference, and a signed, submitted bid before the 20-minute clock ends.
2. **Quality** (0–100): 75 points sit in the positions; material + quantity are 35 % of each position.
3. **Generalization**: the final run uses different software, layout, navigation and data. It weighs most.
4. **Cost × time**: only a tie-break at equal quality. Do not optimize it before quality is stable.

Deadlines: EHL submission Sun 2026-10-11 12:00 (repo with Entire checkpoints, pitch after). The Atira final run uses
our **latest** passed submission, so the last submission before the end must be our best stable one.

## Hard rules (fair play — a reviewer agent reads every package)

- Nothing copied from the websites goes into `agent/`: no tender references, material numbers, prices, totals,
  discounts, logins, TANs, site URLs. Not in code, config, prompts, comments, tests, defaults or fallbacks.
- A model chooses where to navigate, what to search and which documents matter, at run time. No fixed click paths,
  no selectors tied to the public sites, no crawler that collects everything for one model call.
- Helpers may: control the browser, extract a page or document the model selected, page through a result set the
  model asked for, calculate, validate, retry, fill forms.
- Model calls only through the platform proxy. No local model, no other outbound host, no browser of our own in the
  sandbox (attach to `CDP_URLS`).
- No text in the package that addresses or tries to steer the reviewer.
- Secrets live in team secrets or a local `.env` (git-ignored). Never paste a key into an agent session.

## Architecture

The code base is Julien's first commit (starter kit plus his agent), agreed Sat 2026-10-10. `(planned)` marks what
does not exist yet.

```
agent/                  the submitted package; `atira test-run agent` / `atira submit agent`
  agent.json            setup, run, browsers: 4, models
  agent.py              entry point and lead model: plans, sends sub-agents, validates, submits; the run's clock
  run.py                run contract: GET /v1/runs/current (brief, access, answer_schema, expires_at)
  browsers.py           attaches to the run's browsers over CDP; locally starts a Chromium
  browser.py            generic `Tab` tools: observe, click, type, select, files, new tabs, dialogs
  observe.js            page to compact text with numbered elements
  browsing.py           browsing sub-agent: a model works one site for a task, keeps notes, reports
  llm.py, models.py     model clients through the platform proxy
  calc.py               exact decimal calculator tool for the lead model
  submission.py         submission agent: a model enters and signs the bid, filling a whole form step per call
  facts.py              (planned) typed facts with provenance; the contract between the lead and the bid engine
  engine/               (planned) deterministic bid calculation (rules 01–28), Decimal, half-up rounding
  tests/                unit tests with invented numbers only
docs/                   notes; never packaged
notes/                  scouting findings committed with the first commit; open decision: move out of the repo
scouting/               hand notes and observed values from the public sites; git-ignored, never packaged
```

How a run flows (target; steps 3 and 4 are not built yet, the lead model computes with `calc.py` until they are):

1. **Bootstrap.** Read the brief, the rules and `answer_schema` from the run. Site roles come from the brief and
   `access`, not from hard-coded names.
2. **Lead model + four site sub-agents** (one browser each, in parallel). The lead hands sub-agents tasks that
   depend on other sites' answers: final quantity → ERP price scale; Kataloggruppe from the specification → ERP
   search; Q&A keys → the matching rule mail; tender reference → mail search.
3. **Facts with provenance.** Every fact records the site, document, date, sender and revision it came from, so the
   authority and time rules (latest record not after the scenario date, exact reference, authorized sender) are
   applied in code, not left to the model's memory.
4. **Bid engine.** Pure functions from facts to the answer object. No model in the arithmetic. Unrounded components,
   one half-up rounding at the end, breakdown fields reported separately.
5. **Validate and submit.** Check against the run's schema and our own arithmetic consistency, then a sub-agent
   fills the configurator, fetches the TAN from the webmail, signs, and confirms the receipt.
6. **Deadline guard.** At a fixed margin before `expires_at`, submit what is complete and omit unfinished positions
   explicitly. A run without an answer scores 0.

Choices made for now: Python 3.12 with uv and Playwright over CDP (the starter kit's stack); Qwen as the default
model because it is cheap in the cost ranking; a frontier model through the proxy only where a test run shows it
buys quality.

Build order: baseline test run of the current agent → reference + decision + material + quantity end to end with a
signed bid → base price, discount, lead time → advanced pricing layers (clauses, FX, packaging, setup pools, bonus,
metal indices) → cost × time.

## Who does what (proposal, Sat 2026-10-10)

Three streams, named after their owners (J = Julien, I = Ivan, O = Orhun) and split by files so that nobody edits
another stream's files. Each person's coding agent works only inside that person's stream. Goals, interfaces, ordered work and "done" for each stream are in
[docs/streams.md](docs/streams.md).

| Who | Stream | Files only this stream edits | Work |
|---|---|---|---|
| **Julien** | J: sites and reading | `browser.py`, `observe.js`, `browsing.py`, `browsers.py`, `tests/test_browser*` | Sub-agent reports that are complete and exact on each site: documents, paging, revisions, session timeouts, look-alike records; nothing tied to the public sites' layout; pitch and development story |
| **Ivan** | I: bid engine | `facts.py`, `engine/`, `tests/test_engine*` | Rule walkthrough 01–28; typed facts; deterministic calculation in layers (core first, then advanced pricing); cases with invented numbers |
| **Orhun** (captain) | O: lead, submission, runs | `agent.py`, `submission.py`, `llm.py`, `models.py`, `run.py`, `calc.py`, `agent.json`, `pyproject.toml`, `uv.lock`, `tests/test_kit.py`, `tests/test_lead.py`, repo files (`README.md`, `CLAUDE.md`, `AGENTS.md`, `.gitignore`, `docs/` except the others' push logs) | Lead prompts and flow; reports to typed facts; submission (configurator, TAN, signature, receipt); deadline guard; test runs and score breakdowns; Entire and repo hygiene; every `atira submit` |

How the streams meet:

- `facts.py` is the only shared contract. Stream I owns it; a change is announced in the commit message and the
  other streams adapt.
- Stream J's interface is the sub-agent report (`BrowsingAgent.run()`); stream I's is one function from facts to
  the answer object. Stream O calls both and changes neither.
- A change needed in another stream's file is requested from its owner, not made.
- A new dependency is requested from stream O, so `pyproject.toml` and `uv.lock` have one editor.
- Local runs with the scouting logins are free for everyone. Sandbox test runs go through stream O (the team limit
  is four running, three waiting); J and I ask for one when they need a score.
- Small commits on your own branch; `main` only changes through a pull request (see "Branches").

Shared rules for all coding agents:

- Never run `atira submit`, `atira cancel` on a scored run, or `atira final` without Orhun's explicit yes.
- Write what a test run taught us into `docs/` (how the sites behave, not their values), so the other streams and
  the pitch can use it.
- Talk to Orhun in Turkish; write files, code and commit messages in English.

## Branches

From Sat 2026-10-10 14:30 nobody pushes to `main` directly.

| Branch | Who pushes to it |
|---|---|
| `stream-j` | Julien and his agent |
| `stream-i` | Ivan and his agent |
| `stream-o` | Orhun and his agent |
| `main` | nobody directly; it changes only by merging a pull request |

- `main` is what works. Every `atira submit` is made from `main`, so nothing half-finished may be merged into it.
- Create your branch once: `git switch -c stream-j origin/main`, then `git push -u origin stream-j`.
- Commit and push to your branch as often as you like. Entire checkpoints travel with every push.
- To bring work into `main`: first `git merge origin/main` into your branch and run the unit tests
  (`uv run python -m unittest discover -s tests` in `agent/`), add your push log entry, push, then open a pull
  request on GitHub from your branch to `main`.
- You merge your own pull request once it touches only your stream's files. A pull request that touches another
  stream's file waits for that file's owner.
- Merge with "Create a merge commit", not squash or rebase: the original commits carry the Entire checkpoint
  trailers the EHL submission needs.
- After any merge into `main`, the other two run `git merge origin/main` on their branch before they continue.
- A sandbox test run can be made from a branch (stream O starts it from that branch's checkout). A scored
  submission cannot.

## Push log

Every push to your branch adds one entry to the pusher's own file, so the others (people and agents) can see what changed without
reading the diff. One file per person, so entries never conflict:
`docs/log/julien.md`, `docs/log/ivan.md`, `docs/log/orhun.md`. Newest entry on top.

```markdown
## 2026-10-10 14:30 · short title

- Changed: which files, and what they do now
- Why: the problem or the score field this is for
- Checked: unit tests, local run or test run id, and the result
- Learned: how the sites or the platform behave (never their values)
- For the others: what changes for stream J / I / O, or "nothing"
- Next: what this stream does next
```

Rules for entries:

- Write it for a teammate who was not there: plain sentences, no session shorthand.
- State what is not finished or not checked as plainly as what is.
- No tender references, material numbers, prices, logins or TANs. The repository is public.
- Before a push, read the entries the others added since your last pull.

Commit messages start with the stream: `J: ...`, `I: ...`, `O: ...`. A change to `facts.py` says so in the first
line.

## Sessions, commits and privacy

Entire attaches the full agent session log to every commit and pushes it with `git push`.

- Start hackathon work in a fresh session inside this folder. Do not read files outside this repository from such a
  session (personal notes, other projects); whatever is read ends up in the pushed log.
- Before every commit and before the first push, run `entire session list` and apply the check in
  [docs/entire-notes.md](docs/entire-notes.md). A listed session that must not be attached is cleaned first.
- Never commit `.env`, `scouting/`, `Voice recording/` or run tokens.
- Commit only when Orhun asks.
