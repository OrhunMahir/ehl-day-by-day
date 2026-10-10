# Push log · Julien · stream J: sites and reading

Newest entry on top. Format and rules: [AGENTS.md](../../AGENTS.md), section "Push log".

## 2026-10-10 16:00 · Survey harness, own-site guard, sourced notes, wrap-up report

- Changed:
  - `tests/test_browser_survey.py` (new): runs one sub-agent per website with a generic survey task against the
    scouting logins and writes stats (time, actions, finished, notes, notes with source, refused requests). It is
    skipped unless `SURVEY=1`; reports go to the git-ignored `scouting/survey/`.
  - `browser.py`: every `Tab` records the origin it started on (`Tab.homes`).
  - `browsing.py`:
    - `goto` and `download` refuse addresses of another website of the same run; unknown hosts stay allowed.
    - `note` takes a `source` (document, date, sender, revision/status) and is stamped with site, page title and
      URL.
    - `finish` asks for a FOUND / NOT FOUND report.
    - A sub-agent that runs out of steps or time writes that report from its notes in one tool-less call, with Qwen
      reasoning switched off, a token cap and at most 90 s. If that call fails too, a report is built in code from
      the notes and the visited pages.
    - Reminders after 6 actions without a note and 3 steps before the limit.
    - Each model call is bounded by the deadline.
    - New prompt rule: look for newer versions before relying on a record.
  - `tests/test_browser_scope.py` (new): 10 offline tests on invented hosts.
- Why: in the first local run sub-agents opened other sites' pages in their own browser, reports had no sources,
  and three of four sub-agents stopped at the step limit with no usable report. One of those had noted only an
  older, superseded record.
- Checked:
  - Unit tests: the 10 new ones pass. The 3 errors in `tests/test_kit.py` are older (the starter test still expects
    `solve()`).
  - Two four-site surveys with Qwen (40 steps per site). Notes with a source: 15/15 and 23/23. Sub-agents that
    finished: 1/4, then 2/4. Notes on the ERP: 1, then 4.
  - The mail sub-agent now finds the governing approval and labels the older one and the look-alikes as such.
  - A forced cross-site task was refused 3 times and the item was reported under NOT FOUND.
  - The wrap-up report was checked live.
- Learned:
  - Qwen reasons before answering. With a token cap the reasoning can use up the whole cap, and the answer then
    comes back empty. Reasoning can be switched off per call (`enable_thinking: False` via `extra_body`).
  - Qwen calls sometimes take minutes, probably when the team's 32 shared slots are full.
  - The shared scouting mailbox collects signing-code mails from everyone's runs.
- For the others:
  - O: `Tab` and `BrowsingAgent` keep the same constructor and `run()` return type.
  - O: reasoning tokens are a large share of time and cost per call; switching reasoning off where it isn't needed
    is worth testing in the lead.
  - O: `tests/test_kit.py` fails on `main`.
  - I: nothing.
- Next: Step 2: re-login when a session expires, retry error pages, show open dialogs first in the page view.
