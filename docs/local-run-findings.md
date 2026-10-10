# Local run of `stream-o` — what it showed and what to do next

Written Sat 2026-10-10 19:15 by stream O for the team, and first for Julien, who starts the sandbox test runs.
Points and timings only; no values from the websites.

## What was run

- The agent as it is on `stream-o` (code of commit `b98ce09`): lead with reasoning effort "low", recorder "medium",
  sub-agents "xhigh", first-round investigations 3 minutes, later ones 4.
- On one machine, against the public websites with the scouting logins, 18:43 to 19:03. A local run has no
  score: a bid entered with the scouting logins is acknowledged and never counts. It tells what happened, not how
  many points it would get.
- No sandbox run was started and nothing was merged into `main`.

## Timeline

| Second | What happened |
|---|---|
| 39 | Round 1: one investigation per website |
| 219 | All four reports back (the 3-minute limit held) |
| 239 | The lead got the state of the bid. Two of the four recordings had not landed yet |
| 303 | Round 2: three investigations (specification site, tender site, mailbox). None for the ERP |
| 542 | All round-2 reports back (the 4-minute limit held) |
| 540–660 | Waiting for two recordings that had stalled |
| 660 | Automatic submission started: a bid with all five positions left out |
| 1198 | Bid confirmed on the website |

## What works now

- **The second round gets its time.** It started at 5:03 and had 4 minutes. In the last sandbox run of `main` it
  had 76 seconds.
- **The lead decides quickly.** About one minute between the rounds with effort "low" (2.5 minutes with "xhigh").
  It made two model calls in the whole run.
- **The recorder writes the facts.** Seven recordings: positions, quantity changes, one withdrawal, amendments,
  discount approvals, condition groups, keys, reserves, tariffs. The lead wrote none of them.
- **Identifiers and catalogue groups.** All 13 recorded requirements carry a catalogue group (5 different groups),
  and every requirement identifier of the positions is found among them.
- **The governing revision.** With the final facts exactly one revision is marked current and the engine picks it.

## Why no position was priced

There is one blocker, and one thing that feeds it:

1. **The ERP was never searched by catalogue group.** With the final facts the engine's checklist asks for
   exactly that, once per group, and nothing else stands between the facts and a priced position. Only three
   materials were known, from the first-round look at the ERP, and no group counted as searched.
2. **The chain is three steps long and the run has two rounds.** Specification → catalogue groups → ERP search.
   After round 1 the recordings of the tender and the specification had not landed (one recording call stalled for
   75 seconds), so the checklist still asked for the positions and the revisions, and the lead spent round 2 on
   reading those again. The groups were in the facts only after round 2, when the time to investigate was over.

A third finding, separate from the two above:

3. **The submission took 9 minutes** for a bid without positions: 58 steps, three sign-ins after expired sessions,
   one to three fields per `fill_form` call. In the sandbox the same kind of bid was confirmed in 17 seconds
   earlier today, so part of this is the local setup; but filling a whole step in one call is not yet what
   happens on the real form.

Correction to what was said in the chat before this file: the note "the engine could not determine the current
revision" was read from the state after round 2, before the last recording had landed. With the final facts the
revision is found. The blocker is the ERP search.

## What happens next

### Stream O (Orhun's agent) — before any further run

1. **Start ERP searches by catalogue group without waiting for the lead.** As soon as a recording lands and the
   engine's checklist names a search on a website, the investigation for it is started at once, one per group,
   each in its own tab, as long as enough time is left. The lead's rounds stay for everything else.
2. **Shorter limit for a stalled recording call** (75 seconds today), so a stall costs less.
3. Check with a local run that at least one position is priced. Local runs need no queue.

### Julien — sandbox runs

1. **Do not start a sandbox run of `stream-o` yet.** It would price no position, for the reason above, and use a
   slot for 20 minutes.
2. When stream O reports a local run with a priced position, start **one** sandbox run of that state, alone (no
   second run of ours at the same time; all our runs share the model's limit of 32 calls at once).
   ```sh
   git fetch origin
   git merge origin/stream-o            # into the branch you run from
   cd agent && uv run python -m unittest discover -s tests
   atira test-run . --no-open
   ```
3. Note in `docs/log/julien.md`: the run id, the commits that were in the package, quality, and the points per
   field. The command for the points is in `docs/handover-stream-o.md`.
4. In the log of that run, look for: `opened tab` (ERP searches side by side), `facts recorded` (how many, how
   long), `state of the bid:` (positions priced after each round), `time to submit`, and how many fields each
   `[submit] fill_form` carried.

### Julien — stream J

The first-round investigations are 3 minutes long, of which about 2 minutes 15 seconds are on the site. What the
recorder needs from a report, in so many words:

- For the specification: every revision with its date and whether the site marks it current, and for the current
  one every requirement with its attributes and its catalogue group.
- For an ERP search by catalogue group: that the search was by that group, how many results it had and that all
  were read; then for each candidate the attributes, status, stock, lead times and price conditions. A search
  that was not finished belongs under "NOT CHECKED", because only a finished search may count as searched.
- For the tender: the positions with quantity, unit and requirement identifier exactly as written.

### Ivan — stream I

- The checklist worked as intended in this run: it named the four ERP group searches as the next thing to find.
- One request: when a requirement's catalogue group is known and not searched, could the checklist entry carry
  the group and the site role as separate fields (not only inside the text)? Stream O wants to start those
  searches from code and should not have to read them out of a sentence.

### The long diagnostic run (60 minutes)

Orhun's idea: one test run with a generous time limit, to see the score the agent reaches when time is not the
limit, and then to optimise towards 20 minutes. It is worth doing, but only after a local run has priced a
position; today a long run would also end with no position, because the blocker is the order of the work, not the
clock. When it is done:

- from a branch of its own that is never merged and never submitted (a scored run is cut at 20 minutes);
- one run, alone, started by Julien;
- the result is read as "about our upper limit", not as an exact number: one run is one sample.

## Rules that still apply

- No tender references, material numbers, prices, logins or TANs in code, tests, documents or log entries.
- `atira submit` only from `main`, only with Orhun's explicit yes, and only after two test runs in a row have
  signed a bid.
