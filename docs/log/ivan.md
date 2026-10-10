# Push log · Ivan · stream I: bid engine

Newest entry on top. Format and rules: [AGENTS.md](../../AGENTS.md), section "Push log".

## 2026-10-10 16:19 · Checklist of what is still missing: result.needed() and result.complete

- Changed: new `agent/engine/needs.py`. `result.needed()` lists what the lead still has to find or verify, each
  entry with `kind` ("find": a fact is missing; "verify": a value is assumed or decides a lot), `site` by role
  (brief, tender portal, specification, ERP, mailbox; not by name, since the final run's sites have other names),
  `what`, `rule` and `positions`. Ordered by kind and site, an outstanding ERP search first. `result.complete` is
  true when nothing is left to find and no position is left out or incompletely priced. The text helpers
  `clean`, `norm_key` and `norm_text` in `facts.py` are now cached: on a 100-position tender the engine takes
  0.4 s instead of 1.5 s, the checklist 0.8 s, the explanation 0.2 s. New `agent/tests/test_engine_needs.py`.
- Why: the two finished test runs (from before the engine was merged) scored 18.3 and 5; the material was wrong
  in every position of both, and five of eight runs ended without an answer. The lead needs to know what exactly
  is still missing, and when it can stop investigating and submit.
- Checked: 112 engine tests pass on Python 3.12 and 3.14, ruff clean. Every gap is named: for each of 17 kinds of
  removed facts (FX rate, metal index, tariff, pool, bonus, Konditionsregeln, commitment, end reserve, conditions,
  stock, carton size, masses, materials, pieces per set, current mark, revisions) and for damaged tenders, each
  position the engine leaves out or prices incompletely is named by a "find" entry; a one-off run over 3000 doubly
  damaged tenders found no unnamed gap. All 300 clean random tenders are `complete`.
- Learned: test-run scores are readable by every team member with `GET /v1/runs/{run_id}` (field `score.breakdown`,
  points per field). Only print points: the session logs are public, and `got` and `answer` hold our values.
- For the others: O — return `result.needed()` with every `price_bid` result and tell the lead to work through the
  "find" entries in order; when `result.complete` is true (or the time is up), submit. J — the entries name the
  site by role and say exactly what to read, which can go straight into a sub-agent's task.
- Next: read the next test run with the engine; then a check of the brief's rule text against the rules the
  engine implements.

## 2026-10-10 15:50 · Thorough check: Python 3.12, damaged facts, speed, fair play

- Changed: the engine now keeps the answer valid whatever the facts look like. A position listed twice counts once
  (last entry), an empty or over-long position number or material number leaves the position out (in `omitted`),
  a negative quantity is unknown, a discount outside 0–100 % counts as 0, a negative price component makes the
  unit price unknown and its breakdown field blank, an unreadable amendment value is ignored, a bonus or pool
  without usable weights is unknown; each case is named in `issues`. A no_bid now names the closest candidates
  and what differs ("closest: M: pressure 170 (required 160)"), so the lead can re-check those values before
  submitting a no_bid. `FactsBuilder` checks only what an update changes: 282 updates of a 100-position tender
  took 235 s before and 2.8 s now. New `agent/tests/test_engine_robust.py`.
- Why: a model fills the facts; a crash or an answer the configurator rejects costs the run. One misread attribute
  turns a bid into a no_bid, which loses almost all points, so a no_bid must be easy to double-check.
- Checked: 107 engine tests pass on Python 3.12 (the sandbox's version) and 3.14; ruff (errors, bugbear) clean.
  400 damaged tenders in the tests and 3000 more with twice the damage in a one-off run: no crash, every answer
  passes the answer schema with a total equal to the sum of its positions. A 100-position tender with 412 materials
  and 3872 mail records: evaluate 1.5 s, explain 0.9 s. `atira test-run agent --dry-run` packs `facts.py` and
  `engine/`. No value from the scouting notes in any package file (115 values checked). Full suite with
  `PYTHONUTF8=1`: only the three known `test_kit` failures.
- For the others: O — when `price_bid` returns a no_bid, have the lead re-check the values named after "closest:"
  once before submitting. J — nothing.
- Next: read O's first test run with the engine.

## 2026-10-10 15:32 · Explanation of the bid for the run log, guide for filling the facts

- Changed: `result.explain()` (new `agent/engine/explain.py`) returns plain text for the run log: per position
  how the quantity came about (which Q&A answers, which withdrawn), the governing requirement, the chosen material
  and why the other candidates of its group were not, the price condition and scale, every price component, the
  discount scope, the lead-time inputs; per mail key which record counts and how many were ignored and why (older,
  other tender reference, after the scenario date, sender not authorized). It decides nothing itself.
  `facts.FILLING_GUIDE` is a text for the lead's prompt: which fact goes into which field, with the traps from the
  rules and no values. The engine now reports an issue when `director_address` is empty, because then approvals
  from any sender count. The builder from the previous entry (not pushed then) is part of this push.
- Why: a test run shows only points per field; with the explanation in the log a lost point can be traced to a
  wrong fact or a wrong calculation. The guide keeps the prompt and the facts' meaning in one place.
- Checked: 105 engine tests pass; the explanation runs on all 300 random tenders and names the deciding facts in
  a hand-checked case; a test fails if the guide names a field that does not exist. Full suite with
  `PYTHONUTF8=1`: only the three known `test_kit` failures.
- For the others: O — put `facts.FILLING_GUIDE` into the lead's prompt (next to the facts tool), set
  `director_address` and `bidder_domain` from the brief, and print `result.explain()` into the run log after every
  `price_bid`. J — nothing.
- Next: read O's first test run with the engine; trace lost points with the explanation.

## 2026-10-10 15:25 · Facts in pieces: FactsBuilder, update_schema, to_dict

- Changed: `agent/facts.py` gains `FactsBuilder` (collects the facts over several tool calls), `update_schema()`
  (the schema of one update) and `to_dict()` (plain JSON of the facts, the reverse of `from_dict`). Nothing existing
  changed. New `agent/tests/test_engine_builder.py`.
- Why: with one big facts JSON, a single bad field throws away the whole tool call, and Qwen has to hold every fact
  until the end. Now the lead can add facts as each report comes in.
- How it behaves: each update is checked at once; an update with a bad field is rejected whole with the field
  named, and what was collected before stays. Lists grow; an item with the same identity as an earlier one (same
  position, same material number, same mail key + date + sender, ...) replaces it. Materials, revisions and
  positions merge field by field, so a material's stock and conditions can come after its attributes; a null never
  erases a known value. `replace_lists` takes back records added by mistake.
- Checked: 101 engine tests pass. 50 of the random tenders are cut into random pieces (materials and revisions split
  field by field, updates in random order) and must give the same bid as the facts sent at once; with the
  field-by-field merge of materials switched off on purpose, 47 of the 50 fail, so the test catches it.
- For the others: O — prefer two tools, `add_facts` (parameters `facts.update_schema()`, handler
  `builder.add(args)`) and `price_bid` (no parameters, `engine.evaluate(builder.facts())`); the docstring of
  `agent/engine/__init__.py` shows it. `from_dict` with one big JSON still works. Log `facts.to_dict(...)` or
  `builder.data` in the run log so a test run's points can be traced to the facts. J — nothing.
- Next: read O's first test run with the engine and trace lost points to facts or engine.

## 2026-10-10 15:10 · Re-read of /start and /challenges/rfq: pool allocation rule and omitted positions

- Changed: `facts.SetupPool.allocation` (new enum `facts.Allocation`) carries the cent-allocation rule the Rüstpools
  mail states; the default is floor plus largest remainders with ties by ascending position, as in rule 26.
  `engine.evaluate()` now returns `omitted`, the tender positions left out of the answer.
- Why: rule 25 says the pool is allocated "using the cent-allocation rule in that mail", so the method can differ in
  the private dataset; the engine had it fixed. Rule 9 requires unfinished positions to be omitted explicitly in the
  form ("Position auslassen / Teilpunkte"); the submission step could not tell which ones.
- Checked: the rule text on /challenges/rfq is unchanged since the engine was written (diffed against the copy from
  `atira challenge rfq`); /docs/scoring.md and /docs/fair-play.md read again. 90 engine tests pass.
- Learned: the leaderboard's leader has quality 21.5 after two submissions; most teams are not eligible yet.
  /docs/scoring.md: a wrong decision costs only its points; extra or duplicate positions cost one position's share
  each; test runs show points per answer field, scored runs do not.
- For the others: O — mark every position in `result.omitted` as "Position auslassen" in the configurator, sign
  anyway. Have the lead set `SetupPool.allocation` from the Rüstpools mail's wording. Worth deciding as a team:
  opt in to best of three for the final (`atira final --best-of 3`, before the final starts; only own-key calls
  cost up to 3x). J — nothing.
- Next: check the facts against a real local run's reports with O.

## 2026-10-10 15:05 · Fewer site assumptions in facts.py, randomised scenarios against a reference

- Changed: `agent/facts.py` contract change: `QuantityChange`, `TechnicalAmendment` and `Assignment` take
  `qa_number` as an optional field after the values (order: values, then `qa_number`, then `source`), so a
  quantity, amendment or key may come from any site, not only the tender Q&A. `from_dict` rejects number text whose
  format is ambiguous ("1.250": thousands or decimals?) instead of guessing; JSON numbers always pass.
  `json_schema()` now has descriptions for the objects and the fields that are easy to fill wrongly (kg vs g,
  "no certification" = null, price unit, searched groups). New `agent/tests/test_engine_random.py`.
- Why: the final run uses other software with the information split differently across the sites; the engine must
  not depend on where a fact was found or how the public sites write numbers.
- Checked: 86 engine tests pass. The new test invents 300 tenders, writes them into facts with decoys (superseded,
  look-alike, future-dated and wrong-sender records; inactive and near-miss materials; old revisions; withdrawn and
  superseded clarifications; expired and future conditions; shuffled lists; four position-number styles) and
  compares every answer field with a separate reference written from the rule text with Fractions. The scenarios
  contain 816 positions and 26 no-bid cases. Twelve deliberate bugs planted one at a time in the engine (ignore
  withdrawals, no FX rounding, wrong discount scope, no date check, no sender check, stock not pooled, bonus not
  marginal, no end reserve, exclusive threshold, wrong condition, wrong scale, reversed tie order) were all caught.
  Still not checked: facts read from the real websites.
- Learned: nothing new about the sites.
- For the others: O — if you already build `QuantityChange`, `TechnicalAmendment` or `Assignment` positionally,
  move the Q&A number behind the values or pass it by name. Send numbers to `from_dict` as JSON numbers where
  possible. J — nothing.
- Next: check the facts against a real local run's reports with O.

## 2026-10-10 14:52 · facts.py first version and the bid engine

- Changed: new `agent/facts.py` (typed records for everything the bid rules need, each with a `Source`: site,
  document, date, sender, revision) and new `agent/engine/` (`build_bid(facts)` returns the answer object,
  `evaluate(facts)` also returns `issues`, the working per position and the positions whose price is incomplete).
  `facts.from_dict` builds the facts from plain JSON (German or English numbers, ISO or German dates) and
  `facts.json_schema()` gives the matching JSON Schema, so the lead can fill everything with one tool call. Tests:
  `agent/tests/test_engine_*.py`, invented numbers only.
- Why: the lead model applied the bid rules by itself with the calculator; money is scored to the cent and 75
  points sit in the positions. The engine does the arithmetic and the authority and time rules in code.
- Checked: 84 engine tests pass (`uv run python -m unittest discover -s tests -p "test_engine*"` in `agent/`).
  The full rule text came from `atira challenge rfq`; every calculation rule (1–8, 12–18, 20–28) has at least one
  test; rules 9–11, 19 and 29 are about the run and scoring, not the engine. Not checked: no run
  against real facts from the websites yet; nothing calls the engine in `agent.py` yet.
- Learned: on Windows the full suite cannot import `tests/test_kit.py` because `browser.py` reads `observe.js`
  without `encoding="utf-8"` (it works with `PYTHONUTF8=1`; the sandbox is Linux, so the run is not affected). With
  UTF-8 forced, only the three known `test_kit` failures remain. Entire's commit hook asks "Link this commit to
  session context?" on the Windows console and hangs when an agent commits; `"commit_linking": "always"` in the
  git-ignored `.entire/settings.local.json` answers it once for this clone.
- Choices to confirm (the rule text leaves them open):
  - A price in EUR stays unrounded per piece; only a converted foreign price is rounded to cents (rule 22).
  - Mail records are resolved per key (rule 21): a newer message that does not mention a key leaves the older
    record for that key in force.
  - Q&A answers dated after the scenario date are ignored, like mail records.
  - A requirement "keine gefordert" matches only a material without certification.
  - An ordinary setup fee that was not found counts as 0.
  - A position whose price lacks a component is still listed (material, quantity and lead time can score); it is
    named in `result.incomplete` and `issues`.
  - `dec("1.250")` without a locale reads 1.25; pass the locale the site states ("de"/"en").
- For the others: O — the engine is ready to wire in; the docstring of `agent/engine/__init__.py` shows the four
  lines needed. Masses go into the facts in kg (`facts.mass_kg("625 g")`). Record every ERP Kataloggruppe that was
  searched in `searched_groups`, also when the search found nothing: only then does a missing material make a
  no_bid. J — please open `observe.js` with `encoding="utf-8"` in `browser.py`; the reports should state the
  sender address, date and exact reference of every mail value, because the engine filters on them.
- Next: check the facts shape against a real local run's reports (with O), then the edge cases the rule text leaves
  open.
