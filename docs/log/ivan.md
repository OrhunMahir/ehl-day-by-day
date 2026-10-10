# Push log · Ivan · stream I: bid engine

Newest entry on top. Format and rules: [AGENTS.md](../../AGENTS.md), section "Push log".

## 2026-10-10 15:30 · Fewer site assumptions in facts.py, randomised scenarios against a reference

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
