---
name: Workstation
description: Offload heavy builds/tests/GPU jobs to a more powerful SSH/Tailscale host. Triggers: heavy build, large test suite, GPU training.
---

# Workstation

A dedicated high-spec machine on the same Tailnet exists for work that is too
heavy for the current host. Offload to it instead of grinding the local
machine, or the Beeper host, which should stay responsive.

Set the host in `~/.ssh/config` (or a shell alias) under `<workstation-host>`
and substitute it everywhere below.

## Reach it

```sh
tailscale status | grep -w <workstation-host>          # online?
ssh <workstation-host> -- 'hostname; nproc; free -g | head -2; nvidia-smi -L'
```

- Login: `ssh <workstation-host>` (key-based; see `~/.ssh/config`).
- If Tailscale SSH answers with "requires an additional check", open the
  printed `login.tailscale.com` URL once to re-authorise, then retry.
- Always probe reachability before assuming a job can run there.

## Run work

One-off command:

```sh
ssh <workstation-host> -- 'cd ~/work/project && <command>'
```

Longer jobs — copy the repo over, then run detached so the SSH session can
drop:

```sh
rsync -az --exclude .git ./ <workstation-host>:~/work/project/
ssh <workstation-host> -- 'cd ~/work/project && nohup <command> >run.log 2>&1 & echo STARTED $!'
ssh <workstation-host> -- 'tail -n 40 ~/work/project/run.log'   # poll sparingly
```

Or start a `tmux` session there and attach later. Bring results back the same
way (`rsync` from `<workstation-host>:...`).

## Etiquette and constraints

- **Do not install system packages without asking.**
- Keep the box available; don't leave runaway jobs or saturate the GPU
  indefinitely.
- Send only the context a job needs; treat the code and data as private.
- Prefer real runs over guesses: check exit codes and read logs rather than
  assuming a remote job succeeded.

## T3 note

T3 Connect connects clients *to* an environment for interaction; it is not a
job scheduler. For offloading compute, use SSH as above. T3 worktrees are
local to the host that owns them.
