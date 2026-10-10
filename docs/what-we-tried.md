# What we tried after the 18.3 submission, and what did not work

Written 2026-10-10 20:40 by Ivan (stream I) for the team. Points, counts and times only; no values from the sites.

## The numbers

| Run | Code | Quality | Points per field |
|---|---|---|---|
| Scored submission `sub_9badfc2e8f538bf3` (13:49 UTC) | `agent/` at `270b072` (stream-o, "wire the bid engine into the lead"); every file matches the package | **18.3** | decision 5, tender reference 5, discount 5, one position 3.25, other positions 0 |
| 7 sandbox test runs, 14:48–17:38 UTC | `main`, `stream-o`, `exp-speed`, `stream-j` at different commits | 10 each | decision 5, tender reference 5; discount 0, every position 0 |
| 2 sandbox test runs | `stream-o` | failed | 0 |
| Sandbox test run `run_57f15cb68ad9f88a` | `plan-facts-at-source` at `24c36ea` | **5** | tender reference 5; no position answered |

What the 18.3 has over the 10s is one field (the discount) and part of one position: one run, so part of it can be
luck. But the discount was right in it and wrong in every run after it, which points to a regression.

## What we tried

| Change (who) | Meant to fix | What happened |
|---|---|---|
| A recorder model turns each sub-agent report into facts in the background (O) | the lead spent its turns typing facts | a recording took 75–240 s per report; a report cut off by the time limit lost its facts |
| Several investigations per website, each in its own tab (O) | more reading in parallel | more model calls at the same time, more stalled calls on the shared proxy |
| Lead model with high reasoning effort | better plans | turns up to 339 s and 12k-token answers |
| Reasoning off, shorter calls (`exp-speed`, I) | speed | lead turn 65 s → 14 s; still quality 10 |
| Browser robustness: `read_pages`, ARIA grids, popups, retries after a stalled call, short wrap-up (J) | reports cut off, hangs | fewer cut-off reports; the ERP search still came too late |
| The checklist asks for the ERP search per catalogue group as soon as the groups are known (J, `needs.py`) | no ERP search was ever sent | it was asked for, but after the investigations had ended |
| Facts recorded by the browsing agents while they read; a dispatcher starts small tasks from the open needs (`plan-facts-at-source`, I) | rounds that wait for each other | locally the ERP group searches started 67 s after the groups were known and materials came with attributes, the furthest we got; in the sandbox quality 5, and new bugs |

## Why it did not work

1. **Every change added moving parts.** More agents, more model calls at the same time, more hand-offs. Each brought
   a new failure: stalled calls on the shared proxy, a recorder that wiped the facts (`replace_lists`), empty values
   that overwrote known ones, whole updates rejected because of one unknown field.
2. **The task is a chain.** Specification → catalogue group → ERP search → candidate details → rule mails → price.
   Each step needs the one before. Parallel agents do not shorten a chain; they add waiting and coordination.
3. **Report → facts in two steps loses information and minutes.** In every version the recording or the hand-off
   was the slow part.
4. **The submission is slow and its time varies.** 97 s in one sandbox run, more than 560 s in a local run. It eats
   the time the positions need.
5. **We judged changes on local runs and single sandbox runs.** One run per version cannot tell a 10 from an 18.

## Worth keeping (to bring back one at a time, each with a test run)

- The deterministic bid engine and its tests (stream I).
- No reasoning for the sub-agents (speed).
- The browser fixes against hangs and stalled calls (stream J).
- The ERP search per catalogue group as soon as the groups are known.
- Merging rules for facts: `null`, `[]` and `""` never erase a known value; a recorder never takes records back.

## How we continue: branch `baseline-18`

- `agent/` is exactly the 18.3 package (`270b072`); docs and push logs are current `main`.
- Known in the baseline: `tests/test_kit.py` has 3 tests that call a `solve` function `agent.py` no longer had (they
  already failed at `270b072`); on Windows the tests need `PYTHONUTF8=1` (`observe.js` is read without an
  encoding). Neither affects the sandbox.
- Order:
  1. Test run of the baseline unchanged: does the 18.3 repeat?
  2. Find why the discount was lost after `270b072` (compare the scored run's log with a later quality-10 run).
  3. Then one change per test run, each fixing a failure seen in the baseline's own log, kept only if the points
     per field go up.
