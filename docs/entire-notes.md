# Entire — talk notes and setup state (Sat 2026-10-10)

Source: Orhun's two voice recordings of the talk by Entire's CEO (ex-GitHub CEO), ~47 min in total, machine-transcribed
on-device; the raw text stays on his machine (`docs/entire-talk-transcript-raw.txt`, git-ignored). It is noisy;
points marked **(unclear)** need confirmation on Discord. CLI facts below were checked against `entire` 0.11.4 on
Orhun's machine.

## What Entire is

- A CLI that hooks into git and coding agents (Claude Code, Codex, Cursor, …). Every time you or the agent commits,
  the agent **session log** (prompts, responses, tool calls) is stored in the same repository as git objects and
  linked to that commit as a **checkpoint**. Checkpoints are pushed with `git push`.
- His framing: code is the most important artifact, the session log is the second. EHL makes Entire mandatory because
  the organizers want to see those session logs.
- Secrets, passwords and tokens are scrubbed from the logs, but his advice is still: never paste API tokens into the
  agent; put them in an environment variable / `.env` and let the agent read it from there.
- The company also runs a distributed git backend (regions in the US, EU/Frankfurt, Australia, India) and a web
  dashboard where sessions can be browsed, filtered to prompts only, and shared by link.

## Setup steps as demonstrated

1. Install the CLI (installer from entire.io). **Done.**
2. `entire enable` in the project folder; it can create the git repo and an initial commit, then asks which agents
   and whether to send anonymous telemetry. **Done** (all eight agents enabled, telemetry on).
3. **Codex: approve the hooks.** Codex has a trust system; on first start in the folder you must trust the folder
   and approve all seven Entire hooks, otherwise Codex sessions are not captured.
   **Not done:** `entire status` currently reports `7 Codex hook(s) need approval · open /hooks`.
4. When a session is captured the agent shows a line like "Entire will link this conversation to your next commit".
5. Create an empty repository on GitHub (he left it public), add it as `origin`, push. **Not done.**
6. Tell the agent once: "commit, and make sure the Entire session is attached". After that it remembers.
   Expected confirmation on push: `[entire] Pushing 1 checkpoint ref(s) to origin.... done` (from the Notion page).
7. On entire.io: log in with GitHub, go through onboarding, pick a region (EU), select the repository. This mirrors
   the GitHub repo to Entire and makes the sessions visible in the dashboard. **Not done.** Whether EHL requires this
   step or only steps 1–6 is **(unclear)**; the Notion page lists only install, enable and the push confirmation.
8. `entire login` in the terminal, which lets the agent search past session logs, including teammates' sessions in
   the same account/repo. He called this the last setup step. **Not done.** Related commands: `entire search`,
   `entire recap`, `entire session list`.

## Advice from the talk that matters for us

- **"Spend an hour tomorrow morning to prepare your pitch."** The jury are humans and weigh the pitch more than the
  product.
- Watch the token budget: do not burn more on API credits than the prize is worth.
- His working model: give agents tasks big enough to run unattended for a long time, with a check at the end, instead
  of many tiny supervised steps. Shared session logs are meant to stop agents repeating each other's mistakes.
- General claims (most code now written by agents, review as the new bottleneck) are opinion, not rules.

## Privacy check — before every commit and before the first push

Entire attaches the full log of every agent session registered in this folder to the next commit, and pushes it.
A session that read anything unrelated to the hackathon must not be attached.

1. `entire session list` — every listed session will be attached. Only sessions started in this folder for
   hackathon work may be there.
2. Remove any other session: `entire clean --session <id> --force`. This clears its state but **not** its data:
   also delete the shadow branch (`git branch -D entire/<base>-<hash>`; it is shared by all sessions on the same
   base commit and is rebuilt on the next turn) and the folder `.entire/metadata/<id>/`.
3. Verify what is left: `git ls-tree -r --name-only <shadow branch> | grep metadata` must list only wanted sessions.
4. A cleaned session registers again on its next turn, so close its window.

State 2026-10-10 ~12:50: one unrelated session was removed this way before anything was committed or pushed.
