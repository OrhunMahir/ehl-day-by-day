# Stream O — changes to test together with yours

Written Sat 2026-10-10 18:20 for Julien, who starts every sandbox test run from now on. One person starts runs, so
that no state is run twice; this file says what stream O changed, where it is, what to look at in the run, and what is not proven yet.

## Where the changes are

- `main` already has stream O up to pull request #9 (17:09).
- Three more commits are on `stream-o` only. They touch `agent/agent.py`, `agent/tests/test_lead.py` and files under
  `docs/`, nothing else:

  | Commit | What |
  |---|---|
  | `b54879f` | A website that does not answer at the start no longer ends the run |
  | `d9c00ce` | The lead thinks with reasoning effort "low" |
  | `b98ce09` | Short narrow first round, exact identifiers in the recording, recorder with its own model |

To test them with your own changes, bring them into your branch:

```sh
git fetch origin
git merge origin/stream-o
cd agent && uv run python -m unittest discover -s tests   # all tests pass on stream-o, 1 skipped
```

Stream O only edits its own files, so the merge should not conflict with stream J or stream I.

## What changed, in the order a run goes through it

### Start of the run
- Every website is opened with a 20-second limit and up to three attempts. A site that still does not open keeps
  its tab; the sub-agent that works it opens the address again. Before, a timeout here ended the run after 44
  seconds with exit code 1 and no model call.

### Investigating
- **Several investigations per website.** Up to four work the same site at once. The first uses the tab that is
  there; each further one gets a tab in a browser context of its own inside the same browser and signs in there.
  The lead is told to split a large job on one site, e.g. one ERP investigation per catalogue group.
- **Two rounds with different lengths.** An investigation of the first round is ended after 3 minutes, a later one
  after 4. The sub-agent stops browsing 45 seconds before its end to write its report, so a first-round
  investigation has about 2 minutes 15 seconds on the site.
- **Narrow first jobs.** The lead is told to give every site one narrow first job (the tender's positions with
  their requirement identifiers and all clarifications; the current specification revision with every
  requirement's attributes and catalogue group; the messages that name this tender reference; in the ERP only how
  to search by catalogue group) and to keep the ERP searches by group for the second round.
- Investigations end at 45 % of the run's time (minute 9 of 20). A step that hangs is cut off 30 seconds after the
  investigation's end; the notes taken so far are kept.

### From reports to facts
- **The lead no longer writes the facts.** When a report comes back, a separate model call (the "recorder") puts
  its facts into a `facts.FactsBuilder`, several reports side by side. A stalled recording call is asked again
  after 75 seconds; a bad update is shown to the recorder to correct.
- The recorder is told to write identifiers character for character (positions, requirements, revisions,
  materials, catalogue groups), to record every requirement's catalogue group, and to put a group into
  `searched_groups` only when the report says the ERP was searched by that group and every result was read.
- After a round the lead waits up to 20 seconds for pending recordings and gets the state of the bid as the engine
  prices it: answer, `issues`, `needed`, `incomplete`, `omitted`, `complete`. It uses `add_facts` only for
  corrections. `authority` is not offered to the model.

### Deciding
- **"No bid" is not taken hastily.** A no-bid from the engine is submitted only when the catalogue group of every
  unmatched position is in `searched_groups` and the checklist has nothing to find for it. Otherwise those
  positions are left out and the rest is bid.
- When the time to investigate is over, pending recordings are awaited (as long as 7 minutes stay for the
  submission), the facts are priced in code and the answer is submitted without asking the lead again. Without any
  facts the lead is made to add them; after two failed attempts it has to hand in an answer of its own.

### Submitting
- `agent/submission.py` is a submission agent of its own. One `fill_form` call sets all fields of a form step in
  order and clicks the button that continues. It waits for dropdowns whose options load after an earlier choice
  and finds a field again when the page rebuilt it.
- **Fields that are not on the page yet** can be named by their label instead of an element number; they are
  looked up when their turn comes. The button can be named by its text.
- At its start it reads only the submission website (30-second limit); the other sites are opened when the model
  asks. A model call that has not answered after 45 seconds is dropped and asked again. In the last 150 seconds it
  is told to stop filling optional fields and to sign and confirm.
- After each attempt the lead asks the platform whether the run is still open (three times, 2 seconds apart) and
  submits again while it is. A model error after a submission ends the run quietly.

### End of the run
- Everything is stopped 5 seconds after the run's time, whatever is still waiting on a page or a model.

## Settings as they are on `stream-o`

| Setting | Value | File |
|---|---|---|
| Run length planned with | 20 minutes, in every kind of run | `agent.py` |
| Investigations end / pricing and submission start | 45 % of the time | `agent.py` |
| One investigation, first round / later | 180 s / 240 s | `agent.py` |
| Investigations per website at once | 4 | `agent.py` |
| Reasoning effort: lead / recorder / sub-agents and submission | "low" / "medium" / "xhigh" | `agent.py` |
| One recording: in total / per call / the lead waits | 150 s / 75 s / 20 s | `agent.py` |
| Time kept for the submission | 420 s | `agent.py` |
| Submission agent: one model call / one tool call | 45 s / 90 s | `submission.py` |
| Any model call | 240 s | `llm.py` |

Note: `main` has "xhigh" for the lead too, and no recorder setting; the two "low" and "medium" values are on
`stream-o` only.

## What the sandbox runs have shown so far

Points only; no values from the websites.

| State | Runs | Result |
|---|---|---|
| Engine wired in, first version | 1 | Accepted, quality 5: a wrong "no bid" |
| + no hasty no-bid, 4-minute rounds | 5 | One accepted with quality 18.3 (one position: quantity and project bonus right, material wrong); one hung in the submission; three ran out of time before the signature (two of them with two positions priced, one after the lead took 8 minutes to write the facts) |
| + reasoning "low" for the lead, or for all | 2 | Both accepted, quality 10 (all positions left out) |
| + reasoning "high" for the lead, or for all | 2 | Both ended without a bid; not analysed |
| `main` after PR #8, and with tabs per site | 2 | Both accepted, quality 10 |
| `main` after PR #9 | 2 | Both accepted, quality 10, in 15 and 16 minutes |
| Same, one more run | 1 | Ended after 44 seconds: the mailbox did not open at the start (fixed in `b54879f`) |

Quality 10 is decision 5 plus tender reference 5: a signed bid without any position.

Read from the log of one quality-10 run on `main` after PR #9:
- Recording, tabs and submission worked: twelve recordings, nine investigations side by side in the second round,
  the submission took 17 seconds.
- No position was priced because the second round had 76 seconds. The first reports were back at 280 s; the lead's
  next turn ended at 465 s; investigations end at 540 s.
- The specification's requirements were recorded without a catalogue group, the positions' requirement identifiers
  did not match them, and no material reached the facts.

The three commits on `stream-o` are the response to exactly that. They have not run in the sandbox.

## What to look at in the next run

In the run's log (`atira logs <run>`), lines to search for:

| Line contains | Tells you |
|---|---|
| `[start]` | A website did not open at the start and was tried again |
| `opened tab` | A further tab was opened for a site; `no further tab` means it could not be |
| `facts recorded in` / `facts not recorded` / `recording call took too long` | How the recorder did, per report |
| `state of the bid:` | What the engine priced after a round and what it still asks for |
| `time to submit` | When the automatic submission started |
| `[submit] fill_form` | How many fields went into one call |
| `stopped at the end of the run's time` | The hard stop had to end the run |

Questions the run should answer:
1. When does the second round start, and how long does it get? (Target: start before minute 5, at least 3 minutes.)
2. Do the recorded requirements carry catalogue groups, and do the positions' requirement identifiers match them?
3. Do ERP investigations by group run side by side, and do materials reach the facts?
4. How many positions does the engine price, and does the form still reach the signature with them? Runs with 0
   or 1 positions signed in time; runs with 2 did not, before the label-based form filling existed.

The points per field of a finished test run (points only):

```sh
curl -s -H "x-api-key: $(atira token)" -H "User-Agent: day-by-day-dev/0.1" \
  https://ehl-challenge.atira.ai/v1/runs/<run id> | python3 -c "
import json, sys
d = json.load(sys.stdin); b = d['score']['breakdown']
print('quality', d['score']['quality'], 'eligible', d['score']['eligible'], 'seconds', d['score']['duration_ms'] // 1000)
for k in ('decision', 'tender_reference', 'discount_percent', 'total_net', 'total_consistency'):
    print(k, b[k]['points'])
for i, p in enumerate(b['positions'], 1):
    print('position', i, {k: v['points'] for k, v in p.items() if isinstance(v, dict)})"
```

## Not proven yet

- The three commits on `stream-o`, in the sandbox.
- Whether the ERP accepts several sessions of the same login at once (the tabs-per-site change ran once and was
  accepted, but that run priced no position).
- A bid with two or more positions reaching the signature.
- Whether 3 minutes are enough for a first-round job on the real websites.
- The recorder on real reports with effort "medium": it was checked on invented reports only (two reports side by
  side in 19 seconds, identifiers exact, every requirement with its catalogue group).

## Rules that still apply

- No tender references, material numbers, prices, logins or TANs in code, tests, documents or log entries. The
  repository is public and a reviewer agent reads every package.
- `atira submit` only from `main`, only with Orhun's explicit yes, and only after two test runs in a row have
  signed a bid.
- Findings from the run go into your push log (`docs/log/<name>.md`), points and timings only.
