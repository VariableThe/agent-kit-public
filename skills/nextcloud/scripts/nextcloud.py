#!/usr/bin/env python3
"""Nextcloud file + share-link operations over WebDAV and the OCS sharing API.

One self-contained CLI for a Nextcloud instance:
list, read, write, edit, delete, and move files over WebDAV, and create /
list / update / delete public link shares over OCS.

Standard library only. Credentials come from the NC_URL / NC_USER / NC_PASS
environment variables, or from the `[nextcloud]` section of
~/.config/rclone/rclone.conf (rclone stores `pass` obscured; this script
decrypts it with the same AES-256-CTR scheme rclone uses).
"""
from __future__ import annotations

import argparse
import base64
import configparser
import email.utils
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 30
WRITE_TIMEOUT = 120      # PUTs send the whole body first; Nextcloud can be slow to acknowledge
RETRIES = 2              # retry count on transient failures (total attempts = RETRIES + 1)
RETRY_BACKOFF = (1.0, 2.0)
RATE_LIMIT_BACKOFF = 10.0  # OCS rate-limits share creation to ~1 per 10s
# Some Nextcloud instances fronted by Cloudflare treat urllib's default User-Agent, and any
# browser-style UA, as a bot: it blocks them (403 "error code: 1010") or hangs
# the connection intermittently. A curl-like UA matches a non-browser client
# fingerprint and passes cleanly (verified: 8/8 rapid requests succeed).
USER_AGENT = "curl/8.23.0"
RCLONE_CONF = Path.home() / ".config" / "rclone" / "rclone.conf"
DAV_NS = "{DAV:}"
OCS_BASE_PATH = "/ocs/v2.php/apps/files_sharing/api/v1/shares"

# Public-link permission values Nextcloud accepts via the OCS API.
PERMS = {"read": 1, "upload": 15, "download": 19}


class NextcloudError(Exception):
    """User-facing failure: printed as `Error: <message>`, exit 1."""


@dataclass
class Config:
    url: str       # WebDAV root, ends in .../dav/files/<user>/
    user: str
    password: str
    origin: str    # scheme://host derived from url


# --------------------------------------------------------------------------
# rclone obscure (AES-256-CTR) — mirrors fs/config/obscure/obscure.go


SBOX = [
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
]
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]
CRYPT_KEY = bytes([
    0x9C, 0x93, 0x5B, 0x48, 0x73, 0x0A, 0x55, 0x4D,
    0x6B, 0xFD, 0x7C, 0x63, 0xC8, 0x86, 0xA9, 0x2B,
    0xD3, 0x90, 0x19, 0x8E, 0xB8, 0x12, 0x8A, 0xFB,
    0xF4, 0xDE, 0x16, 0x2B, 0x8B, 0x95, 0xF6, 0x38,
])


def _xtime(x: int) -> int:
    x <<= 1
    if x & 0x100:
        x ^= 0x11B
    return x & 0xFF


def _gf_mul(a: int, b: int) -> int:
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _key_expansion(key: bytes) -> list[list[int]]:
    nk, nr = 8, 14  # AES-256
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        temp = list(w[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[(i // nk) - 1]
        elif nk > 6 and i % nk == 4:
            temp = [SBOX[b] for b in temp]
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    return w


def _aes_encrypt_block(block: bytes, w: list[list[int]]) -> bytes:
    state = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
    for c in range(4):
        for r in range(4):
            state[r][c] ^= w[c][r]
    for rnd in range(1, 14):
        for r in range(4):
            for c in range(4):
                state[r][c] = SBOX[state[r][c]]
        for r in range(1, 4):
            state[r] = state[r][r:] + state[r][:r]
        for c in range(4):
            a = [state[r][c] for r in range(4)]
            state[0][c] = _gf_mul(a[0], 2) ^ _gf_mul(a[1], 3) ^ a[2] ^ a[3]
            state[1][c] = a[0] ^ _gf_mul(a[1], 2) ^ _gf_mul(a[2], 3) ^ a[3]
            state[2][c] = a[0] ^ a[1] ^ _gf_mul(a[2], 2) ^ _gf_mul(a[3], 3)
            state[3][c] = _gf_mul(a[0], 3) ^ a[1] ^ a[2] ^ _gf_mul(a[3], 2)
        for c in range(4):
            for r in range(4):
                state[r][c] ^= w[rnd * 4 + c][r]
    for r in range(4):
        for c in range(4):
            state[r][c] = SBOX[state[r][c]]
    for r in range(1, 4):
        state[r] = state[r][r:] + state[r][:r]
    for c in range(4):
        for r in range(4):
            state[r][c] ^= w[14 * 4 + c][r]
    return bytes(state[r][c] for c in range(4) for r in range(4))


def _aes_ctr_decrypt(iv: bytes, data: bytes) -> bytes:
    w = _key_expansion(CRYPT_KEY)
    ctr = int.from_bytes(iv, "big")
    out = bytearray()
    for i in range(0, len(data), 16):
        ks = _aes_encrypt_block((ctr + i // 16).to_bytes(16, "big"), w)
        out += bytes(b ^ k for b, k in zip(data[i:i + 16], ks))
    return bytes(out)


def reveal_obscured(value: str) -> str:
    """Decrypt an rclone-obscured password; return plaintext as-is if not obscured."""
    pad = "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(value + pad)
    except (ValueError, base64.binascii.Error):
        return value
    if len(raw) < 16:
        return value
    try:
        return _aes_ctr_decrypt(raw[:16], raw[16:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return value


# --------------------------------------------------------------------------
# configuration


def load_config() -> Config:
    if all(v in os.environ for v in ("NC_URL", "NC_USER", "NC_PASS")):
        url = os.environ["NC_URL"].rstrip("/") + "/"
        user = os.environ["NC_USER"]
        password = os.environ["NC_PASS"]
    else:
        if not RCLONE_CONF.exists():
            raise NextcloudError(
                "no credentials: set NC_URL/NC_USER/NC_PASS or add a [nextcloud] "
                "section to ~/.config/rclone/rclone.conf"
            )
        cp = configparser.ConfigParser()
        cp.read(RCLONE_CONF)
        if "nextcloud" not in cp:
            raise NextcloudError(f"no [nextcloud] section in {RCLONE_CONF}")
        sec = cp["nextcloud"]
        type_ = sec.get("type", "").strip().lower()
        vendor = sec.get("vendor", "").strip().lower()
        if type_ and type_ != "webdav":
            raise NextcloudError(f"[nextcloud] type is '{sec['type']}', expected 'webdav'")
        if vendor and vendor != "nextcloud":
            raise NextcloudError(f"[nextcloud] vendor is '{sec['vendor']}', expected 'nextcloud'")
        for key in ("url", "user", "pass"):
            if key not in sec:
                raise NextcloudError(f"[nextcloud] section is missing '{key}' in {RCLONE_CONF}")
        url = sec["url"].rstrip("/") + "/"
        user = sec["user"]
        password = reveal_obscured(sec["pass"])

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise NextcloudError(f"invalid WebDAV url: {url}")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return Config(url=url, user=user, password=password, origin=origin)


def ocs_base(cfg: Config) -> str:
    return cfg.origin + OCS_BASE_PATH


# --------------------------------------------------------------------------
# HTTP


def basic_token(cfg: Config) -> str:
    raw = f"{cfg.user}:{cfg.password}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _first_line(body: bytes) -> str:
    text = body.decode("utf-8", "replace").strip()
    return text.splitlines()[0] if text else ""


def _transient_http(code: int, body: bytes) -> bool:
    """True for errors worth retrying: rate-limit, server error, CF browser challenge."""
    return code == 429 or code >= 500 or (code == 403 and b"1010" in body)


def request(cfg: Config, method: str, url: str, *, body: bytes | None = None,
            headers: dict[str, str] | None = None, auth: bool = True) -> bytes:
    timeout = WRITE_TIMEOUT if body is not None else TIMEOUT
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json, text/plain, */*")
        if auth:
            req.add_header("Authorization", "Basic " + basic_token(cfg))
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read()
            if e.code == 401:
                raise NextcloudError(
                    "HTTP 401 Unauthorized — check credentials in ~/.config/rclone/rclone.conf"
                ) from None
            if _transient_http(e.code, detail) and attempt < RETRIES:
                attempt += 1
                time.sleep(RATE_LIMIT_BACKOFF if e.code == 429 else RETRY_BACKOFF[attempt - 1])
                continue
            line = _first_line(detail)
            raise NextcloudError(f"HTTP {e.code}: {line}" if line else f"HTTP {e.code}") from None
        except OSError as e:
            # timeouts, connection resets, and URLError all surface here
            if body is not None and method == "PUT" and _write_landed(cfg, url, body):
                return b""
            if attempt < RETRIES:
                attempt += 1
                time.sleep(RETRY_BACKOFF[attempt - 1])
                continue
            raise NextcloudError(f"request failed: {e}") from None


def _remote_size(cfg: Config, url: str) -> int | None:
    """Return the remote file's size via PROPFIND Depth 0, or None if unknown."""
    try:
        body = request(cfg, "PROPFIND", url, headers={"Depth": "0"})
    except NextcloudError:
        return None
    entries = parse_multistatus(body, url)
    return entries[0]["size"] if entries else None


def _write_landed(cfg: Config, url: str, body: bytes) -> bool:
    """After a PUT times out, confirm the body reached the server.

    Nextcloud (behind Cloudflare) can store a large PUT and then be slow to send
    its 201, so the response read times out even though the file landed. Compare
    the remote size against what we sent to tell that case apart from a real
    failure (short write, connection reset before send, or absent file).
    """
    if _remote_size(cfg, url) == len(body):
        print("(verified via PROPFIND)", file=sys.stderr)
        return True
    return False


def ocs(cfg: Config, method: str, url: str, params: dict | None = None) -> dict | list:
    headers = {"OCS-APIRequest": "true"}
    if params is None:
        params = {}
    sep = "&" if "?" in url else "?"
    url += f"{sep}format=json"
    body: bytes | None = None
    if method in ("POST", "PUT"):
        body = urllib.parse.urlencode(params).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif params:
        url += "&" + urllib.parse.urlencode(params)
    payload = json.loads(request(cfg, method, url, body=body, headers=headers).decode("utf-8"))
    meta = payload.get("ocs", {}).get("meta", {})
    if meta.get("status") != "ok":
        raise NextcloudError(f"OCS error {meta.get('statuscode')}: {meta.get('message')}")
    return payload.get("ocs", {}).get("data")


# --------------------------------------------------------------------------
# path / URL helpers


def encode_remote(remote: str) -> str:
    """URL-encode each path segment (not '/') of a remote path relative to the root."""
    p = remote.lstrip("/")
    if not p:
        return ""
    return "/".join(urllib.parse.quote(seg, safe="") for seg in p.split("/"))


def file_url(cfg: Config, remote: str) -> str:
    return cfg.url + encode_remote(remote)


def ocspath(remote: str) -> str:
    """Remote path with a guaranteed leading slash for the OCS API."""
    return "/" + remote.lstrip("/")


def iso_mtime(rfc1123: str | None) -> str:
    if not rfc1123:
        return "-"
    try:
        return email.utils.parsedate_to_datetime(rfc1123).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return "-"


# --------------------------------------------------------------------------
# PROPFIND


def parse_multistatus(xml_bytes: bytes, request_url: str) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    req_path = urllib.parse.urlparse(request_url).path.rstrip("/")
    entries: list[dict] = []
    for resp in root.findall(f"{DAV_NS}response"):
        href = resp.findtext(f"{DAV_NS}href") or ""
        href_path = urllib.parse.urlparse(urllib.parse.unquote(href)).path.rstrip("/")
        propstat = resp.find(f"{DAV_NS}propstat")
        prop = propstat.find(f"{DAV_NS}prop") if propstat is not None else None
        if prop is None:
            continue
        is_dir = prop.find(f"{DAV_NS}resourcetype/{DAV_NS}collection") is not None
        size_text = prop.findtext(f"{DAV_NS}getcontentlength")
        size = int(size_text) if size_text else 0
        name = href_path.rsplit("/", 1)[-1] if href_path else ""
        entries.append({
            "name": name,
            "is_dir": is_dir,
            "size": size,
            "mtime": iso_mtime(prop.findtext(f"{DAV_NS}getlastmodified")),
            "etag": prop.findtext(f"{DAV_NS}getetag") or "-",
            "content_type": prop.findtext(f"{DAV_NS}getcontenttype") or "-",
            "is_self": href_path == req_path,
        })
    return entries


# --------------------------------------------------------------------------
# file commands


def cmd_ls(cfg: Config, args: argparse.Namespace) -> None:
    url = file_url(cfg, args.remote)
    body = request(cfg, "PROPFIND", url, headers={"Depth": "1"})
    for e in parse_multistatus(body, url):
        if e["is_self"]:
            continue
        kind = "D" if e["is_dir"] else "F"
        size = "-" if e["is_dir"] else str(e["size"])
        print(f"{kind} {e['name']} {size} {e['mtime']}")


def cmd_stat(cfg: Config, args: argparse.Namespace) -> None:
    url = file_url(cfg, args.remote)
    body = request(cfg, "PROPFIND", url, headers={"Depth": "0"})
    entries = parse_multistatus(body, url)
    if not entries:
        raise NextcloudError(f"not found: {args.remote}")
    e = entries[0]
    print(f"name: {e['name']}")
    print(f"type: {'directory' if e['is_dir'] else 'file'}")
    print(f"size: {e['size']}")
    print(f"mtime: {e['mtime']}")
    print(f"etag: {e['etag']}")
    print(f"content-type: {e['content_type']}")


def cmd_get(cfg: Config, args: argparse.Namespace) -> None:
    url = file_url(cfg, args.remote)
    data = request(cfg, "GET", url)
    if args.output:
        dest = Path(args.output)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"Downloaded {len(data)} bytes -> {args.output}")
    else:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def cmd_download(cfg: Config, args: argparse.Namespace) -> None:
    data = request(cfg, "GET", file_url(cfg, args.remote))
    dest = Path(args.local)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    print(f"Downloaded {len(data)} bytes -> {args.local}")


def cmd_cat(cfg: Config, args: argparse.Namespace) -> None:
    data = request(cfg, "GET", file_url(cfg, args.remote))
    sys.stdout.write(data.decode("utf-8"))


def cmd_put(cfg: Config, args: argparse.Namespace) -> None:
    if args.local == "-":
        data = sys.stdin.buffer.read()
    else:
        data = Path(args.local).read_bytes()
    request(cfg, "PUT", file_url(cfg, args.remote), body=data)
    print(f"Uploaded {len(data)} bytes -> {args.remote}")


def _source_bytes(args: argparse.Namespace) -> bytes:
    if args.text is not None:
        return args.text.encode("utf-8")
    if args.stdin:
        return sys.stdin.buffer.read()
    return Path(args.file).read_bytes()


def cmd_set(cfg: Config, args: argparse.Namespace) -> None:
    data = _source_bytes(args)
    request(cfg, "PUT", file_url(cfg, args.remote), body=data)
    print(f"Uploaded {len(data)} bytes -> {args.remote}")


def cmd_append(cfg: Config, args: argparse.Namespace) -> None:
    url = file_url(cfg, args.remote)
    current = request(cfg, "GET", url)
    extra = _source_bytes(args)
    new = current + extra
    request(cfg, "PUT", url, body=new)
    print(f"Appended {len(extra)} bytes -> {args.remote} (now {len(new)} bytes)")


def cmd_rm(cfg: Config, args: argparse.Namespace) -> None:
    request(cfg, "DELETE", file_url(cfg, args.remote))
    print(f"Deleted {args.remote}")


def cmd_mv(cfg: Config, args: argparse.Namespace) -> None:
    request(cfg, "MOVE", file_url(cfg, args.src),
            headers={"Destination": file_url(cfg, args.dst)})
    print(f"Moved {args.src} -> {args.dst}")


def cmd_mkdir(cfg: Config, args: argparse.Namespace) -> None:
    request(cfg, "MKCOL", file_url(cfg, args.remote))
    print(f"Created directory {args.remote}")


def cmd_info(cfg: Config, args: argparse.Namespace) -> None:
    status = None
    last_err: Exception | None = None
    for attempt in range(RETRIES + 1):
        if attempt:
            time.sleep(RETRY_BACKOFF[attempt - 1])
        try:
            req = urllib.request.Request(cfg.origin + "/status.php")
            req.add_header("User-Agent", USER_AGENT)
            req.add_header("Accept", "application/json, text/plain, */*")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                status = json.loads(resp.read().decode("utf-8"))
            break
        except (urllib.error.HTTPError, OSError, ValueError) as e:
            last_err = e
    if status is None:
        raise NextcloudError(f"status.php failed: {last_err}") from None
    product = status.get("productname") or "Nextcloud"
    version = status.get("versionstring") or status.get("version") or "?"
    print(f"server: {product} {version}")
    request(cfg, "PROPFIND", cfg.url, headers={"Depth": "0"})
    print(f"OK user={cfg.user}")


# --------------------------------------------------------------------------
# share commands


def cmd_share_create(cfg: Config, args: argparse.Namespace) -> None:
    params: dict = {"path": ocspath(args.remote), "shareType": 3, "permissions": PERMS[args.perm]}
    if args.password:
        params["password"] = args.password
    if args.expire:
        params["expireDate"] = args.expire
    if args.note:
        params["note"] = args.note
    if args.name:
        params["name"] = args.name
    data = ocs(cfg, "POST", ocs_base(cfg), params)
    if not isinstance(data, dict):
        raise NextcloudError("unexpected OCS response for share-create")
    token = data.get("token") or ""
    url = data.get("url") or f"{cfg.origin}/s/{token}"
    print(f"url: {url}")
    print(f"id: {data.get('id')}")
    print(f"token: {token}")
    print(f"password-protected: {'yes' if data.get('password') else 'no'}")
    if not args.password:
        print("Warning: public link has no password — anyone with the URL can access it.", file=sys.stderr)


def cmd_share_list(cfg: Config, args: argparse.Namespace) -> None:
    data = ocs(cfg, "GET", ocs_base(cfg))
    if not isinstance(data, list):
        raise NextcloudError("unexpected OCS response for share-list")
    rows = [s for s in data if s.get("share_type") == 3]
    if args.remote:
        target = ocspath(args.remote)
        rows = [s for s in rows if (s.get("path") or "").rstrip("/") == target.rstrip("/")]
    print("ID  TOKEN  URL  PATH  PERM  PASSWORD  EXPIRATION  NOTE")
    for s in rows:
        token = s.get("token") or "-"
        url = s.get("url") or "-"
        path = s.get("path") or "-"
        perm = s.get("permissions")
        pw = "yes" if s.get("password") else "no"
        exp = (s.get("expiration") or "-")
        note = (s.get("note") or "-")
        print(f"{s.get('id')}  {token}  {url}  {path}  {perm}  {pw}  {exp}  {note}")


def cmd_share_delete(cfg: Config, args: argparse.Namespace) -> None:
    ocs(cfg, "DELETE", f"{ocs_base(cfg)}/{args.id}")
    print(f"Deleted share {args.id}")


def cmd_share_update(cfg: Config, args: argparse.Namespace) -> None:
    params: dict = {}
    changes: list[str] = []
    if args.password:
        params["password"] = args.password
        changes.append("password set")
    if args.clear_password:
        params["password"] = ""
        changes.append("password cleared")
    if args.expire:
        params["expireDate"] = args.expire
        changes.append(f"expiry set to {args.expire}")
    if args.clear_expire:
        params["expireDate"] = ""
        changes.append("expiry cleared")
    if args.note is not None:
        params["note"] = args.note
        changes.append("note updated")
    if args.perm:
        params["permissions"] = PERMS[args.perm]
        changes.append(f"permissions -> {args.perm}")
    if not changes:
        raise NextcloudError("no changes requested (use --password/--expire/--note/--perm or a --clear-* flag)")
    ocs(cfg, "PUT", f"{ocs_base(cfg)}/{args.id}", params)
    print(f"Updated share {args.id}: {', '.join(changes)}")


# --------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="nextcloud.py",
        description="Nextcloud file + share-link operations (WebDAV + OCS).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ls", help="List a remote directory.")
    p.add_argument("remote", nargs="?", default="/", help="Remote path (default /).")
    p.set_defaults(fn=cmd_ls)

    p = sub.add_parser("get", help="Fetch a file to stdout or a local file.")
    p.add_argument("remote")
    p.add_argument("-o", "--output", metavar="FILE", help="Write to FILE instead of stdout.")
    p.set_defaults(fn=cmd_get)

    p = sub.add_parser("download", help="Fetch a file to a local path (creates parent dirs).")
    p.add_argument("remote")
    p.add_argument("local")
    p.set_defaults(fn=cmd_download)

    p = sub.add_parser("cat", help="Print a remote text file to stdout.")
    p.add_argument("remote")
    p.set_defaults(fn=cmd_cat)

    p = sub.add_parser("put", help="Upload a local file (overwrite).")
    p.add_argument("local", help="Local path, or '-' to read stdin.")
    p.add_argument("remote")
    p.set_defaults(fn=cmd_put)

    p = sub.add_parser("set", help="Replace remote content entirely.")
    p.add_argument("remote")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", metavar="LOCAL", help="Source local file.")
    src.add_argument("--stdin", action="store_true", help="Read from stdin.")
    src.add_argument("--text", metavar="STR", help="Literal string content.")
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("append", help="Append content to a remote text file.")
    p.add_argument("remote")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", metavar="STR", help="Append a literal string.")
    src.add_argument("--stdin", action="store_true", help="Append stdin bytes.")
    src.add_argument("--file", metavar="LOCAL", help="Append a local file's bytes.")
    p.set_defaults(fn=cmd_append)

    p = sub.add_parser("rm", help="Delete a remote file or directory (recursive).")
    p.add_argument("remote")
    p.set_defaults(fn=cmd_rm)

    p = sub.add_parser("mv", help="Move/rename a remote path.")
    p.add_argument("src")
    p.add_argument("dst")
    p.set_defaults(fn=cmd_mv)

    p = sub.add_parser("mkdir", help="Create a remote directory.")
    p.add_argument("remote")
    p.set_defaults(fn=cmd_mkdir)

    p = sub.add_parser("stat", help="Show metadata for a remote path.")
    p.add_argument("remote")
    p.set_defaults(fn=cmd_stat)

    p = sub.add_parser("info", help="Show server version and confirm auth.")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("share-create", help="Create a public link share (shareType 3).")
    p.add_argument("remote")
    p.add_argument("--password", metavar="P", help="Protect the link with a password.")
    p.add_argument("--expire", metavar="YYYY-MM-DD", help="Expiry date.")
    p.add_argument("--note", metavar="N", help="Note on the share.")
    p.add_argument("--name", metavar="NAME", help="Display name for the link.")
    p.add_argument("--perm", choices=sorted(PERMS), default="read",
                   help="Permissions: read (1, default), upload (15), download (19).")
    p.set_defaults(fn=cmd_share_create)

    p = sub.add_parser("share-list", help="List public link shares.")
    p.add_argument("remote", nargs="?", help="Filter by remote path.")
    p.set_defaults(fn=cmd_share_list)

    p = sub.add_parser("share-delete", help="Delete a share by id.")
    p.add_argument("id")
    p.set_defaults(fn=cmd_share_delete)

    p = sub.add_parser("share-update", help="Update a share's fields.")
    p.add_argument("id")
    p.add_argument("--password", metavar="P", help="Set/change the password.")
    p.add_argument("--clear-password", action="store_true", help="Remove the password.")
    p.add_argument("--expire", metavar="YYYY-MM-DD", help="Set the expiry date.")
    p.add_argument("--clear-expire", action="store_true", help="Remove the expiry.")
    p.add_argument("--note", metavar="N", help="Set the note.")
    p.add_argument("--perm", choices=sorted(PERMS), help="Change permissions.")
    p.set_defaults(fn=cmd_share_update)

    return ap


def main() -> None:
    args = build_parser().parse_args()
    try:
        cfg = load_config()
        args.fn(cfg, args)
    except NextcloudError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
