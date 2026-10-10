# Branch `stream-j-simple` at `92456f6`

The board concept: one browsing agent per website, connected only by a shared board of typed facts. Best sandbox
result so far: **quality 51.2** (`run_9b8aee1022f379bc`, Qwen only, eligible, 11:44, $0.27). Earlier states of the
team's agent scored 10 in every sandbox run.

No values from the websites are in this file (no references, material numbers, prices or quantities), only points,
timings and how the agent behaves.

## Where it comes from

| Commit | What |
|---|---|
| `31f8933` (`stream-j`) | Starting point: `main` + `stream-o` up to `3a0362e` + Ivan's `exp-speed` (`a6ef420`) + stream J's fixes (submission gets through the configurator's cascades, hung page loads, reports keep what was read last) |
| `45685b3` | The engine always bids every position with what is known; the lead loop without rounds (kept in `agent.py`, not used by this branch's entry point) |
| `8af7391` | First attempt: three fixed jobs (positions, materials, rules) recording typed facts. Replaced by the next commit: agents wandered across sites and logged in on the same site several times |
| `91893e5` | **Board concept**: one agent per website, `board.py`, `simple.py` as the entry point |
| `92456f6` | Only the agent that searched records a catalogue group as searched; a pending form submission is never cut short; websites opened at the same time |

## How a run works

1. **Start (about 5 s).** The four websites are opened at the same time. One model call reads the tender reference,
   the scenario date and the sales director's address from the brief and puts them on the board.
2. **One agent per website, until minute 9 (45 % of the run).** Every website of the run's access list gets one
   browsing agent with that website's tab only: one session, no second login, no other site to wander to. No site
   names or roles are in the code; every agent gets the same job text.
   - Each agent records every fact the bid rules need that its website shows, typed, with `record`, the moment it
     reads it. Positions, Q&A answers, revisions and requirements, materials and price conditions, mails and their
     records: whatever the website holds.
   - Before each step an agent sees what changed on the board: what the other agents recorded, and what the bid
     still needs (the engine's `needed` list, by role, not by website). It takes on what its own website can
     answer, e.g. the ERP agent searches a catalogue group as soon as the specification's agent has recorded it.
   - When nothing on its website is left for now, an agent calls `wait`, which returns as soon as the board
     changes (at most 45 s).
3. **Pricing (at minute 9).** The engine prices the board's facts. Every position of the bill of quantities is
   always in the answer: an exact material, else the nearest candidate of its catalogue group, else none (then the
   submission chooses it in the form from the requirement's attributes). Price parts that are not known count as 0;
   the total is the sum of the entered positions.
4. **Submission.** The submission agent enters the answer in the tender portal's configurator, gets the TAN from
   the webmail and signs. The materials' attributes (or the requirement's, when no material is confirmed) are given
   to it for the configurator's cascade of selections. A second attempt follows if the platform did not accept the
   bid.

There is no lead model, no recorder, no report and no hand-off between models: the board is the only connection,
and it is immediate.

## Files

| File | Role on this branch |
|---|---|
| `agent/simple.py` | Entry point (`agent.json` runs it): start, brief, one agent per website, pricing, submission |
| `agent/board.py` | The shared board: typed facts (a `facts.FactsBuilder`), a change counter, the update text for each agent (others' records plus open items), `wait`; refuses `searched_groups` from an agent that recorded no materials |
| `agent/browsing.py` | The browsing agent; board mode adds `record` (full facts schema) and `wait`, shows board updates before each step, and has no `note`, `finish` or wrap-up report |
| `agent/browser.py`, `agent/observe.js` | Page reading and actions: numbered elements, frames, dialogs first, hidden decoys dropped, paged lists, downloads; a plain page load unanswered for 25 s is requested again, a form submission is never cut short; an element named by its text is the button or link with exactly that name |
| `agent/engine/` | Deterministic pricing (rules 01–28). On this branch: every position is always bid (`allow_no_bid`, `resolve.best_candidate`), the ERP search is asked for as soon as the specification names a catalogue group, with the attributes to match |
| `agent/facts.py` | Typed facts and the update schema the agents record with |
| `agent/agent.py` | Reused for pricing and submitting (`Orchestrator.price`, `submit`, `materials`, the check against a hasty no-bid); its lead loop is not used here |
| `agent/submission.py` | The submission agent: `fill_form` (also with buttons between fields for cascades), clicks name the button pressed, a note when the same action brings it back to the same page a third time |
| `agent/llm.py` | Model client; `Model(thinking=False)` switches Qwen's thinking off |
| `agent/tests/test_simple.py` | Board and concept tests, among them two websites where the ERP agent starts the moment the specification's group is on the board |

## Settings

| Setting | Value | Where |
|---|---|---|
| Model | Qwen3.8-Flash-Next, thinking off, for every agent | `simple.py` |
| Investigations end | 45 % of 20 minutes (minute 9) | `agent.py` `COLLECT_SHARE` |
| After the end | 30 s grace for running steps, then pricing and submission | `agent.py` `INVESTIGATION_GRACE_SECONDS` |
| Steps per agent | at most 400 (the clock ends it first) | `simple.py` `JOB_STEPS` |
| One `wait` | at most 45 s | `browsing.py` `WAIT_SECONDS` |
| A model call counts as stalled | after 30 s without thinking, 75 s with; up to 5 attempts | `browsing.py` |
| One browser action | at most 60 s | `browsing.py` `ACTION_SECONDS` |
| One page load attempt / re-request of a hung load | 30 s / after 25 s | `browser.py` |
| Board update | the last 12 records of others (700 characters each), up to 15 open items | `board.py` |
| Submission | 2 attempts; told to sign 150 s before the end; a model call dropped after 45 s | `simple.py`, `submission.py` |
| Package | `"run": ["uv", "run", "--no-sync", "python", "simple.py"]`, `"models": ["qwen"]`, `"browsers": 4` | `agent/agent.json` |

## Results

### Sandbox test runs

| Run | Commit | Quality | What happened |
|---|---|---|---|
| `run_930993c7fb3173ed` | `91893e5` | 5 | The ERP never answered in time (every action over 60 s, the start took 83 s). The specification's agent recorded every catalogue group as searched; with no materials the engine took that for a search without a match and the bid went in as "no bid". Fixed in `92456f6` |
| `run_9b8aee1022f379bc` | `92456f6` | **51.2** | See below. The platform lists this run as a scored run of submission `sub_fd3d69351a3a7fd1`, although it was started as a test run; not checked yet |

`run_9b8aee1022f379bc`, timeline (seconds into the run): brief 4 · positions 49 · discount approvals 99 · first ERP
materials 112 · delivery commitments 170 · specification 186 · Q&A changes, withdrawal, amendments 214 · ERP with 7
groups searched and 9 materials 275 · investigations end 541 · run end 704. 210 model calls, all Qwen.

Points per field:

| Field | Points |
|---|---|
| Decision, tender reference | 5 + 5 |
| Discount percent | 0 (a wrong percentage entered) |
| Total net | 0 |
| Total consistent with the positions | 5 |
| Withdrawn Q&A quantity check | 5 |
| Dated metal-index check | 0 |
| Positions (31.2 of 65) | material 2.6 × 5, quantity 1.95 × 5, lead time 1.3 × 5, base price 1.95 for one position; surcharge, packaging, project bonus and unit price 0 everywhere |

### Local runs (scouting logins, free Qwen through `atira models proxy`)

| Log (`scouting/`, git-ignored) | Commit | Outcome |
|---|---|---|
| `local-board-1.log` | `91893e5` | Bid with 5 positions signed (receipt) at 1020 s. The webmail hung locally for minutes, so no mail facts |
| `local-board-2.log` | `92456f6` | Bid signed at 655 s. The supplier portal hung locally on every page load; the board refused one wrong "searched" record, as intended |

Unit tests at `92456f6`: 215 pass, 1 skipped (`cd agent && uv run python -m unittest discover -s tests`).

## What is still wrong or open

1. **Prices (about 45 points left).** Base price right for one position of five. Seen in runs:
   - price conditions on a second page were not read;
   - a price per 100 pieces was recorded as a price per piece;
   - USD prices need the exchange rate, and the surcharges need the metal index and masses.

   So unit prices, the total, surcharge, packaging and bonus are missing or wrong.
2. **Discount.** A wrong percentage was entered. Not analysed yet: a wrong record, or the conditional threshold
   computed from incomplete prices.
3. **Wrong records by a "foreign" agent.** Every agent may record every kind of fact (on purpose, for websites
   that split the information differently). The board now refuses one known case (`searched_groups`); others may
   exist.
4. **Websites that hang.** The scouting websites hung for minutes in local runs (webmail, supplier portal), and the
   sandbox ERP in one run. An agent can only retry; the always-complete answer limits the damage.
5. **Unused time.** The 51.2 run ended after 11:44 of 20 minutes. Investigating longer is the cheapest gain, as long
   as the submission keeps enough time (five priced positions took up to 487 s locally, about 165 s in the
   sandbox).
6. **The scored-run listing** of `run_9b8aee1022f379bc` (see above) is not explained yet.

## Next steps prepared, not on this branch

- `stream-j-tweaks` (from `92456f6`): investigations to 53 % of the run; every page and validity period of the price
  conditions; prices recorded exactly as shown with their price unit; up to 30 open items in a board update. Kept in
  `git stash` (`stash@{0}`), not committed, not run.
- **A stronger model (e.g. an OpenAI one through the proxy, key as the team secret `OPENAI_API_KEY`, which exists).**
  This code is not ready for it yet:
  - it sends `temperature` with every call, which OpenAI's reasoning models usually refuse;
  - it has no setting for the reasoning level;
  - its 30 s stall limit is set for Qwen.

  These three need a small change first.

## Fair play

No value from the websites is in the code, the prompts or the tests (invented values only). The models choose
where to navigate, what to search and what to record; code only runs the actions, reads pages, keeps the board and
prices the facts. No website names or roles are in the code: every website of the run's access list gets the same
job text.

## How to run

```sh
# local, against the public sites (free models)
atira models proxy                                        # in one terminal
eval "$(atira scout --env)" && cd agent && uv run python simple.py

# unit tests
cd agent && uv run python -m unittest discover -s tests

# sandbox test run (Julien starts them)
atira test-run agent
```
