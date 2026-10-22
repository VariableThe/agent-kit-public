---
name: t3-thread-orchestration
description: Launch/monitor/follow up T3 threads. Triggers: new top-level thread, independent worktree, monitor thread.
---

# T3 Thread Orchestration

A T3 thread is a top-level conversation with its own model, runtime mode, and
optionally its own worktree. This skill covers launching one, watching it, and
sending it more work. It does not cover child subagents.

## When to use

- Only on explicit user intent for a separate or top-level thread, or when
  unrelated work would otherwise pollute the current thread's context.
- Not a substitute for `delegate_task` (child subagents) or for `create_threads`
  batch fan-out across the same checkout.
- Cross-host work (another machine's T3 environment on the tailnet) is a
  different tool surface — see "Across the tailnet" below.
- Never launch a thread the user did not ask for.

## Tool surface

All under the `t3_` prefix except `create_threads`.

| Tool | Key params |
| --- | --- |
| `t3_thread_launch` | `title` (required), `projectId?`, `scratch?`, `message?` (≤120k), `modelSelection?`, `runtimeMode?`, `interactionMode?`, `workspaceStrategy?`, `attachments?` → `{threadId, projectId, modelSelection, runId, status}` |
| `create_threads` | `threads[]` (1–20: `prompt?`, `title?`, `target?`, `runtimeMode?`, `interactionMode?`), `clientRequestId?` |
| `t3_thread_list` | `projectId?`, `statuses?`, `titleContains?`, `settled?`, `snoozed?`, `includeSubagents?`, `cursor?`, `limit?` ≤100 |
| `t3_thread_search` | `query` + `projectId?` |
| `t3_thread_read` | `threadId`, `itemId?`, `textOffset?`, `view` (`messages`\|`activity`), `afterPosition?`, `limit?` ≤100, `runLimit?` ≤50, `maxCharsPerItem?` ≤50000 |
| `t3_thread_wait` | `threadId`, `runId?`, `timeoutMs?` → `{threadId, runId, status, timedOut}` |
| `t3_thread_send` | `threadId`, `message` ≤120k, `mode?` `auto`\|`queue`\|`steer`\|`restart`, `clientRequestId?` → `{threadId, messageId, runId, taskId?, status, delivery}` |
| `t3_thread_interrupt` | `threadId`, `runId?`, `reason?` ≤2000 |
| `t3_thread_update` | `threadId?`, `action` `rename`\|`regenerate_title`\|`link_pull_request`\|`unlink_pull_request` |
| `t3_thread_organize` | `threadId?`, `action` `pin`\|`unpin`\|`snooze`\|`unsnooze`\|`settle`\|`unsettle`\|`archive`\|`unarchive`\|`mark_unread`, `snoozedUntil?` |
| `t3_thread_configuration` / `t3_thread_configure` | read everything / set `modelSelection` only |
| `t3_thread_fork`, `t3_thread_merge_back`, `t3_thread_transfers` | fork / merge at `sourcePoint` (`latest_stable`\|`run`\|`checkpoint`); list cross-thread transfers |
| `t3_worktree_handoff` | `branch`, `baseRef?`, `startFromOrigin?`, `runSetupScript?`, `continuationPrompt?` — moves the **calling** thread, ends the turn |
| `t3_worktree_list`, `t3_worktree_status` | worktree inventory and the caller's attachment state |

`scratch: true` gives a project-less thread in its own folder; it is mutually
exclusive with `projectId` and `workspaceStrategy`.

`workspaceStrategy` is one of:

- `{type:"root", branch?}`
- `{type:"existing_worktree", worktreePath, branch?}` — absolute path
- `{type:"worktree", baseRef, branch?, startFromOrigin?}`

## Create a thread

1. Dedupe first: `t3_thread_list({titleContains, statuses})` or
   `t3_thread_search`. Reuse the existing `threadId` if the work already has one.
2. Launch: `t3_thread_launch({title, message, projectId?, workspaceStrategy?,
   modelSelection?})`. Put the task in `message`; the `title` is a label.
   The parent must put its done-criteria and required report fields into `message`.
3. Retain the returned `threadId`. `status` can still be `preparing` after the
   call returns — acceptance is not completion.
4. Follow preparation with one bounded `t3_thread_wait(threadId, {timeoutMs})`
   or a single `t3_thread_read(threadId)`, then report.
5. If the launch response is lost, do **not** blind-retry: there is no
   idempotency key. Call `t3_thread_list` and inspect before launching again.

Worktree rule: for any file-touching or PR work pass
`workspaceStrategy: {type:"worktree", baseRef:"<branch>", branch:"feat/...",
startFromOrigin:false}`. Asking a thread to run `git worktree add` or `cd` in
its prompt does **not** rebind T3's workspace. Omitting `workspaceStrategy`
means the project root, not the caller's worktree.

## Monitor and report back

- Make one `t3_thread_wait(threadId, {timeoutMs})` call. If `timedOut: true`,
  do not spin: read new output with
  `t3_thread_read({threadId, afterPosition})` or check `t3_thread_list`, then
  report and end the turn.
- Track `afterPosition` between reads so nothing is re-read.
- `t3_thread_wait` returns status only. The result itself comes from
  `t3_thread_read`.
- Long items come back `textTruncated`; re-read that `itemId` with
  `textOffset` (UTF-16 units) until `nextTextOffset` is null.
- If T3 Code offers the `watch_pull_request`-style notification path for PR
  threads, prefer one wait call and ending the turn over manual polling.

## What the launched thread is

- An ordinary top-level thread: `createdBy:"agent"`, `creationSource:"mcp"`,
  `parentThreadId:null`, its own worktree under `~/.t3/scratch/...`, visible in
  the project sidebar until archived or settled. Run status and thread status are
  separate things.
- It inherits this environment's MCP servers, so it can act and verify the
  action itself. Put that duty in the launch `message` — resolve the target by
  exact name, confirm before acting, act once, read back — because it has none
  of the parent's context.
- No push from child to parent: the parent learns the outcome only via
  `thread_wait` → `thread_read`, or a later `thread_send`.
- Launch needs an explicit `modelSelection` when the project has no default
  (`defaultModelSelection` is null here); omitting it fails with
  `invalid_request`.
- `thread_list`/`thread_search` issued from outside a thread need `projectId`.

## Send follow-ups and updates

`t3_thread_send(threadId, message, {mode})`:

- `auto` — starts when idle, steers when active, queues when not steerable.
- `queue` — a separate follow-up turn.
- `steer` — inject into the in-flight run.
- `restart` — interrupt and restart.

Pass `clientRequestId` on retries so a resend does not duplicate the turn.
Report back the `delivery` and `taskId` from the response. `t3_thread_send`
works on any T3 thread, not just threads this session launched.

To stop runaway work: `t3_thread_interrupt(threadId, {runId?, reason})` and
report the returned `status` (`interrupt_requested` is not `interrupted`).

## Update metadata

`t3_thread_update` handles `rename` (prefix `[closed]` when the thread is
finished), `regenerate_title`, `link_pull_request`, and `unlink_pull_request`.
Linking needs `pullRequest: {repository, number, url}`.

## Close / "delete" — known gap

There is **no delete and no close tool** in the T3 MCP surface. Say so plainly
instead of pretending a thread was removed.

- `t3_thread_organize(threadId, {action:"archive"})` removes it from active
  lists; `action:"settle"` marks it done; `action:"snooze"` with
  `snoozedUntil` defers it.
- Optionally rename it to `[closed] …` and `unlink_pull_request`.
- Hard delete is UI-only.
- A thread's worktree is **not** removed automatically. Clean up with
  `t3_worktree_list` and the worktree removal flow — never by deleting files
  blindly.

## Communication between threads

- Parent → child: write the target `threadId` and the protocol into the child's
  launch `message` (report format, what counts as done, when to stop).
- Child → parent: a thread cannot call MCP tools unless it has them. The
  reliable pattern is parent-driven — the parent waits or reads, then
  `t3_thread_send`s a summary or the next instruction. Do not imply
  bidirectional messaging that does not exist.
- To move the **current** thread into a worktree use `t3_worktree_handoff`
  (which ends the turn and can carry a `continuationPrompt`), not a launch.

## Across the tailnet

Thread IDs, runs, and worktrees are per T3 environment. The local `t3_thread_*`
tools cannot read or message another machine's thread. Cross-host means
calling the *same* tool surface on the peer's MCP endpoint.

- A second `t3 serve` on B fails while the desktop app runs:
  `ServerAlreadyRunningError: A T3 Code server already owns <T3 home>`. Drive the
  server that is already up; a separate `t3 serve` needs its own `--base-dir`
  and then has its own projects and sessions.
- That server binds loopback (`host` in `~/.t3/userdata/server-runtime.json`).
  Two ways to reach it, both verified. Zero config: `ssh -N -L 3774:127.0.0.1:3773 B`
  then `http://127.0.0.1:3774/mcp`. Or publish it with
  `tailscale serve --bg --https=<free-port> http://127.0.0.1:3773` and use
  `https://<host>.<tailnet>.ts.net:<free-port>/mcp`; the port must be free — a
  peer's 8443 often already proxies another app, and `tailscale serve` flags must
  precede the target. Either form answers 401 unauthenticated, 200 on
  `initialize` with a valid token.
- Mint the credential with the MCP subject:
  `t3 auth session issue --scope orchestration:read --scope orchestration:operate
  --subject mcp-client --ttl 30m --token-only`. Without `--subject mcp-client`
  every call returns `401 invalid_mcp_credential` (server log:
  `unknown_or_expired_token`; only MCP client sessions are accepted) — scopes
  alone are not enough. The token is printed once: keep it in a shell variable,
  never in a file, log, or chat.
- Raw MCP is Streamable HTTP: `POST /mcp` with
  `Accept: application/json, text/event-stream`, take `mcp-session-id` from
  `initialize`, send `notifications/initialized` (202), reuse the header. Bodies
  come back as JSON or SSE (`data: {...}`) — parse both.
- `tools/list` returns bare names — `t3_thread_list`, `t3_thread_launch`,
  `t3_thread_wait`, `t3_thread_read`, `t3_thread_send`, `t3_thread_organize`
  (~83 tools on 0.1.1-nightly). A server registered under `mcp.servers.<name>`
  prefixes them (`t3-workstation_thread_launch`). Record a remote `threadId` with
  the host — it is meaningless on A.
- A caller outside a thread has no implicit target: `t3_thread_list` requires
  `projectId` (`target_required`), and `t3_thread_launch` requires
  `modelSelection` when the project has no default model (`invalid_request`;
  `orchestrator_capabilities` lists providers and models). `scratch:true`
  launches in the environment's scratch project; `runtimeMode` may not exceed
  the session ceiling (`approval-required` for a plain MCP session).
- Register B under `mcp.servers.<name>` (`type: "remote"`; url is the endpoint
  above), then `opencode mcp auth <name>` — or send `Authorization: Bearer`
  yourself, so a headless A needs no browser. Verify: `opencode mcp list`.
- Monitoring is the same shape, remote-scoped: `thread_wait` →
  `thread_read(view:"messages")` → `thread_send(mode:"auto")`. One bounded wait,
  no loop. Wait reports `completed`/`timedOut`; read returns `items` with
  `type:"assistant_message"` and the reply `text`.
- Worktrees and files live on B. Move files with the `tailnet-transfer` skill,
  not thread tools.
- Cleanup: `thread_organize({action:"archive"})` (returns `{sequence}`), then
  revoke on B (`t3 auth session revoke <id>`, or `t3 auth pairing revoke <id>`),
  close the tunnel, and drop A's MCP entry if the link was one-off.
- Failures: connection refused → not running or loopback-bound; 401 → credential
  missing, expired, revoked, or issued without `--subject mcp-client`;
  `isError:true` results carry `{_tag,code,message}` naming the fix. A peer that
  lags needs `t3 update`.
- Security: pairing links, tokens, and codes are secrets; least-privilege
  scopes (`orchestration:read` to monitor, `orchestration:operate` to launch);
  never copy token files between machines — revoke and re-issue; never publish a
  `127.0.0.1` URL as a remote endpoint.

## Safety and failure handling

- One thread per concern; never launch duplicates of active work.
- Bounded waits only; never busy-loop `t3_thread_read`.
- A launched thread must not run with a broader `runtimeMode` or
  `interactionMode` than the caller.
- Report tool failures and permission denials verbatim; never claim success
  without a tool confirmation.
- A `timedOut` wait is not a failure — it means the thread is still working.
- A remote thread's result is only trustworthy after a remote read on that
  host; local state says nothing about it.
