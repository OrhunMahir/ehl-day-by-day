# Push log · Julien · stream J: sites and reading

Newest entry on top. Format and rules: [AGENTS.md](../../AGENTS.md), section "Push log".

## 2026-10-11 01:30 · stream-j-tweaks: amounts as written, setup fees per position, no hasty no bid

- Test run `run_e7b86a346e4fe041` (`7f5e89d`): quality 73.1, eligible, 13:29. Position 01.01 completely right;
  materials, quantities and packaging right everywhere; discount right. Lost points:
  - a price per 100 pieces recorded a thousand times too low (two base prices, the bonus split, four unit prices,
    the total);
  - one-time setup fees from Q&A had no field (two surcharges);
  - a stock that included inspection stock (one lead time).
- Changed:
  - `board.py`: a money amount given as a number with three decimals or as text like "64.853" is refused with the
    reason; masses with a unit are converted to kg.
  - `facts.py`: `TenderPosition.setup_fee_eur`.
  - `engine/bid.py`: the setup fee is added to the ordinary setup fee.
  - `engine/bid.py`: when a requirement as amended by Q&A matches no material, but the specification's own values
    do, the position is bid with that material instead of the whole bid becoming a no bid. A local run had
    recorded a withdrawn amendment a second time under another answer.
  - `simple.py`: setup fees go to their field, stock is the free stock.
- Checked:
  - 225 unit tests pass (1 skipped); the new ones fail on the previous code.
  - In a local run, prices came out right per piece, the setup fees of two positions were in their field, and
    stock and masses were right.
  - The facts of that run, priced again with the new engine: a bid with the materials that scored before, the
    corrected base prices, surcharges and lead time (it had been a wrong no bid).
- For the others: I: the setup fee field and the no-bid rule are in your files on this branch only.
- Next: sandbox test of this commit.

## 2026-10-10 23:40 · stream-j-tweaks: a website that hangs gets a fresh session; the submission checks nothing

- Test run `run_550974275152686b` (`0492559`) was cancelled at 12:43 (quality 0).
  - The supplier portal never loaded during the whole run; every page view took over 60 s, because each one asked
    for the hung load again.
  - The submission agent then spent minutes in the mailbox checking a discount mail instead of entering the bid.
- Changed:
  - `browser.py`: a page view never asks for a load again; while one hangs it says "still loading … for N s" and
    reads for 2 s only. `goto` makes 2 attempts of 20 s. `Tab.reopen` opens a website in a fresh session (new
    browser context, in any browser).
  - `browsing.py`: after 2 page loads in a row without an answer, the agent's website is opened in a fresh session,
    in its own browser first, then in another of the run's browsers; at most twice. `simple.py` passes the run's
    browsers.
  - `submission.py` (stream O's file): the answer is final; the agent opens another website only for what the form
    needs, such as the signing code.
- Checked:
  - 220 unit tests pass (1 skipped), among them: a page view returns in under 5 s while a load hangs; a website that
    stops answering opens again in a new context; two unanswered loads bring the fresh session.
  - Local run with Qwen:
    - The bid was complete at 406 s (every rule mail recorded, all five positions with confirmed materials), so
      investigations ended early.
    - No website hung.
    - Signed at 694 s; the mailbox was used only for the signing code.
  - Still wrong in that run: one price copied as "64.853" (ambiguous) and a mass in grams read as kilograms.
- For the others: nothing changes on `main`.
- Next: sandbox test of this commit; then the two number errors.

## 2026-10-10 22:55 · Branch stream-j-tweaks: prices as written, every rule mail, until minute 11

- Changed (`stream-j-tweaks`, from `stream-j-simple`):
  - `simple.py`: agents record every price, amount, rate and mass as text exactly as the page writes it; the facts
    module reads the German format.
  - The job names every kind of rule mail the bid needs (end reserves, exchange rates, metal-index windows,
    packaging tariffs, setup pools, bonus bands, condition-group rules, delivery commitments).
  - Every page and validity period of a material's price conditions is read.
  - Investigations run until minute 11 at the latest (was 9) and end as soon as the engine has nothing left to
    find.
  - `board.py`: up to 30 open items per update; `complete()` for the early end.
- Why: what the engine missed in the 51.2 run (`run_9b8aee1022f379bc`):
  - no rule mail was recorded at all, so there was no exchange rate, metal index, packaging, setup pool, bonus or
    condition-group rule;
  - "186.615,00" per 100 pieces was recorded as 186.615, so four base prices were a thousand times too low;
  - the run ended after 11:44 with 8 minutes unused.
- Checked: 218 unit tests pass (1 skipped), among them a German price kept at its value and the early end. Local run
  and sandbox test: see the next entry.
- For the others: nothing changes on `main`.
- Next: local run with Qwen, then a sandbox test if it looks better than the 51.2 run.

## 2026-10-10 22:30 · Branch stream-j-simple: the board concept (best test run 51.2)

- Changed: new branch `stream-j-simple` (from `stream-j` at `31f8933`). One browsing agent per website, connected
  only by a shared board of typed facts (`board.py`, `simple.py` as the entry point); no lead model, no recorder, no
  hand-offs. The engine always bids every position with what is known. Details: `docs/branch-stream-j-simple.md`
  (on `main` via pull request #10).
- Why: every earlier state scored 10; the information was lost or late at the hand-offs between models.
- Checked: 215 unit tests pass (1 skipped). Test runs: `run_930993c7fb3173ed` (`91893e5`) quality 5 (ERP never
  answered, groups wrongly marked as searched, fixed in `92456f6`); `run_9b8aee1022f379bc` (`92456f6`) quality 51.2
  (every position with material, quantity and lead time; prices mostly missing). Two local runs signed a bid.
- Learned: one session per website ends the logins that threw each other out; a fact recorded at once reaches the
  other agents immediately.
- For the others: O, I: this branch replaces the lead and the recorder for its runs; `agent.py` and the engine are
  reused. Nothing is merged into `main` except the notes file.
- Next: prices (every page of the conditions, price units, discount), investigating longer.

## 2026-10-10 20:15 · Test run run_a1424b6668fbc7f4; the submission no longer goes round in a circle

- Test run `run_a1424b6668fbc7f4`, package `stream-j` at `8b7a460`: quality 10 (decision 5, tender reference 5),
  eligible, 18:38, $0.27.
  - The ERP was searched by all four catalogue groups in the second round, and 7 materials and 4 searched groups
    reached the facts.
  - One position had a matching material. In the configurator the submission agent filled the conditions step,
    then clicked a number it remembered from an earlier page. On that page the number was the back button. It
    then pressed "leave position out" on the page before and arrived at the same step again, about 30 times,
    until its time was up. The second attempt signed a bid without positions.
  - The browser windows that looked stuck (the ERP at its sign-in page, the webmail behind its notice pop-up) were
    investigations the lead started a few seconds before investigations ended. They opened a page and stopped.
  - The platform's stored log of this run starts at 501 s; the earlier lines are gone.
- Reproduced with the scouting logins, using a harness outside the repository that runs the agent's own
  submission step:
  - The configurator selects the material in a cascade: a selection, then a button that applies it, and only
    then is the next selection enabled. The agent could not get past it and gave up after 7 minutes.
  - The webmail sometimes leaves a page request unanswered for over 30 s; a new request is answered at once.
- Changed:
  - `browser.py`: a page request still unanswered after 8 s is made again, and a page that cannot be read at all
    is loaded again. `goto` gives a load 15 s instead of 30 s. An element named by its text is a button or link
    with exactly that name before any text that only contains it.
  - `submission.py` (stream O's file, for Orhun to confirm):
    - `fill_form` can press a button between fields (`{"click": "<text>"}`), so a cascade fits into one call.
    - A disabled selection says what usually enables it.
    - Every click result names the button it pressed.
    - The same action that leads back to the same page a third time gets a note that the agent is going round in
      a circle.
    - The prompt says that element numbers belong to one page view, and that buttons and fields are best named by
      their text.
  - `agent.py` (stream O's file, for Orhun to confirm):
    - No investigation starts with less than 120 s left.
    - The submission task lists each position's material with its bore, stroke, pressure, mounting and
      certification from the ERP, so the agent can choose them in such a cascade.
  - Tests in `tests/test_browser_pages.py`, `tests/test_submission.py` and `tests/test_lead.py`. The new ones fail
    on the previous code.
- Checked:
  - 202 unit tests pass (1 skipped).
  - With the scouting logins, the agent's own submission step entered a bid with one fully priced position and
    four left out. It chose the material through the cascade, signed with the TAN from the webmail and got the
    receipt in 383 s. Before these changes, the same step stopped after 7 minutes without a signature.
- For the others: O: please look at the `submission.py` and `agent.py` changes above. I: nothing.
- Next: sandbox test run of this commit.

## 2026-10-10 19:10 · Test run run_969ce72f14a980ac; the ERP search is asked for as soon as the groups are known

- Test run `run_969ce72f14a980ac`, package `stream-j` at `2a030cd`: `main` + `stream-o` up to `3a0362e` +
  `exp-speed` + the entry below. Quality 10 (decision 5, tender reference 5, every position 0), eligible, 11:48,
  $0.17.
  - The lead's turns took 20 s and about 30 s. The second round started at 231 s instead of after investigations
    had ended, and got about 195 s on the sites.
  - All 9 reports were recorded (the largest in 158 s, after one call over 75 s).
  - At the end the facts held the positions, Q&A changes, withdrawals, 38 keys per position, the rule mails and
    the discount approvals.
  - No ERP search by catalogue group was ever sent. When the lead planned the second round (231 s), the
    specification's groups were recorded, but the positions were not yet (343 s). The checklist asks for a group
    search only per recorded position whose requirement is complete, which for usable travel also takes the
    end-reserve mail, so `needed` had no ERP entry. The lead without thinking follows `needed`. At 613 s the
    checklist named all four groups, after investigations had ended at 540 s.
- Changed: `engine/needs.py` (stream I's file, for Ivan to confirm): as soon as the current specification revision
  is recorded, every catalogue group of its requirements that has not been searched is a "find" entry for the ERP.
  It does not wait for the positions or for every attribute of the requirement. Positions recorded later add
  themselves to the same entry; with positions known, only the requirements they use count. Two tests in
  `tests/test_engine_needs.py`; the first fails on the previous code.
- Checked: 194 unit tests pass (1 skipped).
- Learned: with the run's 20 minutes, the run ended after under 12 minutes. Signing a bid without positions took
  97 s, so time is left over once positions are priced.
- For the others: I: please look at the `needs.py` change. O: the second round now depends on `needed` naming the
  ERP searches early; the prompt's "one ERP investigation per catalogue group" alone did not make the lead send
  them.
- Next: test run of this commit; the points per field and when the ERP investigations start.

## 2026-10-10 18:45 · Test-run state: stream-o and exp-speed merged; reports keep what was read last

- Changed:
  - Merged `stream-o` (up to `3a0362e`, the hand-over) and Ivan's `exp-speed` (`a6ef420`) into `stream-j` for
    the next sandbox test run. The only conflict was `agent/agent.py`: both branches gave the recorder a model of
    its own. Kept stream O's names (`recorder_model`, `RECORDER_REASONING`, `open_site`) and exp-speed's settings:
    sub-agents, submission agent and recorder without thinking; the lead at "low" with 4000 tokens and 120 s per
    call, asked again on a stall.
  - `agent/agent.py` (stream O's file, for Orhun to confirm):
    - A recording call may take 120 s instead of 75 s, a whole recording 240 s instead of 150 s. Without thinking
      the recorder writes about 40 tokens per second; a report with many records needs 3000–4000 tokens, which 75 s
      cut off on every attempt.
    - The lead without thinking, up to 8000 tokens and 180 s per call, and told to keep each investigation task to
      about 80 words. In a local run with exp-speed's lead setting ("low", 4000 tokens, 120 s) the turn after the
      first round took 118 s and was cut off inside its tool calls (an empty `add_facts`, an `investigate` without a
      task), the next one hit 120 s, and the second round was sent at 560 s, after investigations had ended.
  - `browsing.py`: a model call without thinking counts as stalled after 30 s (75 s with thinking) and is retried
    up to five times while the deadline allows. The wrap-up report is limited to 1000 tokens and names noted facts
    instead of repeating them (the notes are appended anyway); 2000 tokens took about 50 s and the 45 s reserve
    cut 3 of 8 wrap-ups off. An agent that stops without `finish` passes on the results of its last two actions
    after its last note, so a page read just before the deadline is not lost.
  - Tests in `tests/test_browser_scope.py` for all three.
- Why: in the two quality-10 runs every one of 17 investigations stopped without `finish`; in one of them the
  specification's requirement table was read at the last step and never reached the facts.
- Checked:
  - 192 unit tests pass (1 skipped). The three new stream-J tests fail on the previous `browsing.py`.
  - Local run with the scouting logins and exp-speed's lead setting:
    - First round: about 135 s on each site. All four wrap-up reports were written in time, and the ERP report
      used the new "not noted yet" part.
    - All four reports were recorded, the largest in 123 s.
    - Failure: the lead's turns are what is described above, and the bid would again have had no positions.
  - Second local run with the lead without thinking: its first turn took 14 s instead of 65 s. Stopped there, to
    test in the sandbox instead.
  - Sandbox test run: see the next entry.
- Learned: see the analysis of the two runs in this session; the time goes into thinking tokens and into rounds
  that wait for each other.
- For the others: O: the merged settings above are on `stream-j`; please confirm the recorder time limits. I:
  nothing changed in your files beyond exp-speed.
- Next: the score breakdown of this state's sandbox test run.

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
