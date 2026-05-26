---
name: message-project
description: |
  Forward a message from the user to a specific named project-lead and read the
  lead's reply. Use when the user says "tell <project> to ...", "ask <project>
  about ...", or "<project>: ...".
allowed-tools:
  - mcp__project-orchestrator__send_to_project
  - Bash(tmux -L soma-lead-*:*)
---

# message-project

Project-leads are NOT teammates of the channel bot. Each lead is an independent
claude process in its OWN tmux server (socket `soma-lead-<name>`, session
`soma-proj-<name>`, in a sibling cgroup). `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
only lets a lead spawn its own subagents; it does NOT federate the lead into the
bot's team. So `SendMessage(to: "soma-proj-<name>")` always fails with
"No agent named ... is currently addressable", and there is **no push-back
channel** from a lead back to the bot. Therefore: deliver by typing into the
lead's tmux pane, and read the reply by scraping that pane.

> **If you are the channel bot, do NOT run this skill inline.** The reply-poll
> in step 3 is unbounded (the lead may think for minutes) and would freeze the
> Telegram channel. Dispatch a background `Agent` (`run_in_background=true`,
> `model="opus"`) whose prompt is to run this flow and return the lead's reply,
> ack the user immediately, and relay the reply when the agent completes — see
> "Messaging a project-lead" in `system_prompts/responsive_bot.md`. Run the
> steps below inline only when you are already a background/non-channel agent
> (e.g. that dispatched agent, or a project-lead talking to another lead).

## Process

1. **Resolve + validate.** Extract the target project name from the user's
   message, then call
   `mcp__project-orchestrator__send_to_project(name, message)`. This DELIVERS
   NOTHING -- use it only to (a) confirm the project exists (it raises
   `no project named X` otherwise) and (b) confirm the bare name. From the bare
   name the tmux coordinates are fixed:
   - socket:  `soma-lead-<name>`
   - session: `soma-proj-<name>`

2. **Deliver the message** with two SEPARATE tmux calls -- type the literal
   text, then submit. Combining the text and Enter into one call is unreliable.

   Shell-safety: the user's text is arbitrary (it may contain `'`, `"`, `$`,
   backticks, `#`, newlines). Wrap the whole message in SINGLE quotes and
   escape any embedded single quote as `'\''`. This is exactly what
   `shlex.quote` produces; inside single quotes every other character is
   literal, so nothing can expand or break out of the argument.

   ```bash
   # type the message literally (-l = literal text, no key-name interpretation)
   tmux -L soma-lead-<name> send-keys -t soma-proj-<name> -l '<safely-quoted message>'
   # submit it as a SEPARATE call
   tmux -L soma-lead-<name> send-keys -t soma-proj-<name> C-m
   ```

   Worked example -- message is: `what's the $STATUS of build #3?`
   ```bash
   tmux -L soma-lead-f1-tracker send-keys -t soma-proj-f1-tracker -l 'what'\''s the $STATUS of build #3?'
   tmux -L soma-lead-f1-tracker send-keys -t soma-proj-f1-tracker C-m
   ```

   If the text lands in the prompt but doesn't submit, send one more `C-m`
   after ~1s as a fallback. If the message spans multiple lines, send it as one
   `-l` call (single quotes preserve the newlines) -- but note each newline is
   delivered as an Enter, so the lead may treat lines as separate inputs; to
   keep a multi-line message as one turn, collapse it to a single line first.

3. **Read the reply by scraping the pane** (there is no reply bus). Give the
   lead a few seconds, then capture its pane; poll up to a few times if it's
   still thinking:

   ```bash
   sleep 4
   tmux -L soma-lead-<name> capture-pane -p -t soma-proj-<name>
   ```

   `capture-pane -p` returns the visible screen -- the lead's most recent
   output -- so just read the last lines of it for the response. For more
   scrollback, add `-S -<N>` (e.g. `-S -80` to start ~80 lines back). If the
   footer still shows the lead working/thinking, `sleep` again and re-capture.
   (Deliberately no `| tail`: the bare capture is already the tail, which keeps
   this skill's only shell dependency the single `tmux -L soma-lead-*` pattern.
   The pane is a live TUI, so expect box-drawing and a prompt footer around the
   text.)

4. **Report to the user**: relay the lead's response. If it's still working
   after a couple of polls, say the message was delivered and the lead is still
   processing.

## Error handling

- `send_to_project` raises `no project named X`: the project doesn't exist (or
  already died and was reconciled out). Offer the closest match from
  `list_projects` and ask the user to confirm.
- A tmux call prints `can't find session` or `no server running on ...`: the
  lead's session has died even if the registry still listed it. Tell the user
  the lead isn't running and offer to respawn it (spawn-project).
