# Push log · Orhun · stream C: lead, submission, runs

Newest entry on top. Format and rules: [AGENTS.md](../../AGENTS.md), section "Push log".

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
