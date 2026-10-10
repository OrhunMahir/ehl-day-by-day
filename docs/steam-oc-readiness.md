# steam-oc readiness

Prepared on 2026-10-10 · agent: codex.

This note describes the `steam-oc` candidate. It has been exercised with offline tests, including local Chromium and scripted model responses. **This snapshot has not been validated by a live Atira run. No score improvement is promised.**

**Full suite passed:** 260 tests in 133.753 seconds; 259 passed and the live-site survey was skipped. Run from `agent/`:

```sh
SURVEY=0 UV_CACHE_DIR=/private/tmp/ehl-uv-cache uv run --no-sync python -m unittest discover -s tests
```

`git diff --check` passed. Entire configuration and hooks were preserved. Orhun authorized commit and push to `steam-oc`, then explicitly postponed the test run. No live test is authorized until he resumes it.

## Why the earlier signed bid scored 10

The submitted answer already contained no offered positions before the submission agent entered the form. Research and fact recording had not established the required specifications, complete catalogue searches and matching materials in time. The old engine could treat the presence of a candidate as proof that a group had been searched completely, producing a premature no-bid decision. The lead then converted the incomplete result into a bid with unresolved positions omitted. Signing that empty answer succeeded, but did not earn position quality points.

That diagnosis is separate from the browser and form risks below. It does not establish that the wizard lost previously priced positions, or that an unchanged visible modal proves background research stopped.

## Changes in this candidate

| Boundary | Current behavior |
| --- | --- |
| Reading and browser control | Element IDs persist for the same living DOM node and are not recycled onto replacement nodes. Exact, unambiguous target resolution and physical clicks reject disconnected, disabled, covered or modal-blocked controls. Cloned research contexts copy session state, including origin-matched session storage. Access gates remain incomplete reads rather than evidence that a document is absent. Deadline interruption preserves notes and recent unread views for later fact recording. |
| Lead and scheduling | Starting pages open in parallel. The initial research batch must cover every available website, and all its investigations start before an interleaved wait can block them. Investigations return background job IDs; reports become facts progressively. Incoming facts invalidate an outdated pending plan. Follow-ups wait for pending recordings. An unchecked fallback cannot bypass initial research. Early submission waits for all research, recordings and failed-report recovery. At the cutoff, research closes, recorders drain within the signing reserve, overdue tasks are cancelled and the answer is frozen before form entry. |
| Failed-report recovery | Each report has its own identifier. A manual fact update acknowledges only the specific recovered reports; other failed reports remain visible. |
| Facts and bid engine | Catalogue search coverage must be explicitly complete; finding one candidate is insufficient. Unknown active status and an unconfirmed governing revision block conclusive no-bid. Partial bids preserve the original facts and unresolved lines, so follow-ups still ask for the missing ERP evidence. Runtime catalogue groups become actionable early. Partial condition and quantity-scale updates merge without discarding earlier evidence. Arithmetic remains deterministic with Decimal values. |
| Model queue | All model roles share a local limit of four concurrent requests. The current roles request `low` for the lead, `medium` for recording and `xhigh` for browsing and submission. Callers apply phase and deadline limits; permanent API errors fail promptly. Calls still use the configured platform proxy. These settings have not been compared in a controlled live run. |
| Submission | Ordered form actions support dependent choices, exact material selection and successive position steps on the same URL. Numeric targets are captured before changes; unsafe later actions wait for a fresh observation. Automatic navigation recovery is disabled. The form protection also survives a new submission attempt. Repeated-page warnings use page content and preserve the current form. |
| Acceptance | `SubmissionAgent.accepted` requires an explicit signed-receipt declaration whose nonempty receipt text appears in an observed submission page. Acceptance cannot be declared in the same response that performs the preceding page actions. The lead distinguishes a closed run from an accepted bid; expiry or loss of API access alone is insufficient. Positive platform terminal status can independently confirm acceptance. |

## Offline evidence and limits

- The final suite includes 18 submission tests, 45 lead tests and 8 model-runtime tests. These cover stale targets, dependent choices, protected fields, actual deadlines, four-site scheduling, final recording barriers, report recovery, queue cancellation and receipt evidence.
- The new bid-pipeline integration test passed. Invented typed facts are recorded by the real orchestrator, priced by the real engine and passed to the real submission agent. A scripted model reads browser observations and fills two positions in a local Chromium wizard. The test checks quantity, unit price, discount, total and lead time, then observes the signed receipt in a later turn.
- Browser, lead and engine regressions use fabricated HTML and facts. No public scenario values, credentials, fixed challenge routes or site-specific selectors were added to these tests. The pipeline test blocks outbound requests and does not call a real model or run API.
- These tests do not establish hosted queue latency, live session behavior, reviewer acceptance, final-scenario generalization or eventual quality. The model still judges whether observed receipt text signifies a successfully signed bid; platform confirmation remains valuable. The conservative submission navigation restrictions may need adjustment for an unfamiliar wizard, and loop warnings do not guarantee automatic recovery.

## One controlled next test

Orhun is the single run operator. Other agents should inspect code and results without starting competing runs or changing the candidate while it is being packaged.

1. Use this verified code snapshot. If it changes before packaging, rerun the relevant checks. Review the package contents, preserving Entire configuration and excluding secrets and observed scenario values.
2. When authorized, start one test of this candidate with `atira test-run agent`. Record the uploaded package hash and code snapshot so the result can be attributed to the tested code. Do not automatically start another run, a scored submission or a final run.
3. Inspect whether all websites receive early tasks, reports become facts progressively, and specification-derived group searches begin while research time remains. Distinguish missing evidence from completed searches with no match.
4. Check the handoff answer before judging the wizard: offered positions should be nonempty when matching evidence supports them, and omissions should have explicit reasons. Inspect research, recording, pricing and signing timestamps against the actual deadline.
5. Follow material selection and each successive position step. Verify entered quantities and prices against the computed answer, then require a signed receipt and platform acceptance confirmation before calling the run successful.
6. Review the quality breakdown, omitted positions, errors and request timing. Save only general behavior, scores and code/package attribution in repository notes; keep observed scenario values outside the package and public documentation. Decide the next change or test after this result is understood. A scored submission remains a separate captain decision.

No live test, scored submission or cancellation was performed for this candidate. Commit and push are authorized; running the candidate remains on hold.
