# EHL Grand Finale 2026: How to Pick Your Challenge

**Event:** European Hackathon League, Grand Finale (TUM.ai), Garching bei München, 10–11 Oct 2026
**Decision deadline:** **Saturday 13:00.** The team captain selects on ehl.gg, and every team member must be checked in first.
**Scope of this guide:** Atira and Tacto. The BMW challenge has **not** been evaluated yet; see section 8.

---

## 1. TL;DR

| | Atira: "From RFQ to bid" | Tacto: "Negotiation Arena" |
|---|---|---|
| One-liner | Agent clicks through 4 German business websites and submits **one correct, signed bid** | Agent **negotiates by e-mail** with supplier bots and closes the **lowest total-cost deal** |
| Type of problem | Deterministic: exactly one right answer | Strategic: score relative to the best achievable deal |
| Hardest part | Fragile browser engineering | Negotiation judgment on unseen suppliers |
| Harder to implement | **Yes** | No |
| Harder to win at the top | Medium | **Yes** |
| Default pick | Only if the team has a strong browser-automation dev | **✔ Tacto** |

---

## 2. What each challenge actually tackles

### Atira: From RFQ to bid
You are a German hydraulics manufacturer bidding on a public tender for hydraulic cylinders. The agent must:

1. Read the tender, the bill of quantities and the bidder Q&A on the **Tender portal** (Vergabeportal). Traps: cookie wall, flaky errors, docs behind an expression of interest, iframes, ZIPs, a deadline in another timezone.
2. Find the **current** specification revision on the **Supplier portal**. Traps: it opens the oldest revision first, nested viewers, paged tables.
3. Match materials, stock, lead times and price conditions in the **SAP-style ERP**. Traps: transaction codes, F4 popup, short session timeout.
4. Find the right discount approval and the signing TAN in the **Webmail**. Traps: look-alike approvals for other tenders, superseded approvals, archive folder, paging.
5. Enter the bid in the configurator and sign it with the TAN.

**The 8 rules** define exactly one correct answer:

| # | Rule |
|---|---|
| 1 | Quantity = tender quantity, overridden by the latest clarification for that position |
| 2 | Requirement comes from the spec revision marked *current* |
| 3 | Material = active ERP material with an exact match on bore, stroke, pressure, mounting and certification. If one position has none, the answer is `no_bid` |
| 4 | Unit price = condition valid on the tender date, at the highest quantity scale reached, minus the discount, rounded half-up to the cent |
| 5 | Discount = most recent sales-director approval for *this* tender, otherwise 0 % |
| 6 | Lead time = from-stock if stock covers the quantity, otherwise production lead time |
| 7 | Total = Σ quantity × unit price, rounded half-up |
| 8 | `no_bid` → no positions, total 0 |

**Real-world problem:** autonomous back-office execution across legacy UIs (computer use and RPA reliability).

### Tacto: Negotiation Arena
You are procurement at Hollerau Pumpenwerke, a fictional German pump maker. Each **case** is one negotiation: a price-increase letter, an expiring framework contract, an RFQ with 2–3 suppliers, or a supplier with a red scorecard. The agent:

1. **Prepares.** It reads the case file and mines the data package: 3 years of POs, invoices, scorecards (OTD, PPM), contracts, market indices, should-cost, call notes and letters.
2. **Negotiates by e-mail.** Each message has a body, up to 3 term sheets, and cited data IDs that the arena checks as verified, contradicted, irrelevant or duplicate.
3. **Closes.** It accepts one supplier term sheet or walks away.

**Score per case** = `(C_open − C_deal) / (C_open − C_best)`, clipped to 0…1. C is the total cost: price, uncovered need, payment interest, freight, lead time, quality, index risk and supplier health. A broken must-have scores 0.

**Real-world problem:** autonomous commercial decision-making in procurement. The agent has to read the counterpart, trade the right levers, and know when to walk away.

---

## 3. Formats, scoring and final mechanics

| Item | Atira | Tacto |
|---|---|---|
| Ranking order | Eligible first → quality (0–100) → cost × time | Sum of 5 wave scores (max 500) |
| Practice / feedback | Test runs (max 2 h), 1 submission in flight | 200 practice sessions per 24 h, replay with supplier reasoning, local `arena-sim` |
| Final | 1 run on a **private dataset** after the hackathon (20 min limit) | **Live Final** Sun 12:00, 5 waves × 8 min, no human in the loop |
| Where the agent runs | Their sandbox (tarball + `agent.json`) | **Your machine**, your network, your LLM key |
| Winners decided by | Private run (most weight) + agent architecture + public leaderboard. Exact weights not published | Live Final points → top 10 pitch to the jury (5 + 2 min). Weighting still "placeholder" in the brief |
| LLMs | Free hosted Qwen3.8-Flash-Next + Cloudflare Clef. Your own key optional. **Cost counts in ranking** | Bring your own key (you pay). **Cost not scored**, but 60 s per message |
| Generalisation risk | Same sites, different data | Up to ⅓ of hidden cases are unseen supplier and case combinations |
| Prize | €1,500 + best 2 EHL finalists → Grand Final | €1,500 + best 2 EHL finalists → Grand Final |

---

## 4. Skills required

Legend: ●●● critical, ●● important, ● nice to have, – not needed

| Skill | Atira | Tacto |
|---|---|---|
| Python | ●●● | ●●● |
| Browser automation (Playwright/CDP, selectors, waits, iframes, popups) | ●●● | – |
| Robust error handling (retries, session timeouts, flaky pages) | ●●● | ●● |
| Document parsing (PDF, ZIP, XLSX, nested viewers) | ●● | ●● |
| Async / parallelism | ●● | ●●● |
| SQL / pandas data mining | ● | ●●● |
| LLM prompt + tool orchestration | ●● | ●●● |
| Deterministic rule / cost engine | ●●● | ●● |
| Procurement / commercial knowledge (Incoterms, payment terms, TCO, tactics) | ●● | ●●● |
| Quality domain (PPM, 8D, scorecards) | ● | ●● |
| SAP familiarity | ●● | ● |
| German reading | ●● | ● |
| Packaging / DevOps (tarball, sandbox env vars) | ●● | ● |
| Pitch / storytelling | ●● | ●●● |

**Ideal team shape**
- **Atira:** 1–2 browser-automation engineers, 1 rule-engine/data person, 1 person who scouts the sites and writes the pitch.
- **Tacto:** 1 backend/async engineer (runner, API, cost calculator), 1 data person (SQL over the package, ID mapping), 1 procurement/negotiation strategist (prompts, lever playbook), 1 pitch person.

---

## 5. Implementation difficulty

| Criterion | Atira | Tacto |
|---|---|---|
| Time to first end-to-end run | Long (4 sites, logins, configurator, TAN) | Short (starter kit plays a case in ~15 min) |
| Number of components | 4 site drivers + rule engine + packaging | Client + data layer + cost calc + LLM negotiator + runner |
| Brittleness | High: one missed popup means no answer | Low: stable API |
| Debug loop | Slow | Fast |
| 0 → 60 % of the score | Hard (all-or-nothing chain) | Easy |
| 60 → 95 % of the score | Medium (precise per-field feedback) | **Hard** (stochastic opponents, unseen types) |
| Silent-failure traps | Wrong revision, superseded approval, wrong price scale → plausible but wrong bid | Wrong data IDs cost trust; broken must-have = 0 |
| Final-day operational risk | Low (their sandbox) | Medium–high (your laptop and network must run unattended) |
| **Overall implementation difficulty** | **Higher** | Medium |

---

## 6. The decision process: run this before 12:30

### Step 1: Hard knock-out questions
Answer honestly. One "no" can decide the choice.

| Question | If NO → |
|---|---|
| Does anyone on the team have **production-grade Playwright/Selenium/CDP** experience? | Strongly avoid Atira |
| Do you have a **paid frontier LLM key** with enough budget for ~200 practice sessions + the final? | Tacto gets harder (open question: are Qwen-class models fast and good enough within 60 s per message?) |
| Can one laptop and network stay **online and unattended Sun 12:00–13:00**? | Tacto final at risk; use a cloud VM or a backup machine |
| Is anyone comfortable with **SQL/pandas** on a multi-table dataset? | Tacto preparation step weak |

### Step 2: Score each challenge
Rate each criterion 1–5 for **your actual team**, multiply by the weight, then sum.

| Criterion | Weight | Atira (1–5) | Tacto (1–5) |
|---|---|---|---|
| Team skill fit (section 4) | 30 % | | |
| Probability of a working end-to-end agent by Sat 20:00 | 20 % | | |
| Ceiling: ability to beat strong teams at the top | 15 % | | |
| Domain edge (procurement / quality / SAP / German) | 15 % | | |
| Final-day operational risk (5 = low risk) | 10 % | | |
| Pitch story / jury appeal | 10 % | | |
| **Weighted total** | 100 % | | |

> Rule of thumb: if the totals are within **0.3**, pick the challenge with the **faster feedback loop** (Tacto). Iteration speed beats raw ambition in a 24 h build.

### Step 3: Reference scoring for a typical team
This assumes a domain-strong, code-moderate team **without** a dedicated browser-automation specialist.

| Criterion | Weight | Atira | Tacto |
|---|---|---|---|
| Team skill fit | 30 % | 2 | 4 |
| Working agent by Sat 20:00 | 20 % | 2 | 5 |
| Ceiling vs strong teams | 15 % | 3 | 3 |
| Domain edge | 15 % | 3 | 5 |
| Final-day operational risk | 10 % | 4 | 3 |
| Pitch story | 10 % | 3 | 4 |
| **Weighted total** | | **2.6** | **4.1** |
| **Recommendation** | | | **✔ Tacto** |

With a strong browser-automation engineer, Atira's "skill fit" and "working agent" scores rise to 4–5 and the gap closes. Re-score in that case.

### Step 4: Sanity checks before committing
- [ ] Opened both starter kits and confirmed they install on our laptops
- [ ] Attended or skimmed the 12:00–13:00 deep dive of the leading candidate
- [ ] Asked the jury how the final score and the jury score combine (unpublished for both)
- [ ] Confirmed whether we are an **invited League finalist**. This matters only for the Grand Final, not for the €1,500 track prize
- [ ] Captain has the ehl.gg login ready and everyone is checked in

---

## 7. Pros and cons

### Atira
**Pros**
- Clear, objective target: one correct answer and per-field scoring
- Free hosted models, and the run happens in their sandbox (no laptop risk on final day)
- The private data has the same sites and rules, so scripted navigation transfers well
- Strong fit for SAP, German and spec-revision (APQP) backgrounds

**Cons**
- Heavy browser engineering, and every trap (cookie wall, timeouts, iframes, ZIPs) must be handled
- All-or-nothing chain: one broken step means no answer
- Slow iteration (1 submission in flight, long test runs)
- Cost × time is ranked, so you can't just throw a big model at it
- Hardcoded answers are rejected, and a coding agent reviews the package

### Tacto
**Pros**
- Running in 15 minutes, with 200 practice sessions per day and replays that explain the supplier's reasoning
- Rewards domain judgment: procurement levers, PPM, 8D, scorecards, TCO
- Partial scores possible; a naive agent already scores something
- Strong pitch material (replays as storytelling)

**Cons**
- Hard to reach the top: stochastic opponents, and ⅓ of the hidden cases are unseen combinations
- You pay for LLM calls; the 60 s per message limit forces a model speed vs. quality trade-off
- The final runs on **your** infrastructure, unattended, with 10 parallel sessions per wave
- Wrong data IDs cost trust, and a broken must-have scores 0
- The cost engine must be code (`total_cost`), not LLM math

---

## 8. Open items and unknowns

| Item | Status |
|---|---|
| BMW challenge | **Not evaluated.** Get the brief and add a third column to sections 3–6 |
| Atira winner weighting (private run vs architecture vs leaderboard) | Not published ("exact weights will follow") |
| Tacto track places 1–3 (Live Final vs jury weighting, jury criteria) | Placeholder in the brief, "decided Sat morning" |
| Tacto: when the arena goes offline Saturday night | Announced on Discord |
| Team composition and skills | **Fill in Step 2 with real names** |

---

## 9. First hour after deciding

**If Tacto**
1. Apply for the key at https://arena.tacto.ai/apply and get it approved at the Tacto desk (the key is shown once).
2. Run `git clone https://github.com/tacto-ai/negotiation-arena-kit.git`, `cd starter-kit`, `cp .env.example .env`, then `uv sync --all-extras`.
3. Run `uv run arena play case:P01` and read the replay together.
4. Split the work: runner + async, data layer + `facts.json` ID index, `total_cost` per term sheet, LLM negotiator prompts.
5. Put Sunday 08:00 (new data release) and 09:00 (pre-flight) in everyone's calendar.

**If Atira**
1. `curl -fsSL https://ehl-challenge.atira.ai/install.sh | sh`, then `atira login`.
2. `eval "$(atira scout --challenge rfq --env)"`, then click through all 4 sites by hand and document every trap.
3. Fork the starter kit (https://ehl-challenge.atira.ai/starter-kit.tar.gz) and get a first test run end to end, even with a wrong answer.
4. Split the work: one driver per site (4 browsers in parallel), plus a deterministic rule engine for rules 1–8.
5. Never tune to the public data; the private run decides.

---

## 10. Sources
- EHL Finale info: https://tum-ai.notion.site/EHL-Finale-All-you-need-to-know-3eb7306bfd62808993e9ec18c2d64834
- Atira challenge: https://ehl-challenge.atira.ai and https://ehl-challenge.atira.ai/challenges/rfq
- Atira docs for agents: https://ehl-challenge.atira.ai/llms.txt
- Tacto kit: https://github.com/tacto-ai/negotiation-arena-kit
- Tacto brief: https://github.com/tacto-ai/negotiation-arena-kit/blob/main/brief/brief.md