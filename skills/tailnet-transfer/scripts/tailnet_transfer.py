#!/usr/bin/env python3
"""Tailnet file transfer: peers, send, recv, serve.

Moves files between this host and other devices on the Tailscale tailnet
over three mechanisms: Taildrop (queued by tailscaled, no delivery
confirmation), rsync/scp over SSH, and a temporary tailshare HTTP share.

Standard library only. Needs `tailscale` on PATH, plus `ssh` and
`rsync`/`scp` for the SSH paths and `tailshare` for `serve`.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

BULK_BYTES = 50 * 1024 * 1024
PROBE_TIMEOUT = 5
DEFAULT_PORT = 8765
CHECKSUM_BATCH = 200

# Machine-local receive hints keyed by the OS string in `tailscale status --json`.
RECEIVE_HINT = {
    "linux": "inbox (recv)",
    "macos": "~/Downloads / app",
    "android": "app share sheet",
    "windows": "Downloads",
    "ios": "app share sheet",
}
OS_LABEL = {"macos": "macOS", "ios": "iOS"}


class TransferError(Exception):
    """User-facing failure: printed as `Error: <message>`, exit 1."""


@dataclass
class Peer:
    name: str  # DNS short name: stable, matches ~/.ssh/config aliases
    hostname: str  # status HostName as reported by tailscaled
    dns_name: str
    os: str  # lowercased OS key
    online: bool
    ip: str
    last_seen: str
    files_ok: bool
    files_reason: str

    @property
    def receive(self) -> str:
        return RECEIVE_HINT.get(self.os, "unknown")

    @property
    def os_label(self) -> str:
        return OS_LABEL.get(self.os, self.os.capitalize() or "unknown")


# --------------------------------------------------------------------------
# process helpers


def require_bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise TransferError(f"{name} not found on PATH.")
    return path


def port_in_use(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def first_stderr_line(proc: subprocess.CompletedProcess) -> str:
    text = (proc.stderr or "").strip()
    if text:
        return text.splitlines()[0]
    return (proc.stdout or "").strip().splitlines()[0] if (proc.stdout or "").strip() else "no output"


def run(cmd: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run cmd; any failure becomes a TransferError carrying the first stderr line."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise TransferError(f"{cmd[0]} not found on PATH.") from None
    except subprocess.TimeoutExpired:
        raise TransferError(f"{cmd[0]} timed out after {timeout}s.") from None
    if proc.returncode != 0:
        raise TransferError(f"{cmd[0]} failed ({proc.returncode}): {first_stderr_line(proc)}")
    return proc


def stream_run(cmd: list[str]) -> tuple[int, str]:
    """Run cmd with inherited-stream semantics; return (rc, first stderr line)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    seen: list[str] = []

    def pump(stream, sink, collect: bool) -> None:
        for line in stream:
            if collect and not seen:
                seen.append(line.rstrip())
            print(line, end="", file=sink, flush=True)

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, sys.stdout, False), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, sys.stderr, True), daemon=True),
    ]
    for t in threads:
        t.start()
    try:
        rc = proc.wait()
    except KeyboardInterrupt:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        sys.exit(130)
    for t in threads:
        t.join()
    return rc, (seen[0] if seen else "")


# --------------------------------------------------------------------------
# tailnet state


def load_peers() -> list[Peer]:
    require_bin("tailscale")
    raw = run(["tailscale", "status", "--json"]).stdout
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise TransferError("could not parse `tailscale status --json` output.")
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            raise TransferError("could not parse `tailscale status --json` output.") from None
    peers: list[Peer] = []
    for node in (data.get("Peer") or data.get("Peers") or {}).values():
        dns = node.get("DNSName") or ""
        ips = node.get("TailscaleIPs") or []
        reason = node.get("NoFileSharingReason") or ""
        peers.append(
            Peer(
                name=dns.rstrip(".").split(".")[0] or node.get("HostName", ""),
                hostname=node.get("HostName") or dns.rstrip("."),
                dns_name=dns,
                os=(node.get("OS") or "").lower(),
                online=bool(node.get("Online")),
                ip=ips[0] if ips else "",
                last_seen=node.get("LastSeen") or "",
                files_ok=not reason,
                files_reason=reason,
            )
        )
    return sorted(peers, key=lambda p: p.name.lower())


def match_peers(peers: list[Peer], needle: str) -> list[Peer]:
    key = needle.strip().lower()
    hits: list[Peer] = []
    for peer in peers:
        names = {peer.name.lower(), peer.hostname.lower(), peer.dns_name.lower(), peer.dns_name.rstrip(".").lower()}
        if key in names or (peer.ip and key == peer.ip.lower()):
            if peer not in hits:
                hits.append(peer)
    return hits


def resolve_peer(peers: list[Peer], needle: str) -> Peer:
    hits = match_peers(peers, needle)
    if not hits:
        raise TransferError(f"unknown peer '{needle}' (run `peers` for the inventory).")
    if len(hits) > 1:
        raise TransferError(
            f"ambiguous peer '{needle}': {', '.join(p.name for p in hits)} (use the DNS name)."
        )
    return hits[0]


def probe_ssh(target: str, timeout: int) -> bool:
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", target, "true"],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def probe_peers(peers: list[Peer], timeout: int) -> dict[str, bool]:
    """SSH reachability per peer name, in parallel. Online peers only."""
    targets = [p for p in peers if p.online]
    if not targets or not shutil.which("ssh"):
        return {}
    results: dict[str, bool] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        futures = {pool.submit(probe_ssh, p.name, timeout): p for p in targets}
        for future in concurrent.futures.as_completed(futures):
            results[futures[future].name] = future.result()
    return results


# --------------------------------------------------------------------------
# paths and hashes


def measure(sources: list[Path]) -> tuple[int, bool]:
    """(total bytes, any directory) for the given sources."""
    total = 0
    has_dir = False
    for src in sources:
        if src.is_dir():
            has_dir = True
            for path in src.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    try:
                        total += path.stat().st_size
                    except OSError:
                        pass
        elif src.is_file():
            total += src.stat().st_size
    return total, has_dir


def transfer_items(sources: list[Path]) -> list[tuple[Path, str]]:
    """(local path, path relative to the remote destination) for every file sent."""
    items: list[tuple[Path, str]] = []
    for src in sources:
        if src.is_dir():
            for path in sorted(src.rglob("*")):
                if path.is_file():
                    items.append((path, f"{src.name}/{path.relative_to(src).as_posix()}"))
        else:
            items.append((src, src.name))
    return items


def remote_dir_expr(dest: str) -> str:
    """Shell expression for a remote directory, expanding a leading `~`."""
    if dest == "~" or dest.startswith("~/"):
        rest = dest[2:] if dest.startswith("~/") else ""
        return f'"${{HOME}}/{rest}"' if rest else '"${HOME}"'
    return shlex.quote(dest)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remote_sha256(target: str, dest: str, rels: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for start in range(0, len(rels), CHECKSUM_BATCH):
        batch = rels[start : start + CHECKSUM_BATCH]
        files = " ".join(shlex.quote(rel) for rel in batch)
        cmd = (
            "if command -v sha256sum >/dev/null 2>&1; then "
            f"cd {remote_dir_expr(dest)} && sha256sum -- {files}; "
            f"else cd {remote_dir_expr(dest)} && shasum -a 256 -- {files}; fi"
        )
        proc = run(["ssh", "-o", "BatchMode=yes", target, cmd])
        for line in proc.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and len(parts[0]) == 64:
                out[parts[1].lstrip("*").strip()] = parts[0]
    return out


def verify_transfer(target: str, sources: list[Path], dest: str) -> None:
    items = transfer_items(sources)
    local = {rel: sha256_file(path) for path, rel in items}
    remote = remote_sha256(target, dest, list(local))
    for rel, digest in local.items():
        other = remote.get(rel)
        if other is None:
            raise TransferError(f"checksum missing on remote: {rel}")
        if other != digest:
            raise TransferError(f"checksum mismatch for {rel}: local {digest[:12]} remote {other[:12]}")
    print("Checksums: OK")


# --------------------------------------------------------------------------
# mechanisms


def send_taildrop(peer: Peer, sources: list[Path]) -> None:
    require_bin("tailscale")
    if not peer.files_ok:
        raise TransferError(f"{peer.name} does not accept files: {peer.files_reason}")
    if not peer.ip:
        raise TransferError(f"{peer.name} has no tailnet IP to target.")
    # MagicDNS is unreliable on this host: target the peer by IP.
    run(["tailscale", "file", "cp", *[str(s) for s in sources], f"{peer.ip}:"])
    print(
        f"Queued {len(sources)} item(s) via Taildrop to {peer.name} ({peer.ip}) - not delivered yet; "
        f"{peer.name} receives them with `tailscale file get`."
    )


def send_rsync(peer: Peer, sources: list[Path], dest: str, bwlimit: int | None) -> None:
    require_bin("rsync")
    # --partial keeps the incomplete file in place, so a retry resumes it.
    cmd = ["rsync", "-a", "--partial"]
    if bwlimit:
        cmd.append(f"--bwlimit={bwlimit}")
    cmd += [str(s) for s in sources]
    cmd.append(f"{peer.name}:{dest}")
    run(cmd, timeout=None)
    print(f"Copied {len(sources)} item(s) to {peer.name}:{dest} via rsync.")


def send_scp(peer: Peer, sources: list[Path], dest: str, bwlimit: int | None) -> None:
    require_bin("scp")
    cmd = ["scp", "-r"]
    if bwlimit:
        cmd += ["-l", str(bwlimit * 8)]  # scp -l is Kbit/s
    cmd += [str(s) for s in sources]
    cmd.append(f"{peer.name}:{dest}")
    run(cmd, timeout=None)
    print(f"Copied {len(sources)} item(s) to {peer.name}:{dest} via scp.")


def choose_via(peer: Peer, bulk: bool, ssh_ok: bool) -> tuple[str, str]:
    if not peer.online:
        return "taildrop", "peer is offline, Taildrop queues it"
    if bulk:
        if ssh_ok and shutil.which("rsync"):
            return "rsync", "bulk over SSH with resume and checksums"
        if ssh_ok and shutil.which("scp"):
            return "scp", "bulk over SSH, local rsync missing"
        return "taildrop", "no SSH path available"
    return "taildrop", "single small file, no SSH key needed"


def validate_via(via: str, peer: Peer, ssh_ok: bool) -> None:
    if via == "taildrop":
        if not peer.files_ok:
            raise TransferError(f"{peer.name} does not accept files: {peer.files_reason}")
        return
    if not peer.online:
        raise TransferError(f"{peer.name} is offline; {via} needs an online peer (use --via taildrop).")
    if not shutil.which("ssh"):
        raise TransferError("ssh not found on PATH.")
    if not ssh_ok:
        raise TransferError(
            f"ssh to {peer.name} failed in batch mode (no key or unreachable; use --via taildrop)."
        )
    if via == "rsync":
        require_bin("rsync")
    else:
        require_bin("scp")


# --------------------------------------------------------------------------
# commands


def cmd_peers(args: argparse.Namespace) -> None:
    peers = load_peers()
    if args.online_only:
        peers = [p for p in peers if p.online]
    probed = {} if args.no_probe else probe_peers(peers, args.timeout)
    if args.json:
        payload = {
            "count": len(peers),
            "online": sum(1 for p in peers if p.online),
            "peers": [
                {
                    "name": p.name,
                    "hostname": p.hostname,
                    "dns_name": p.dns_name,
                    "os": p.os,
                    "online": p.online,
                    "ip": p.ip,
                    "ssh": probed.get(p.name),
                    "receive": p.receive,
                    "last_seen": p.last_seen,
                }
                for p in peers
            ],
        }
        print(json.dumps(payload, indent=2))
        print(f"Listed {len(peers)} peers ({payload['online']} online).", file=sys.stderr)
        return
    rows = [
        (
            p.name,
            p.os_label,
            "online" if p.online else "offline",
            p.ip or "-",
            ("yes" if probed[p.name] else "no") if p.name in probed else "-",
            p.receive,
        )
        for p in peers
    ]
    headers = ("HOSTNAME", "OS", "STATUS", "IP", "SSH", "RECEIVE")
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i]) for i in range(len(headers))]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))).rstrip())
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))).rstrip())
    print(f"Listed {len(peers)} peers ({sum(1 for p in peers if p.online)} online).")


def cmd_send(args: argparse.Namespace) -> None:
    sources = [Path(s).expanduser() for s in args.files]
    missing = [str(s) for s in sources if not s.exists()]
    if missing:
        raise TransferError(f"source not found: {', '.join(missing)}")
    if args.bwlimit is not None and args.bwlimit <= 0:
        raise TransferError(f"bad --bwlimit {args.bwlimit} (use KB/s > 0).")
    peer = resolve_peer(load_peers(), args.to)
    total, has_dir = measure(sources)
    bulk = has_dir or len(sources) > 1 or args.resume or total > BULK_BYTES
    # The probe is only needed when the mechanism depends on it.
    need_probe = args.via in ("rsync", "scp") or (args.via == "auto" and bulk)
    ssh_ok = peer.online and need_probe and bool(shutil.which("ssh")) and probe_ssh(peer.name, PROBE_TIMEOUT)

    if args.via == "auto":
        via, why = choose_via(peer, bulk, ssh_ok)
    else:
        via = args.via
        why = "explicit --via"
        validate_via(via, peer, ssh_ok)

    plan = f"{via} -> {peer.name} ({peer.ip or 'no ip'}), {len(sources)} item(s), {total / 1048576:.1f} MiB"
    if args.dry_run:
        print(f"Dry run: would use {plan} [{why}]")
        return

    if via == "taildrop":
        send_taildrop(peer, sources)
        return
    if via == "rsync":
        send_rsync(peer, sources, args.dest, args.bwlimit)
    else:
        send_scp(peer, sources, args.dest, args.bwlimit)
    if args.verify:
        verify_transfer(peer.name, sources, args.dest)
    else:
        print("Checksums: skipped (--no-verify).")


def cmd_recv(args: argparse.Namespace) -> None:
    require_bin("tailscale")
    dest = Path(args.dest).expanduser()
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TransferError(f"cannot create {dest}: {exc.strerror}") from None
    cmd = ["tailscale", "file", "get", f"--conflict={args.conflict}"]
    if args.wait:
        cmd.append("--wait")
    if args.loop:
        cmd.append("--loop")
    cmd.append(str(dest))
    rc, err = stream_run(cmd)
    if rc != 0:
        raise TransferError(f"tailscale file get failed ({rc}): {err or 'see output above'}")
    print(f"Received files into {dest}.")


def cmd_serve(args: argparse.Namespace) -> None:
    tailshare = require_bin("tailshare")
    src = Path(args.path).expanduser()
    if not src.is_dir():
        raise TransferError(f"serve path is not a directory: {args.path}")
    src = src.resolve()
    port = args.port
    if not 1 <= port <= 65535:
        raise TransferError(f"bad --port {port} (use 1-65535).")
    if port_in_use(port):
        raise TransferError(f"port {port} is already in use on 127.0.0.1 (pass --port to pick another).")
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(src)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def stop_server() -> int | None:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
        return server.returncode

    try:
        deadline = time.monotonic() + 5
        listening = False
        while time.monotonic() < deadline:
            if server.poll() is not None:
                break
            if port_in_use(port):
                listening = True
                break
            time.sleep(0.1)
        if not listening:
            raise TransferError(f"http.server did not start on 127.0.0.1:{port} (port already in use?)")

        share_cmd = [tailshare, "share", str(port)]
        if args.url_path:
            share_cmd += ["--path", args.url_path]
        shared = subprocess.run(share_cmd, capture_output=True, text=True)
        for line in (shared.stderr or "").splitlines():
            print(line, file=sys.stderr)
        if shared.returncode != 0:
            raise TransferError(f"tailshare share failed: {first_stderr_line(shared)}")
        url = next((line.strip() for line in (shared.stdout or "").splitlines() if line.strip()), "")
        if not url.startswith("https://") or "127.0.0.1" in url or "localhost" in url:
            subprocess.run([tailshare, "unshare", str(port)], capture_output=True, text=True)
            raise TransferError(f"tailshare returned a non-tailnet URL: {url or '(none)'}")

        print(url, flush=True)
        print(f"Serving {src} on 127.0.0.1:{port} - Ctrl-C to stop.", file=sys.stderr)

        stop = threading.Event()

        def on_signal(_signum, _frame) -> None:
            stop.set()

        previous = (signal.signal(signal.SIGINT, on_signal), signal.signal(signal.SIGTERM, on_signal))
        try:
            while not stop.is_set() and server.poll() is None:
                stop.wait(0.5)
            exited_early = server.poll()
        finally:
            signal.signal(signal.SIGINT, previous[0])
            signal.signal(signal.SIGTERM, previous[1])
            stop_server()
            unshare = subprocess.run([tailshare, "unshare", str(port)], capture_output=True, text=True)
            if unshare.returncode != 0:
                print(f"Warning: tailshare unshare {port} failed: {first_stderr_line(unshare)}", file=sys.stderr)
        if exited_early is not None and not stop.is_set():
            raise TransferError(f"http.server exited early with code {exited_early}.")
    except Exception:
        stop_server()
        raise


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="tailnet_transfer.py",
        description="Move files between Tailscale tailnet devices (Taildrop, rsync/scp over SSH, tailshare HTTP).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("peers", aliases=["list", "status"], help="List tailnet devices and what each accepts.")
    p.add_argument("--json", action="store_true", help="Machine-readable output (success line goes to stderr).")
    p.add_argument("--online-only", action="store_true", help="Hide offline peers.")
    p.add_argument("--no-probe", action="store_true", help="Skip the SSH reachability probe.")
    p.add_argument("--timeout", type=int, default=PROBE_TIMEOUT, help="SSH probe timeout in seconds (default 5).")
    p.set_defaults(fn=cmd_peers)

    s = sub.add_parser("send", help="Send files or directories to a peer.")
    s.add_argument("files", nargs="+", help="Local files and/or directories.")
    s.add_argument("--to", required=True, help="Peer name, DNS name, or tailnet IP.")
    s.add_argument("--via", choices=["auto", "taildrop", "rsync", "scp"], default="auto")
    s.add_argument("--dest", default="~/", help="Remote destination directory for rsync/scp (default ~/).")
    s.add_argument("--resume", action="store_true", help="Resume an interrupted transfer (forces the bulk path).")
    s.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True, help="sha256 both sides after rsync/scp (default on).")
    s.add_argument("--bwlimit", type=int, default=None, metavar="KBPS", help="Bandwidth limit in KB/s.")
    s.add_argument("--dry-run", action="store_true", help="Print the chosen mechanism without transferring.")
    s.set_defaults(fn=cmd_send)

    r = sub.add_parser("recv", help="Drain the Taildrop inbox into a directory.")
    r.add_argument("dest", help="Local directory to receive into (created if missing).")
    r.add_argument("--wait", action="store_true", help="Block until a file arrives.")
    r.add_argument("--loop", action="store_true", help="Keep receiving until interrupted (exit 130 on SIGINT).")
    r.add_argument("--conflict", choices=["skip", "overwrite", "rename"], default="skip")
    r.set_defaults(fn=cmd_recv)

    v = sub.add_parser("serve", help="Share a directory over HTTPS on the tailnet until Ctrl-C.")
    v.add_argument("path", help="Directory to serve.")
    v.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Local http.server port (default {DEFAULT_PORT}).")
    v.add_argument("--path", dest="url_path", metavar="/NAME", help="URL path for the share (default /p<port>).")
    v.set_defaults(fn=cmd_serve)

    return ap


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.fn(args)
    except TransferError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
