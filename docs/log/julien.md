# Push log · Julien · stream J: sites and reading

Newest entry on top. Format and rules: [AGENTS.md](../../AGENTS.md), section "Push log".

## 2026-10-10 20:45 · Review fixes from stream I

- Changed: all seven findings from Ivan's review (`docs/log/ivan.md`, 16:29):
  1. `observe.js`: the cleaning pattern uses escapes instead of raw invisible characters. A scan of every package
     file for zero-width, direction-mark and soft-hyphen code points now finds none.
  2. `browser.py` reads `observe.js` as UTF-8 explicitly.
  3. `observe.js`: password fields show only `value=<filled>` or `value=<empty>`, never their value.
  4. `browser.py`: a stray closing `script`/`style` tag no longer hides the rest of a document's text.
  5. `browser.py`: an HTML download renders the bytes it already fetched instead of requesting the address a
     second time.
  6. `browsing.py`: calls sent in one turn run before `finish`, so a note sent with `finish` is kept.
  7. `browsing.py`: an expired session is detected only when a sign-in form appears (password plus user-name field,
     or a sign-in address) on a page that had no password field before. A code field alone no longer triggers a
     re-login.
  - Tests for 3–7 in `tests/test_browser_*`, invented pages only.
- Why: the review found these in stream J's files. Raw direction marks read as obfuscation to a reviewer, a
  password in the page view reaches the prompt and the logs, and the others lose text or notes, or repeat requests.
- Checked:
  - 161 unit tests run, including stream I's engine tests. The only failures are the 3 known ones in
    `tests/test_kit.py`.
  - The five new tests fail on the previous code and pass now.
  - Short live ERP survey: finished by itself in 100 s, no false re-login, every note with a source.
- For the others: I: thanks for the review. O: nothing changes in the interfaces.
- Next: the score breakdown of a sandbox test run with all stream-J changes.

## 2026-10-10 20:10 · Generalisation and fewer steps: ARIA grids, shadow DOM, read_pages, popups

- Changed:
  - `observe.js` (step 5, generalisation): tables built from elements with ARIA roles (`grid`, `row`, `cell`)
    render as rows; content inside open shadow roots (web components) is read; more control roles are numbered
    (`combobox`, `treeitem`, `textbox`, `slider` and others).
  - `browser.py`:
    - New `read_pages`: up to 6 pages the model chose, rendered in parallel background tabs and returned in one
      step (step 6).
    - Popups: only popups this tab opened are followed. When a popup closes itself, the tab returns to the page
      that opened it. If the active page closed for any other reason, it falls back to an earlier page or reopens
      the site.
  - `browsing.py`: the `read_pages` tool (addresses of other websites are refused one by one); the page view
    before the newest is cut to 4000 characters.
  - Tests: ARIA grid, shadow DOM and combobox; `read_pages`; a popup that closes itself; view shortening.
- Why:
  - Step 5: the private dataset uses different software. A string search found nothing tied to the public sites
    in stream J's files, so the work went into page structures the public sites do not use.
  - Step 6: sub-agents spent most turns opening pages whose links they could already see.
  - In one survey the ERP sub-agent's tab stayed on a popup that had closed itself, and every later action failed.
- Checked:
  - 25 stream-J tests pass. The popup test fails on the previous `browser.py` with the same error the survey
    showed, and passes now.
  - Survey 5 (with `read_pages`): all four sub-agents finished by themselves for the first time; the mail sub-agent
    noted 11 facts instead of 4.
  - Survey 6: no closed-page errors, and the ERP report reaches the price scales.
- Not as hoped:
  - Survey 6 finished only 1/4 by itself. Five model calls stalled (75 s each), so the two surveys are not
    comparable on finishing.
  - Input tokens per call fell only about 3 % (12.0k to 11.7k); the brief and the newest view dominate.
  - `read_pages` raised the total input tokens of a survey by about 30 %, because more page content is read.
- Learned:
  - Survey results vary strongly with Qwen load; compare several runs before drawing conclusions on speed.
  - Websites open helper popups that close themselves after a choice.
- For the others: O: sub-agents receive the lead's full brief as context. Giving them only the task part would
  shrink every call, though the brief is a stable, cached prefix. I: nothing.
- Next: steps 0–6 are done. Next is a full local run of `agent.py` and the score of a sandbox test run (through O).

## 2026-10-10 19:10 · Versions, look-alikes, and two kinds of "not found"

- Changed: `browsing.py`:
  - `note` takes structured source fields shaped like `facts.Source`: `document`, `date`, `sender`, `revision`,
    `status` and `reference` (free `source` text stays optional).
  - Reports and the wrap-up now have four parts: FOUND; SUPERSEDED / LOOK-ALIKE; CHECKED, NOT PRESENT (with
    search evidence); NOT CHECKED.
  - Prompt rules:
    - Find all versions and say which one is marked current and on what basis.
    - Check every record's reference character for character, its sender and its date against the scenario date.
    - Report something as absent only after a complete search.
  - Test for the source fields in `tests/test_browser_scope.py`.
- Why: a sandbox test run scored 5 of 100 because the lead answered no_bid after the ERP data never arrived; "not
  found" was read as "does not exist". The bid engine's `resolve.searched()` also counts a catalogue group as
  searched as soon as one candidate is known, so reports must say how complete a search was.
- Checked:
  - 13 scope tests pass.
  - Live survey with Qwen on the mailbox and the supplier portal, compared with what is on the websites:
    - The governing record is under FOUND with all source fields.
    - Look-alike references, drafts and a record dated after the scenario date are under SUPERSEDED /
      LOOK-ALIKE.
    - The current revision is named with its basis, and the older revisions are listed as superseded.
    - Every note has a source.
- Not checked: the mail sub-agent ran out of steps before the archive and the second result page. It listed the
  result page under NOT CHECKED but not the archive folder.
- Learned: a record dated after the scenario date is easy to miss without an explicit date check; with the rule
  the model rejected one.
- For the others:
  - O: use the four parts. Only CHECKED, NOT PRESENT supports a no_bid; NOT CHECKED means unknown.
  - I: `resolve.searched()` would be safer if a group counted as searched only when a report confirms the search
    was complete.
- Next: Step 5 (generalisation check), then Step 6 (fewer steps per report).

## 2026-10-10 18:30 · Paged lists, documents, model stalls, wrap-up inside the deadline

- Changed:
  - `browser.py`:
    - New `collect_pages`: follows a "next page" or "load more" control the model chose, and returns the views
      joined.
    - Frames that load late (e.g. after a collapsed section opens) are waited for, at most 3 s.
    - `download` now fetches inside the page (same cookies, proxy and network as the browser), with Playwright's
      own client only as a fallback.
  - `browsing.py`:
    - The `collect_pages` tool.
    - Each model call is capped at 75 s and retried up to 3 times while the deadline allows.
    - Browsing stops 45 s before the deadline, so the wrap-up report fits inside it; the wrap-up is shorter.
    - Prompt rules: use `collect_pages` for lists; refine a search that shows nothing; open a record's tabs or
      registers before reporting a value as missing.
  - Tests: `test_browser_pages.py` covers paging, "load more", a late frame, and ZIP and PDF downloads;
    `test_browser_scope.py` covers a stalled model call.
- Why: in a full local run of `agent.py` the lead submitted with every position omitted. The ERP sub-agent lost
  about five minutes to a single stalled Qwen call and hit the deadline. Two wrap-up reports timed out because they
  started after the deadline. The download fix came out of a test: Playwright's own HTTP client does not go through
  the page's network, so in the sandbox it would probably miss the browser's proxy.
- Checked:
  - 21 stream-J tests pass.
  - ERP-only survey with Qwen: one stall was cut at 75 s and the retry answered in 2.5 s. The sub-agent passed the
    ERP's entry gate (slider, checkbox, receipt number, acknowledgement) and reached materials, stock and
    conditions.
  - Every ERP note has a source.
  - Re-login confirmed live in the full run: after the session expired the mail sub-agent signed in again, cleared
    the consent page and went back to the archive page it was reading.
- Not fixed: the ERP sub-agent still runs out of steps before opening every sub-view. Prices sit in a separate
  register of each condition record. The new prompt rule is not yet checked live.
- Learned:
  - Some websites gate the whole application behind a notice page that needs several controls. A model gets past
    it when it is given the time.
  - Price amounts can be one click deeper than the record that names them.
- For the others: O: a sub-agent now always returns within its deadline. The full run showed the submission
  sub-agent ending after 2 steps for a bid without positions. I: nothing.
- Next: Step 4 (versions and look-alikes in reports), then fewer steps per report.

## 2026-10-10 17:40 · Sessions, errors, dialogs, no more hangs

- Changed:
  - `browsing.py`:
    - After a successful `login`, an action that lands on a sign-in form signs in again by itself and returns to
      the page before the action.
    - Every browser action is bounded (60 s, never past the deadline), and so is the first page view; a stuck
      action reports "did not finish" to the model.
  - `browser.py`:
    - The HTTP status of each page load is recorded; a 5xx page is reloaded up to twice, and the page view says so.
    - Reading each frame is bounded to 10 s.
  - `observe.js`: open dialogs and modals are shown first in the page view, marked as in front of the page, and
    not repeated below.
  - Tests:
    - `tests/test_browser_pages.py` (new): headless Chromium against an invented site served by request routing,
      no network. Covers re-login, the 5xx reload, dialogs first, and hidden decoys and tooltips.
    - `tests/test_browser_scope.py`: a hanging browser action no longer holds the agent.
    - `tests/test_browser_survey.py`: also counts re-logins.
- Why: the brief warns of expiring sessions, flaky error pages and popups. In one survey a sub-agent hung inside a
  single browser action for eight minutes and held up the whole run.
- Checked:
  - 15 unit tests pass.
  - Four-site survey with Qwen: 3/4 sub-agents finished by themselves (best so far), 21/21 notes with a source, no
    hangs. The fourth was stopped by the time limit, and its wrap-up report says plainly what it did not read.
  - The 5xx reload worked live on one website right after the consent page.
- Not checked live:
  - Re-login: no session expired during the 5-minute surveys.
  - Dialogs first: reports do not contain page views, so this is verified only by the offline test.
  - The original hang did not reproduce on demand, so the fix rests on the bound and its unit test.
- Learned:
  - Websites can answer 5xx right after a form step and work on a reload.
  - A Playwright read of a frame that is mid-navigation can wait without limit, so every read needs a bound.
- For the others: O: nothing changes in the interfaces; sub-agents now always return before their deadline plus
  the wrap-up (at most 90 s). I: nothing.
- Next: Step 3, documents and lists (PDF, ZIP, frames, paging, "load more", over-broad searches).

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
