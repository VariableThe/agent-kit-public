---
name: tailnet-transfer
description: Move files across tailnet. Triggers: tailnet file transfer, Taildrop, rsync, share file.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: tailscale-cli, openssh, rsync, tailshare
---

# Tailnet Transfer

One entry point for moving files between this host and the other devices on
the tailnet: `scripts/tailnet_transfer.py`. It resolves peers from
`tailscale status --json`, picks the mechanism that fits the payload, and
never invents a URL.

## Purpose

Three ways to move a file across the tailnet, one CLI:

- `send` picks Taildrop, rsync, or scp from peer state, payload size, and
  what each end actually supports.
- `recv` drains the Taildrop inbox on this host into a directory.
- `serve` exposes one directory over HTTPS on the tailnet for the duration
  of the command, then removes its own mapping.

Scripts are stdlib-only: `python3 scripts/tailnet_transfer.py --help` works
with zero setup.

## Device inventory

The tailnet's peers are resolved at runtime from `tailscale status --json`;
this skill ships no fixed device list. Run:

```sh
python3 scripts/tailnet_transfer.py peers
```

to print your own inventory (hostname, OS, online state, IP, SSH
reachability, and receive path). Re-check before promising a transfer —
`peers` is cheap.

## Mechanisms

| Mechanism | Needs | Best for | Limits |
| --- | --- | --- | --- |
| Taildrop (`tailscale file cp`) | tailscale on both ends, file sharing enabled by ACL | single files or directories to any peer, including phones, macOS, and offline peers | queued by tailscaled, no delivery confirmation; permissions and timestamps are not preserved |
| rsync over SSH | online peer, your SSH key accepted, rsync on both ends | bulk, resumable, directories, `--bwlimit`, sha256 verification | needs a key on the peer; not available for macOS or phones |
| scp over SSH | online peer, your SSH key accepted | same as rsync when local rsync is missing | no resume, no partial files; `--bwlimit` is converted to scp's Kbit/s |
| tailshare HTTP | `tailshare` on PATH plus a foreground server on this host | pulling a file with curl/wget from another machine | this host must stay up; tailnet only, never funnel; access is tailnet membership |

The helper targets Taildrop by peer IP, not hostname: MagicDNS resolution is
unreliable on this host. SSH paths use the peer's short DNS name so any
`~/.ssh/config` alias (user, options) applies.

What the helper actually runs, so a failure can be reproduced by hand:

```sh
# send --via taildrop (IP target; queued, not delivered)
tailscale file cp FILE... <peer-ip>:

# send --via rsync (--partial keeps a resumable fragment; --bwlimit KB/s)
rsync -a --partial [--bwlimit=20000] SRC... <peer>:/tmp/incoming/

# send --via scp (--bwlimit is converted to scp's Kbit/s)
scp -r [-l KBPS*8] SRC... <peer>:/tmp/incoming/

# verification after rsync/scp: sha256 on both ends, then `Checksums: OK`
ssh <peer> -- 'cd /tmp/incoming && sha256sum -- FILE'

# recv: drain the inbox, however long it takes
tailscale file get --conflict=skip [--wait] [--loop] DEST

# serve: loopback HTTP server plus one tailshare mapping, removed on exit
python3 -m http.server 8765 --bind 127.0.0.1 --directory DIR
tailshare share 8765 --path /tt     # prints https://host.<tailnet>.ts.net/tt
```

## Auto-selection

`send` chooses in this order:

1. Peer not in `tailscale status --json`, or matches more than one node:
   `Error:` and exit 1.
2. Peer offline → Taildrop (queued until it returns).
3. Bulk payload — a directory, more than one source, `--resume`, or more
   than 50 MiB total — plus a passing SSH probe plus rsync locally → rsync.
4. Bulk plus SSH but no local rsync → scp (skipped if scp is missing too).
5. Anything else → Taildrop; a single small file needs no SSH key.

`--via {auto,taildrop,rsync,scp}` overrides the decision and validates it:
offline peer plus `--via rsync` fails with a pointer back to Taildrop, and a
failed SSH probe fails instead of silently degrading. Add `--dry-run` to see
the choice and its reason without moving anything.

## Usage

```sh
S=scripts/tailnet_transfer.py

# Inventory: peer, OS, online state, IP, SSH reachability, receive path
python3 $S peers
python3 $S peers --online-only --json | python3 -m json.tool

# Small file to a peer with no SSH key (phone, a Mac) or any offline peer
python3 $S send photo.jpg --to an-android-phone

# Bulk or resumable: directory over rsync with sha256 on both ends
python3 $S send ~/Videos/clip.mp4 --to <peer> --dest /tmp/incoming/ --resume
python3 $S send ./dataset --to <peer> --via rsync --bwlimit 20000

# Drain the inbox on a headless Linux box (no daemon is installed there)
ssh <peer> -- 'timeout 20 tailscale file get --wait /tmp/inbox'

# This host: receive as files arrive, overwrite name conflicts
python3 $S recv ~/Downloads --loop --conflict=overwrite

# Share a directory over HTTPS until Ctrl-C
python3 $S serve /tmp/release --port 8765 --path /tt
```

`send` prints one summary line (`Checksums: OK` after rsync/scp verification)
or `Error:` on stderr with exit 1. Taildrop lines say queued, never delivered.

Every subcommand fails with a single `Error: <reason>` line on stderr and
exit 1 — subprocess failures quote the first stderr line of the tool that
failed, and a missing binary reads `Error: <bin> not found on PATH.` Invalid
flags exit 2 (argparse). `recv --loop` and `serve` leave 130 and 0 on SIGINT,
respectively.

## Receive paths by OS

| Peer OS | Where files land | Notes |
| --- | --- | --- |
| Linux | Taildrop inbox | `recv DEST` drains it; nothing drains it automatically |
| macOS | `~/Downloads` via the Tailscale app | app must be running; conflicts renamed by the app |
| Android | app share sheet | accept the notification; queued while offline |
| Windows | `Downloads` | same Taildrop inbox as Linux |

Headless Linux peers receive nothing until someone runs `recv` there; no
system daemon is installed for it. Long-lived receivers use `recv DEST
--loop`, one-shot receivers `recv DEST --wait`, with `--conflict=rename` when
the inbox keeps a duplicate name it will not overwrite.

## Offline peers

Taildrop queues while the peer is away, so `send` succeeds against an offline
phone or laptop — with no proof of arrival. There is no receipt command; the
only confirmation is the receiver running `recv` and finding the file. For
anything that must be known-delivered, wait for the peer to come online and
use rsync (checksums) or serve (the receiver fetches it).

## Troubleshooting

- **MagicDNS broken on this host.** `tailscale status --json` resolves names,
  `curl`/`ssh` may not. Use IPs: `send ... --to <peer-ip>`, and fetch a
  served file with `curl --resolve host.<tailnet>.ts.net:443:<workstation-ip>`.
- **No SSH key on the peer.** `peers` shows `SSH no`; rsync and scp
  fail by design. Use `--via taildrop`, or `serve` and pull from that machine.
- **Peer unreachable.** Peer offline → Taildrop queues it. Online but SSH
  fails → `recv --loop` on Linux peers, app on macOS.
- **Taildrop refused (ACL or disabled).** `send` fails with
  `does not accept files: <reason>`; fix file sharing in the tailnet ACL.
  Don't retry in a loop.
- **Headless inbox never drains.** Nothing runs `recv` there. Either
  `ssh <peer> -- 'tailscale file get --loop /tmp/inbox'` or send over rsync,
  which writes to the destination directly.
- **macOS shows nothing.** The Tailscale app must be running and signed in;
  check its menu for a pending file, then accept.
- **Android share sheet.** Files arrive as a notification; queued copies show
  up once the phone reconnects.
- **Large or interrupted transfer.** `send <src> --to <peer> --resume --via rsync`
  reuses the partial file; `Checksums: OK` confirms whole-file equality.
  Taildrop cannot resume.
- **Permissions lost.** Taildrop writes plain files (no mode, no owner);
  rsync `-a` preserves both, so use rsync for anything executable.
- **Name conflicts on receive.** `recv` defaults to `--conflict=skip` and
  reports it; pass `--conflict=rename` to keep both files.
- **Serve port in use.** `Error: port N is already in use` — pass
  `--port 8799`. The Tailshare mapping lands on 443 with `--path`, so the URL
  does not carry the local port.
- **Serve URL unreachable.** `tailshare list` shows the live mapping; a
  missing `/tt` entry means the mapping is gone, a wrong target means another
  process owns the port. Never enable funnel: tailnet-only is the point.
