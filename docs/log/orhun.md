# Push log · Orhun · stream C: lead, submission, runs

Newest entry on top. Format and rules: [AGENTS.md](../../AGENTS.md), section "Push log".

## 2026-10-10 14:45 · Work streams described in detail

- Changed: new `docs/streams.md` with, for each of the three streams, the goal, the files only that stream edits,
  what the other streams rely on, the work in order and what "done" means, plus how the streams hand over to each
  other. `AGENTS.md` and `CLAUDE.md` now tell every coding agent to read its stream's section first, and give
  `pyproject.toml`, `uv.lock` and `tests/test_kit.py` to stream C. Nothing under `agent/` was changed.
- Why: the short ownership table was not enough for a coding agent that joins without the conversation behind it.
- Checked: read `agent.py`, `browsing.py`, `browser.py`, `llm.py` and `pyproject.toml` to describe the interfaces
  as they are. Not checked by running: the two gaps named below were found by reading the code.
- Learned: `browser.py` reads PDFs with `pypdf`, which is not a declared dependency, so PDFs cannot be read in the
  sandbox yet. In a test run the platform's expiry is up to two hours away and the agent plans with all of it; that is
  the likely reason the first test run is still going well past 20 minutes (not confirmed from its logs).
- For the others: A — the PDF gap is listed as your item 2, the dependency comes from C. B — you can start now;
  `facts.py` and `engine/` are yours and nothing depends on the websites. Everyone — read your section of
  `docs/streams.md`.
- Next: baseline score from the running test run, then the time budget cap and the `pypdf` dependency.

## 2026-10-10 14:15 · Entire setup, working agreement, first test run

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
  "Start here" and "Who does what" in `AGENTS.md`; your agent reads it too. Sandbox test runs go through stream C.
- Next: read the test run's score breakdown and set priorities for streams A and B from it.
