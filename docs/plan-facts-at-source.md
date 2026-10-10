# Plan: facts at the source, work driven by what is missing

Written Sat 2026-10-10 evening by stream I (Ivan) for the whole team and the coding agents. Status: proposal, on
branch `plan-facts-at-source`; nothing is built yet. Part 1 is the plan, part 2 challenges every decision in it,
part 3 is the order of work with a measurement after each step that decides whether the next one is needed.

## The problem, from the test runs

Every sandbox run so far ended with quality 10 or less: the right decision and tender reference, no position
priced, no discount. The proximate cause was different each time (a lead answer of 12,000 thinking tokens; a second
round started 1 s before the window closed; positions read only in the second round; the checklist that only asked
for ERP searches once positions were known). The constant is that the critical path

    specification (catalogue groups)  ->  ERP search per group  ->  details of the matching candidates

was not completed once: no run ever had a material with its attributes in the facts.

Measured (points and timings only, from `/v1/runs/{id}`, `/logs.jsonl`, `/llm-calls`):

| Run | Spec with groups recorded | ERP searched by group | Materials with attributes | Quality |
|---|---|---|---|---|
| run_78afea5264793796 | yes | no (lead stalled 339 s) | 0 (1 without attributes) | 10 |
| run_118558b27a94dae0 | yes | no | 0 | 10 |
| run_969ce72f14a980ac | yes, at 224 s | no (round 2 sent no ERP job) | 0 (3 without attributes) | 10 |

How information moves today (stream-j at 8b7a460):

    page -> sub-agent notes (free text) -> report (prose, often a wrap-up after a timeout)
         -> recorder model (one LLM call per report) -> FactsBuilder -> engine -> needed -> lead model -> next round

- The lead's turn waits for every investigation it started (`asyncio.gather` in `Orchestrator.run`): work moves in
  rounds, and with 20 minutes and 3-10 s per model step two rounds fit. The critical path needs three hops.
- Almost every investigation ends by its time limit, not by `finish`; its facts come from a hurried wrap-up.
- The recorder is a further model hop of 20-150 s per report and can drop or alter what the sub-agent saw.

## Part 1 · The plan

### Decision 1: sub-agents record typed facts while they browse

Give every browsing sub-agent a tool `record` that takes a facts update (the same JSON `FactsBuilder.add` takes)
and adds it to the run's one `FactsBuilder` at once. The answer to the tool is the builder's summary or its error
naming the bad field, so the sub-agent corrects it in its next step. `note` stays for anything that does not fit.

- The tool's parameter is a plain object; the field reference is a compact text (about 1,500 tokens, generated
  from `facts.json_schema()`), not the 25 KB JSON Schema, so each step does not carry the full schema.
- The report at the end becomes a short status (what was searched, what was not reached), no longer the carrier of
  the facts. The recorder stays only as a fallback for a report whose agent recorded nothing.

### Decision 2: work is started when a fact makes it possible, not in rounds

After every `record`, code evaluates the facts (`engine.evaluate`, about 0.1 s for a real tender) and compares the
"find" entries of `needed()` with the work in flight. New entries are handed to the lead model in a short call
without thinking: "assign these needs to sites and write a one-line task for each". Code then starts each task as
its own small investigation, in a free tab of that site, without waiting for the others.

- The model decides where to look and what the task says (fair play rule 11); code only notices that something is
  missing and that a tab is free.
- A need that is in flight is not handed out again; a task that ends without resolving its need may be handed out
  once more with what the first attempt reported.
- The lead keeps its other jobs: judging ambiguities, a no-bid, the submission.

### Decision 3: small tasks with a clear end

One task is one need: one catalogue group, one candidate's details, one mail topic, the positions, the Q&A. A task
has a step budget of about 15 steps and ends with `finish` as soon as its need is recorded. The time limit stays as
a safety net only.

### Decision 4: needs carry a stable identity

`needed()` entries get an `id` (kind, site role, rule, the key or group and positions) so that code can tell an
entry it has already handed out from a new one, and can log when each need was raised, handed out and resolved.

### Decision 5: one timeline per run

Log, for every need: raised at, handed out at (to which site and tab), resolved at, by which task. After a run this
gives the critical path in seconds without reading any value: the one measurement that tells whether a change
helped.

### What stays

The engine, `facts.py`, `observe.js`, `browser.py`, the submission agent, the deadline guard, no thinking for the
sub-agents. `main` stays the fallback for every submission until a test run of the new branch scores higher.

## Part 2 · Challenging every decision

For each decision: the strongest argument against it, what could fail, how we would notice, and what we do then.

### Is a fundamental change needed at all?

- **Against:** Julien's fix of 19:02 (`needs.py` asks for the ERP searches as soon as the specification is read)
  removes the cause of the last failure. With it, run_969ce72f14a980ac would have sent the five group searches in
  round 2 at 231 s; without thinking the ERP agent does about 30 steps in 2 minutes, and one group search plus the
  details of one or two matching candidates (read with `read_pages`) is about 10 steps. Round 2 lasts 4 minutes. The
  critical path might fit into the existing two rounds.
- **But:** it fits only if (a) the positions or at least the specification arrive in round 1, which failed in one
  of three runs; (b) the round-2 ERP agents finish and their facts survive the recorder; (c) the lead's turn between
  the rounds is short. Each run so far broke one of these.
- **Consequence for the plan:** step 1 below is the cheapest test of the premise. One test run of stream-j with
  Julien's fix comes first. If it prices positions, decisions 2-4 are optional and only decision 1 is done next,
  because it makes a timeout harmless. If it does not, the timeline from that run tells which link broke.

### Decision 1: typed facts at the source

- **Against: a small model without thinking records worse than the recorder.** The recorder sees a whole report
  and the brief; a sub-agent records while browsing, with less context (which tender reference, which revision is
  current). Mitigations: the field reference names the traps (`FILLING_GUIDE` already does); the engine applies the
  authority and time rules itself, so the sub-agent only copies values with their source; `FactsBuilder` rejects a
  malformed update with the field named. Test: a local run with the scouting logins, compare the facts recorded at
  the source with what the pages show (counts and fields, as in the run analysis). Fallback: keep the recorder for
  the report as a second source; same-identity records merge.
- **Against: tool calls get bigger and slower.** A `record` call writes JSON: a material with 3 conditions and 4
  scales each is about 400-600 tokens, 10-15 s at 40 tokens/s. Still cheaper than the recorder's 3,000-4,000 tokens
  per report, and it runs in parallel per tab. Notice: `llm-calls` latency of `record` steps.
- **Against: the information is split differently in the final run.** A tool per site role ("ERP records
  materials") would break when the mailbox holds the price conditions. Therefore one `record` tool for every agent
  with the whole update schema, not role-specific tools.
- **Against: concurrent writes.** Several agents add to one builder. All of them run in one asyncio loop and
  `add()` is synchronous, so updates do not interleave. Same-identity records from two agents are merged field by
  field (materials, revisions, positions) or replaced (mail records): the later one wins. Risk: an agent records a
  look-alike record with the wrong reference and replaces a right one? No: the reference is part of a mail
  record's identity, so a look-alike is a separate record that the engine ignores.
- **Against: a wrong record cannot be taken back.** `replace_lists` exists but is coarse. Accept: the engine filters
  by authority, date and reference; a wrong attribute on a material would have been wrong in the recorder too.

### Decision 2: event-driven dispatch

- **Against: fair play.** Rule 11 forbids helpers that "autonomously discover and traverse the websites to assemble
  the complete task evidence before model-directed investigation". The dispatcher does not browse and does not
  decide where to go: it hands the engine's list of missing facts to the lead model, which assigns sites and writes
  the tasks, and the sub-agent models navigate. The reviewer reads code, so the docstrings must say exactly this.
  If a reviewer flags it, it costs the submission: keep `main`'s structure as the latest passed submission until
  the new branch has passed review once.
- **Against: the checklist is not a complete plan.** `needed()` only knows facts it has a place for. Facts that
  unlock others but are not in `facts.py` (where the bill of quantities is, which mail folder holds what) are not
  needs. So the first tasks stay the lead's: one survey per site, as today. Dispatch takes over for what the
  checklist does name.
- **Against: roles are not sites.** `needed()` says "ERP", "mailbox"; in the final run the sites have other names
  and the information is split differently. Code must not map a role to a site; the lead model does, from the
  first reports. Failure: the lead assigns a need to the wrong site, the task ends with "not found", the need is
  handed out again. Cap: two attempts per need, the second with the first report attached and the instruction to
  consider the other sites.
- **Against: too many parallel agents.** 4 browsers, 4 tabs per site, 32 parallel Qwen calls per team. Five group
  searches plus their candidates plus mail topics can exceed 16 tabs. Cap tasks in flight per site at the tab count;
  queue the rest in the checklist's order (searches first). Sessions: every further tab gets a browser context of
  its own and signs in by itself (`Orchestrator` opens it with `browser.new_context()`), so each tab costs a login
  and a site may end one tab's session when another tab of the same user logs in. Test locally with 4 tabs on the
  ERP before relying on it; if sessions are cut, fall back to fewer tabs or to one shared context per site.
- **Against: the lead becomes a bottleneck again.** Every new batch of needs is a lead call. Without thinking it
  took 14 s in Julien's local run; batches are small. If it stalls, the needs wait. Mitigation: the assignment call
  has its own short limit (30 s, then asked again); if the lead has assigned a role to a site before, code reuses
  that site for the same role, and the lead only writes the task text.
- **Against: thrashing.** A need that stays unresolved (the fact does not exist on any site) would be handed out
  forever. Two attempts per need, then it is marked as given up and the bid goes ahead without it (the engine
  already leaves out what it cannot price).
- **Against: the engine's "find" entries include things no site can answer** (e.g. "director_address from the
  brief"). Needs with site "brief" are never dispatched; they go to the lead.

### Decision 3: small tasks

- **Against: overhead per task.** Every task starts a sub-agent: a new tab, maybe a login, the first page view.
  That was about 10-20 s in the runs. For five groups in parallel that is paid once per tab, not per group, if a
  tab keeps its session for the next task. Keep tabs and their sessions open between tasks.
- **Against: a small task lacks context.** "Read the details of material M" without knowing which requirement it
  must match leads to opening every candidate. The task carries the requirement's attributes (from the facts) so
  the agent can compare in the result list and open only matches (the guide already says so).
- **Against: 15 steps is a guess.** Measure: steps per resolved need in the timeline. Adjust after the first run.

### Decision 4: stable need ids

- **Against: ids change when facts change.** A group search's entry gains positions as they are recorded (Julien's
  fix merges them into the same entry). The id must therefore not contain the positions: kind, role, rule and the
  key or group only. A test in `tests/test_engine_needs.py` pins this.

### Decision 5: the timeline

- **Against: more logging, more noise.** One line per need event, a few dozen per run. Values never appear in it
  (needs name groups and keys of the public scenario; the log is private to the team, but the analysis printed in
  agent sessions must mask them, as the run analyses so far did).

### What could fail across the whole plan

- **The submission gets less time.** Event-driven work tends to fill the window. The deadline guard and
  `SUBMISSION_RESERVE_SECONDS` stay untouched; dispatch stops at the end of the investigation window.
- **It is late in the hackathon.** Every step is on a branch and judged by a test run; a step that does not raise
  the score is not merged. The last submission before Sunday 12:00 is the best one that passed, whatever branch.
- **We optimise for the public scenario.** Every decision here is about how facts flow, not about where they are on
  the public sites; the final run's different split of information is the reason for one `record` tool and for the
  lead mapping roles to sites.

## Part 3 · Order of work, each step with its measurement

| Step | What | Who (files) | Measured by | Go on when |
|---|---|---|---|---|
| 0 | One test run of `stream-j` as it is (Julien's early ERP search) | Julien starts it | points per position; did the ERP get group searches in round 2; materials with attributes | always; it decides how much of the rest is needed |
| 1 | Timeline logging (decision 5) and need ids (decision 4) | I: `engine/needs.py`; O: `agent.py` logging | a local run prints the critical path in seconds | it works locally |
| 2 | `record` tool in the sub-agents (decision 1), recorder as fallback | J: `browsing.py`; O: `agent.py` wiring; I: the compact field reference in `facts.py` | local run: facts recorded at the source vs. pages, record-step latency | facts at the source are as complete as the recorder's |
| 3 | Dispatch from `needed()` with the lead assigning (decision 2), small tasks (decision 3) | O: `agent.py`; J: task budget in `browsing.py` | timeline: time from "groups known" to "materials with attributes" | step 0 or a run after step 2 still misses the critical path |
| 4 | Test run of the branch, compare with `main` | Julien starts it | quality, points per field, timeline | merge only if quality is higher than the best run of `main` |

Steps 1 and 2 are useful whatever step 0 shows: they make a timeout harmless and the next failure measurable.
Step 3 is the large change and is done only if the measurements say the rounds are still the bottleneck.

## Part 4 · Review of this plan: what it gets wrong or leaves open

A second pass over the plan, checking its claims against the code (stream-j at 8b7a460) and the run data.

**Corrected in the text above:** further tabs do not share the first tab's session; each has its own browser
context and signs in (`Orchestrator` tab opening). The session risk of decision 2 is therefore real, not
theoretical, and every extra tab costs a login of 10-20 s.

**Gaps the plan has to close before step 3:**

1. **The engine cannot evaluate before the order-level facts exist.** `FactsBuilder.facts()` raises until
   `tender_reference` and `scenario_date` are set, so event-driven dispatch would sit idle at the start. The lead
   must record these (and `director_address`, `bidder_domain`) from the brief in its first turn, before any
   sub-agent reports. If the scenario date is only on a site, the dispatcher evaluates with a provisional date for
   the needs only and never prices with it.
2. **The assigner must not share the lead's conversation.** If the "assign these needs" call goes into the lead's
   message history while the lead is mid-turn, the two interleave. Make the assigner a separate, stateless call
   (brief, site list, a short digest of the reports so far, the new needs) and append only its outcome to the lead's
   history as a note.
3. **Facts noted but not recorded are lost when the recorder is only a fallback.** A sub-agent that recorded some
   facts and only noted others would have its notes ignored. The recorder should run on every report whose notes
   hold anything not yet recorded (cheap check: the report's FOUND part lists items without a matching record),
   not only on reports with no records at all.
4. **The positions problem is not solved by the plan.** In one of three runs the bill of quantities came only in
   round 2. "The positions" is a global need without a site; dispatch hands it out again only after the first
   survey misses it, which is the same delay. The lead's first turn should give the tender portal two tasks in
   parallel (positions; clarifications), as the earlier review proposed. This is a prompt change for stream O and
   does not need the rest of the plan.
5. **The time claim for the critical path is unproven.** "One group search plus one or two candidates' details is
   about 10 steps" is an estimate from the public ERP's structure (search, list, three transactions read with
   `read_pages`). The final run's ERP may need more steps. The timeline of step 1 is what replaces the estimate.
6. **The first record step may come late.** A sub-agent without thinking may prefer to keep browsing and record at
   the end, which brings back the timeout loss. The prompt must say "record each record as soon as it is read", and
   the agent should be reminded after a few actions without a `record`, like the existing `NOTE_EVERY` reminder.
7. **Two sources of the same fact can disagree.** When both the sub-agent and the recorder write a material, the
   later write wins per field. A recorder that misread a value could overwrite a correct one recorded at the
   source. Order the writes so that the source's record wins: the recorder only adds fields that are missing.

**Claims checked and holding:**

- Rounds come from the lead's turn waiting for all its investigations (`asyncio.gather` in `Orchestrator.run`).
- `FactsBuilder.add` is synchronous and validates only what an update changes; one asyncio loop makes concurrent
  agents safe.
- A mail record's identity contains its reference, so a look-alike never replaces the right record.
- Without thinking, the ERP agent made 33 steps in about 2 minutes in run_969ce72f14a980ac.
- The engine evaluates a 5-position tender in well under a second (0.4 s for 100 positions).

**The decision this review changes:** step 3 (dispatch) is not done before gaps 1-3 are closed; gap 4 (two tender
tasks in the first turn) and gap 6 (record as soon as read) are small and go into steps 0 and 2.
