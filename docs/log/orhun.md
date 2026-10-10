# Push log · Orhun · stream O: lead, submission, runs

Newest entry on top. Format and rules: [AGENTS.md](../../AGENTS.md), section "Push log".

## 2026-10-10 14:30 · Time schedule, PDF dependency, stream names J / I / O, branches

- Changed: `agent/agent.py` splits the run's time into three parts: investigations end at 65 % of the working
  time, from 70 % the lead is told to submit, and the submission sub-agent stops at 95 % (it used to run until two
  minutes after the end). The working time is the platform's own in a scored run (20 minutes) and 10 minutes in a
  test run or a local run. `agent/pyproject.toml` and `uv.lock` now include `pypdf`. In the documents the streams
  are renamed after their owners (A is now J, B is I, C is O; Uzunov is written as Ivan and his push log is
  `docs/log/ivan.md`), and a "Branches" section says that `main` only changes through pull requests.
- Why: the first test run was still going after 30 minutes without a bid, because the agent planned with the
  test run's two-hour expiry. PDFs could not be read in the sandbox. Three people pushing to `main` at once would
  break each other's runs.
- Checked: the schedule was computed for five situations (test run, local run, scored run, late start) and gives
  6.5 / 7 / 9.5 and 13 / 14 / 19 minutes; an investigation asked for after its time is refused; a small PDF with
  invented text is read. Not checked in the sandbox yet: a new test run follows this push. Three unit tests in
  `tests/test_kit.py` fail before and after this change; they still target the starter kit's `solve()`.
- Learned: a test run's expiry is far later than a scored run's, so an agent that plans with the platform's expiry
  behaves differently in the two.
- For the others: create your branch (`stream-j`, `stream-i`) as described in AGENTS.md, section "Branches", and
  stop pushing to `main`. Commit messages now start with `J:`, `I:` or `O:`. J: PDFs are readable in the sandbox
  from this commit on.
- Next: start a new test run and read its score breakdown; then make the submission independent of the lead.

## 2026-10-10 14:10 · Work streams described in detail

- Changed: new `docs/streams.md` with, for each of the three streams, the goal, the files only that stream edits,
  what the other streams rely on, the work in order and what "done" means, plus how the streams hand over to each
  other. `AGENTS.md` and `CLAUDE.md` now tell every coding agent to read its stream's section first, and give
  `pyproject.toml`, `uv.lock` and `tests/test_kit.py` to stream O. Nothing under `agent/` was changed.
- Why: the short ownership table was not enough for a coding agent that joins without the conversation behind it.
- Checked: read `agent.py`, `browsing.py`, `browser.py`, `llm.py` and `pyproject.toml` to describe the interfaces
  as they are. Not checked by running: the two gaps named below were found by reading the code.
- Learned: `browser.py` reads PDFs with `pypdf`, which is not a declared dependency, so PDFs cannot be read in the
  sandbox yet. In a test run the platform's expiry is up to two hours away and the agent plans with all of it (read
  from the code, not yet confirmed in a run).
- For the others: J — the PDF gap is listed as your item 2, the dependency comes from O. I — you can start now;
  `facts.py` and `engine/` are yours and nothing depends on the websites. Everyone — read your section of
  `docs/streams.md`.
- Next: baseline score from the running test run, then the time budget cap and the `pypdf` dependency.

## 2026-10-10 14:00 · Entire setup, working agreement, first test run

- Changed: added the Entire setup files (`.entire/` and the agent hook configs at the repository root), the working
  agreement (`AGENTS.md`, `CLAUDE.md`), the root `README.md`, the briefs and notes under `docs/`, and the push logs
  under `docs/log/`. `.gitignore` now also excludes `.env`, `scouting/`, `Voice recording/` and raw transcripts.
  Nothing under `agent/` was changed.
- Why: the EHL submission requires Entire checkpoints in the repository, and three people with coding agents need
  file ownership that keeps them out of each other's way.
- Checked: `atira test-run agent --dry-run` packs the same 15 files of `agent/` as before; the hook configs do
  nothing on a machine without the `entire` CLI. A first sandbox test run of the unchanged agent was started
  (`run_f5cac2142f7736ec`); its result is not in yet.
- Learned: the Atira team page records the repository link only when GitHub redirects back from the app's install
  page, so the link has to be started from the team page's button.
- For the others: run `entire enable` once in your clone so your sessions are attached to your commits. Read
  "Start here" and "Who does what" in `AGENTS.md`; your agent reads it too. Sandbox test runs go through stream O.
- Next: read the test run's score breakdown and set priorities for streams J and I from it.
