#!/usr/bin/env python3
"""Export Pi's P8 providers and install/refresh them without executing catalog data.

Standard library only. Distribution copies of this file never need project files.
"""
import argparse
import copy
import datetime as dt
import hashlib
import http.client
import socket
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

PI_VERSION = "1.3.0"
PI_PACKAGE = "@earendil-works/pi-coding-agent"
PUBLIC_BASE = "https://ollama.ecitis.org"
PRIVATE_BASE = None  # private router base is host-specific; set only for a private deployment
ROOT = Path("/home/res/projects/ollama-router")
PROVIDERS = {
    "p8-agent": ("/agent/v1", "Reliable agent; router task gate"),
    "p8-agent-fast": ("/agent-fast/v1", "Fast agent; router task gate"),
    "p8-chat": ("/chat/v1", "Verified chat; not an agent qualification"),
    "p8-explore": ("/explore/v1", "Unqualified catalog; may include non-chat models"),
}
FILES = ("models.json", "settings.json", "manifest.json", "model-metadata.json",
         "install.py", "install.sh", "install.ps1", "refresh-models.py",
         "package.json", "bun.lock", "README.md", "key.env", "p8-launch.py")
VALID_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*(?:/[A-Za-z0-9][A-Za-z0-9_.:-]*)*\Z")
CASES = frozenset(("repair", "cli", "analysis", "concurrency"))
LIMIT = 64 * 1024 * 1024
BASE_COMPAT = {"supportsStore": False, "supportsDeveloperRole": False,
               "supportsReasoningEffort": False, "supportsUsageInStreaming": True,
               "supportsFinishReason": True, "maxTokensField": "max_tokens",
               "supportsStrictMode": False, "thinkingFormat": "openai"}
SETTINGS = {"$schema": "https://pi.dev/schemas/settings.schema.json",
            "defaultProvider": "p8-agent", "defaultModel": "router/agent",
            "cacheWarming": "off",
            "compaction": {"reserveTokens": 8192, "keepRecentTokens": 8192},
            "enableInstallTelemetry": False, "enableAnalytics": False}


def valid_model_id(value):
    return (isinstance(value, str) and len(value) <= 256
            and bool(VALID_ID.fullmatch(value))
            and all(part not in (".", "..") for part in value.split("/")))


def literal_key(value):
    # Pi treats a leading ! as a shell command and a leading $ as an env reference.
    if (not isinstance(value, str) or not value or len(value) > 4096
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", value)
            or re.fullmatch(r"[A-Z_][A-Z0-9_]*", value)):
        raise ValueError("Expected an inline recipient key, not a command or environment reference")
    return value


def trusted_base(base):
    base = base.rstrip("/")
    if base not in (PUBLIC_BASE, PRIVATE_BASE):
        raise ValueError("Only the P8 public and private router origins are supported")
    return base


def bounded_connection(address, timeout=45, source_address=None, **kwargs):
    # Try each address briefly instead of spending the entire read budget on one
    # unreachable DNS answer. Preserve the hostname for normal TLS verification.
    last_error = None
    for family, kind, proto, _, destination in socket.getaddrinfo(*address, type=socket.SOCK_STREAM):
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(min(timeout, 3))
            if source_address:
                sock.bind(source_address)
            sock.connect(destination)
            sock.settimeout(timeout)
            return sock
        except OSError as error:
            last_error = error
            sock.close()
    if last_error:
        raise last_error
    raise OSError("No addresses for router")


class RouterHTTPS(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = bounded_connection


class RouterHTTP(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = bounded_connection


class HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(RouterHTTPS, req, context=self._context)


class HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(RouterHTTP, req)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Catalog redirects are not accepted")


def fetch(base, path):
    base = trusted_base(base)
    if not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("Invalid catalog path")
    request = urllib.request.Request(base + path, headers={"User-Agent": "opencode"})
    # Discovery is public. Recipient authentication never leaves its local config.
    with urllib.request.build_opener(NoRedirect(), HTTPSHandler(), HTTPHandler()).open(request, timeout=45) as response:
        raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Catalog exceeds the size limit")
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        raise ValueError("Catalog must contain a data array")
    return value


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def safe_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Refusing to replace a symlink or write through a symlink directory")
    if path.exists() and not path.is_file():
        raise ValueError("Expected a regular file")
    return path


def private_write(path, value):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = value if isinstance(value, bytes) else json_bytes(value)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def jsonc(text):
    """Accept Pi's comments and trailing commas without touching string contents."""
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n\r]*|/\*[\s\S]*?\*/')
    clean = tokens.sub(lambda m: m[0] if m[0].startswith('"') else ' ', text.lstrip('\ufeff'))
    clean = re.sub(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])',
                   lambda m: m[0] if m[0].startswith('"') else m[1], clean)
    value = json.loads(clean)
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a JSON object")
    return value


def read_config(path, missing=False):
    path = safe_path(path)
    if missing and not path.exists():
        return {}
    if path.stat().st_size > LIMIT:
        raise ValueError("Configuration exceeds the size limit")
    return jsonc(path.read_text(encoding="utf-8"))


def backup(path):
    path = safe_path(path)
    if not path.exists():
        return None
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(path.name + ".bak." + stamp)
    private_write(target, path.read_bytes())
    return target


def metadata_snapshot(root):
    grouped = {}
    for name in ("AGENT-ORIGIN-METADATA.json", "BROAD-MODEL-METADATA.json", "BROAD-FRONTIER-METADATA.json"):
        for row in json.loads((root / "docs" / name).read_text()):
            if isinstance(row, dict) and valid_model_id(row.get("model")):
                grouped.setdefault(row["model"], []).append(row)
    result = {}
    for model, rows in sorted(grouped.items()):
        caps = [set(r["capabilities"]) for r in rows if isinstance(r.get("capabilities"), list)]
        thinking = [r["thinking"]["values"] for r in rows
                    if isinstance(r.get("thinking"), dict) and isinstance(r["thinking"].get("values"), list)]
        ctx = [v for r in rows for k, v in (r.get("model_info") or {}).items()
               if k.endswith(".context_length") and type(v) is int and v > 0]
        entry = {"source": "exact-model saved upstream metadata; not weight attestation"}
        if caps:
            # Mixed origins must share a capability before a client advertises it.
            common = set.intersection(*caps)
            entry["reasoning"] = "thinking" in common
            entry["vision_advertised"] = "vision" in common
        if thinking:
            common = set.intersection(*(set(v) for v in thinking))
            entry["thinking_values"] = sorted((v for v in common if isinstance(v, str)))
            entry["thinking_off"] = False in common
            if all(set(v) <= {False} for v in thinking):
                entry["reasoning"] = False
            elif any(isinstance(v, str) or v is True for v in common):
                entry.setdefault("reasoning", True)
        if ctx:
            entry["advertised_context"] = min(ctx)
        result[model] = entry
    return {"models": result, "pi_full_task_qualified": pi_qualification(root)}


def pi_qualification(root):
    """Only complete harness 3 reports from the currently pinned Pi runtime count."""
    qualified = {}
    lock = root / "tools/pi-agent/bun.lock"
    if not lock.is_file():
        return qualified
    fingerprint = hashlib.sha256(lock.read_bytes()).hexdigest()
    identity = {"client": "pi", "client_version": PI_VERSION, "profile": "pi-openai-v1",
                "harness_version": "3", "runtime_lock_sha256": fingerprint}
    paths = sorted(set((root / "state/pi-qualification").rglob("qualification.json"))
                   | set((root / "state/pi-audit/runs").rglob("qualification.json")))
    for path in paths:
        try:
            report = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(report, dict) or report.get("passed") is not True:
            continue
        if any(report.get(k) != v for k, v in identity.items()):
            continue
        rows = report.get("cases")
        if not isinstance(rows, list) or len(rows) != 4 or any(not isinstance(r, dict) for r in rows):
            continue
        if {r.get("case") for r in rows} != CASES or not valid_model_id(report.get("model")) or not report.get("host"):
            continue
        if not all(all(r.get(k) == v for k, v in identity.items())
                   and r.get("model") == report["model"] and r.get("host") == report["host"]
                   and r.get("passed") is True and r.get("exit_code") == 0
                   and r.get("verification_exit") == 0 and r.get("verification_completed") is True
                   and r.get("inputs_unchanged") is True and r.get("agent_completed") is True
                   and r.get("settled") is True and not r.get("timed_out")
                   and r.get("parse_errors") == 0 and not r.get("harness_error")
                   and len(r.get("tool_calls", [])) >= 2
                   and r.get("finish_reasons", [])[-1:] == ["stop"]
                   and (not r.get("errors") or r.get("recovered_error") is True) for r in rows):
            continue
        qualified[report["model"]] = {"cases": sorted(CASES), **identity,
            "thinking": rows[0].get("thinking") if len({r.get("thinking") for r in rows}) == 1 else None,
            "evidence": str(path.relative_to(root)),
            "note": "Saved harness 3 Pi result for this lockfile. Current router eligibility is separate."}
    return qualified



def alias_name(model):
    return {"router/agent": "P8 automatic reliability (model changes)",
            "router/agent-fast": "P8 automatic responsiveness (model changes)"}.get(model, model)

def model_config(model, provider, metadata, caps):
    known = metadata.get("models", {}).get(model, {})
    profile = metadata.get("pi_full_task_qualified", {}).get(model, {})
    reasoning = known.get("reasoning") is True
    values = known.get("thinking_values", [])
    effort = reasoning and (bool(values) or profile.get("thinking") in ("low", "medium", "high"))
    compat = {"supportsReasoningEffort": effort,
              "requiresReasoningContentOnAssistantMessages": reasoning}
    if provider == "p8-explore":
        label = "unqualified catalog"
        if re.search(r"embed|all-minilm|(?:^|/)bge-|rerank", model, re.I):
            label += "; possible embedding/reranker, not a Pi chat claim"
    elif provider == "p8-chat":
        label = "chat verified; agent unqualified" if not caps.get("agent_verified") else "chat; router agent gate"
    else:
        label = "router agent gate; Pi task evidence in manifest"
    row = {"id": model, "name": alias_name(model) + " [" + label + "]", # Text-only is a client restriction, not an audited vision declaration.
           "input": ["text"], "compat": compat}
    if isinstance(known.get("reasoning"), bool):
        row["reasoning"] = known["reasoning"]
    for field, key in (("contextWindow", "advertised_context"), ("maxTokens", "advertised_output")):
        if type(known.get(key)) is int and known[key] > 0:
            row[field] = known[key]
    if effort and values:
        row["thinkingLevelMap"] = {level: (level if level in values else None)
                                   for level in ("minimal", "low", "medium", "high", "xhigh", "max")}
        if known.get("thinking_off") is not True:
            row["thinkingLevelMap"]["off"] = None
    return row


def build(key, base=PUBLIC_BASE, metadata=None, fetcher=fetch, include_explore=False):
    literal_key(key)
    base = trusted_base(base)
    metadata = metadata or {"models": {}, "pi_full_task_qualified": {}}
    catalogs = {}
    raw_counts = {}
    excluded = {}
    selected = {p: v for p, v in PROVIDERS.items() if p in ("p8-agent", "p8-agent-fast") or include_explore}
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = {provider: pool.submit(fetcher, base,
                ("/agent-reliable/v1" if base == PRIVATE_BASE and provider == "p8-agent" else path) + "/models")
                for provider, (path, _) in selected.items()}
        cap_job = pool.submit(fetcher, base, "/v1/capabilities")
        documents = {provider: job.result()["data"] for provider, job in jobs.items()}
        capability_rows = cap_job.result()["data"]
    for provider, rows in documents.items():
        ids = [r.get("id") for r in rows if isinstance(r, dict)]
        valid = sorted({m for m in ids if valid_model_id(m)})
        if provider == "p8-explore":
            valid = [m for m in valid if not re.search(r"embed|all-minilm|(?:^|/)bge-|rerank", m, re.I)]
        if provider in ("p8-agent", "p8-agent-fast"):
            aliases = sorted((m for m in valid if m.startswith("router/")),
                             key=lambda m: (m not in ("router/agent", "router/agent-fast"), m))[:32]
            exact = [m for m in valid if m not in aliases]
            exact.sort(key=lambda m: m not in metadata.get("models", {}))
            valid = aliases + exact[:max(0, 32 - len(aliases))]
        catalogs[provider] = valid
        raw_counts[provider] = len(rows)
        excluded[provider] = len(rows) - len([m for m in ids if valid_model_id(m)])
    caps = {r["id"]: r for r in capability_rows
            if isinstance(r, dict) and valid_model_id(r.get("id"))}
    # Fail closed if a server response would put an unqualified ID in an agent provider.
    for provider in ("p8-agent", "p8-agent-fast"):
        for model in catalogs[provider]:
            if not model.startswith("router/") and caps.get(model, {}).get("agent_verified") is not True:
                raise ValueError("Agent catalog disagrees with its capability gate")
    if "router/agent" not in catalogs["p8-agent"]:
        raise ValueError("The reliable automatic agent is currently unavailable")
    providers = {}
    for provider, (path, purpose) in selected.items():
        if base == PRIVATE_BASE and provider == "p8-agent":
            path = "/agent-reliable/v1"
        providers[provider] = {"name": "P8 " + purpose, "api": "openai-completions",
            "baseUrl": base + path, "apiKey": "$P8_ROUTER_API_KEY", "authHeader": True,
            "headers": {"User-Agent": "opencode"}, "compat": dict(BASE_COMPAT),
            "models": [model_config(m, provider, metadata, caps.get(m, {})) for m in catalogs[provider]]}
    counts = {p: len(ids) for p, ids in catalogs.items()}
    manifest = {"schema_version": 1, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pi_package": PI_PACKAGE, "pi_version": PI_VERSION, "base_url": base,
        "default_provider": "p8-agent", "default_model": "router/agent",
        "provider_model_counts": counts, "raw_provider_records": raw_counts,
        "malformed_records_excluded": excluded,
        "explore_qualification": "Advertised only, includes unqualified and non-chat hints. No agent claim.",
        "router_agent_qualification": "Existing router repair, cli, analysis, concurrency gate; not Pi coverage",
        "capabilities_chat_verified_ids": sum(r.get("chat_verified") is True for r in caps.values()),
        "capabilities_tools_verified_ids": sum(r.get("tools_verified") is True for r in caps.values()),
        "capabilities_agent_verified_ids": sum(r.get("agent_verified") is True for r in caps.values()),
        "pi_full_task_qualified": metadata.get("pi_full_task_qualified", {}),
        "metadata_exact_ids": len(metadata.get("models", {})),
        "reasoning_ids": {p: sum(m.get("reasoning") is True for m in v["models"]) for p, v in providers.items()},
        "limits_note": "Only exact saved reported token limits are emitted. Unknown maxima omitted. Pi 1.3.0 falls back to 128000 context and 16384 output as client assumptions, not measured upstream maxima.",
        "vision_note": "Text-only Pi input; upstream vision advertisements are not an audited router guarantee.",
        "cost_note": "Client estimates are zero; this is not a claim about upstream cost, capacity or availability.",
        "credentials": "Existing recipient key only in private key.env; models.json uses $P8_ROUTER_API_KEY.",
        "files": list(FILES)}
    return {"$schema": "https://pi.dev/schemas/models.schema.json", "providers": providers}, manifest


def providers_object(config):
    providers = config.get("providers", {})
    if not isinstance(providers, dict):
        raise ValueError("providers must be a JSON object")
    return providers


def merge_models(current, incoming):
    result = copy.deepcopy(current)
    old = providers_object(result)
    new = providers_object(incoming)
    result["providers"] = old
    for name in PROVIDERS:
        if name not in new:
            old.pop(name, None)
            continue
        if not isinstance(new[name], dict):
            raise ValueError("Missing P8 provider")
        merged = copy.deepcopy(new[name])
        if name in old:
            if not isinstance(old[name], dict):
                raise ValueError("Existing P8 provider must be an object")
            # Refresh owns model lists; other authored provider settings remain.
            for field, value in old[name].items():
                if field != "models":
                    merged[field] = copy.deepcopy(value)
            if isinstance(old[name].get("compat"), dict):
                merged["compat"] = merge_missing(old[name]["compat"], new[name].get("compat", {}))
            # Do not replace a recipient's login or custom authentication hooks.
            if any(field in old[name] for field in ("apiKey", "oauth")):
                for field in ("apiKey", "oauth", "authHeader"):
                    merged.pop(field, None)
                    if field in old[name]:
                        merged[field] = old[name][field]
            if "headers" in old[name]:
                if not isinstance(old[name]["headers"], dict):
                    raise ValueError("Existing headers must be an object")
                merged["headers"].update(old[name]["headers"])
        old[name] = merged
    return result


def merge_missing(current, defaults):
    result = copy.deepcopy(current)
    for key, value in defaults.items():
        if key not in result:
            result[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(result[key], dict):
            result[key] = merge_missing(result[key], value)
    return result


def config_dir():
    return Path(os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi/agent"))).expanduser()


def verify_bundle(bundle):
    manifest = read_config(bundle / "manifest.json")
    if manifest.get("pi_package") != PI_PACKAGE or manifest.get("pi_version") != PI_VERSION or manifest.get("files") != list(FILES):
        raise ValueError("Unexpected bundle manifest")
    expected = set(FILES) - {"manifest.json"}
    hashes = manifest.get("sha256", {})
    # Distribution copies never ship the recipient key. key.env is therefore
    # required (and checksum-verified) only when it is present in the bundle.
    required = expected if (bundle / "key.env").is_file() else expected - {"key.env"}
    if set(hashes) != required:
        raise ValueError("Incomplete bundle checksums")
    for name in sorted(required):
        path = safe_path(bundle / name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[name]:
            raise ValueError("Bundle checksum mismatch")
    return manifest


def install_config(bundle, target, make_default=False, offline=False, include_explore=False):
    verify_bundle(bundle)
    target = Path(target)
    model_path, settings_path = target / "models.json", target / "settings.json"
    current_models = read_config(model_path, missing=True)
    current_settings = read_config(settings_path, missing=True)
    if offline:
        incoming = read_config(bundle / "models.json")
        discovery = {"mode": "offline bootstrap snapshot"}
    else:
        manifest = read_config(bundle / "manifest.json")
        incoming, discovery = build("unused-install-placeholder", base=manifest["base_url"],
                                    metadata=read_config(bundle / "model-metadata.json"),
                                    include_explore=include_explore)
    # Distribution copies never ship the recipient key. Use the bundle's
    # key.env only when one is actually present (private full bundle); a public
    # distribution bundle relies on the config directory's existing key.env
    # (created from key.env.example by the operator or scripts/bootstrap.sh).
    existing_key = target / "key.env"
    if (bundle / "key.env").is_file():
        key_env = (bundle / "key.env").read_bytes()
    elif existing_key.exists():
        key_env = safe_path(existing_key).read_bytes()
    else:
        raise ValueError("Create key.env from key.env.example in the Pi config directory first")
    recipient = literal_key(key_env.decode().strip().removeprefix("P8_ROUTER_API_KEY="))
    # Existing $ENV authentication must keep its established private file value.
    if existing_key.exists() and any(isinstance(row, dict) and row.get("apiKey") == "$P8_ROUTER_API_KEY"
                                     for name, row in providers_object(current_models).items() if name in PROVIDERS):
        key_env = safe_path(existing_key).read_bytes()
        literal_key(key_env.decode().strip().removeprefix("P8_ROUTER_API_KEY="))
    effective_recipient = literal_key(key_env.decode().strip().removeprefix("P8_ROUTER_API_KEY="))
    # Migrate the prior bundle recipient out of model snapshots, preserving other logins.
    for name, row in providers_object(current_models).items():
        if name in PROVIDERS and isinstance(row, dict) and row.get("apiKey") == effective_recipient:
            row["apiKey"] = "$P8_ROUTER_API_KEY"
    models = merge_models(current_models, incoming)
    settings = merge_missing(current_settings, read_config(bundle / "settings.json"))
    if make_default:
        settings.update(defaultProvider="p8-agent", defaultModel="router/agent")
        # An existing scope can otherwise silently override the new default.
        enabled = settings.get("enabledModels")
        if isinstance(enabled, list) and "p8-agent/router/agent" not in enabled:
            settings["enabledModels"] = [*enabled, "p8-agent/router/agent"]
    backups = [str(b) for p in (model_path, settings_path, target / "key.env") if (b := backup(p))]
    private_write(target / "key.env", key_env)
    private_write(target / "p8-launch.py", (bundle / "p8-launch.py").read_bytes())
    private_write(model_path, models)
    private_write(settings_path, settings)
    return {"config_dir": str(target), "backups": backups, "existing_auth_preserved": True,
            "default_provider": settings.get("defaultProvider"), "default_model": settings.get("defaultModel"),
            "discovery": {"mode": "offline" if offline else "live",
                          "provider_model_counts": discovery.get("provider_model_counts", {})}}


def install_cli(bundle, destination, bin_dir, target=None):
    verify_bundle(bundle)
    bun = shutil.which("bun")
    if bun is None:
        candidate = Path.home() / ".bun/bin" / ("bun.exe" if os.name == "nt" else "bun")
        if candidate.is_file():
            bun = str(candidate)
    if bun is None:
        raise ValueError("Install Bun from bun.sh first, or use --config-only with an existing Pi 1.3.0")
    destination, bin_dir = Path(destination).absolute(), Path(bin_dir).absolute()
    for name in ("package.json", "bun.lock"):
        path = destination / name
        safe_path(path)
        backup(path)
        private_write(path, (bundle / name).read_bytes())
    # No catalog strings, credentials or shell command are passed to Bun.
    subprocess.run([bun, "install", "--frozen-lockfile", "--ignore-scripts"], cwd=destination, check=True, timeout=300)
    package = destination / "node_modules" / PI_PACKAGE
    version = json.loads((package / "package.json").read_text())["version"]
    if version != PI_VERSION:
        raise ValueError("Installed Pi version did not match its pin")
    cli = str(package / "dist/cli.js")
    check = subprocess.run([bun, cli, "--version"], capture_output=True, text=True, check=True, timeout=30)
    if check.stdout.strip() != PI_VERSION:
        raise ValueError("Installed Pi CLI did not report its pinned version")
    target = Path(target or config_dir()).absolute()
    python = sys.executable
    launcher_script = str(target / "p8-launch.py")
    if os.name == "nt":
        # PowerShell literal strings do not interpolate $, backticks, or catalog IDs.
        quote = lambda s: "'" + s.replace("'", "''") + "'"
        launcher = "& " + " ".join(map(quote, (python, launcher_script, bun, cli))) + " -- @args\nexit $LASTEXITCODE\n"
        path = bin_dir / "pi.ps1"
    else:
        launcher = "#!/bin/sh\nexec " + " ".join(map(shlex.quote, (python, launcher_script, bun, cli))) + ' -- "$@"\n'
        path = bin_dir / "pi"
    safe_path(path)
    backup(path)
    private_write(path, launcher.encode())
    if os.name != "nt":
        path.chmod(0o700)
    return {"pi_version": version, "launcher": str(path), "bin_directory": str(bin_dir),
            "path_note": "Put this user bin directory before system paths. No system executable was replaced."}


def refresh(config, metadata_path, include_explore=False):
    current = read_config(config)
    providers = providers_object(current)
    existing = providers.get("p8-agent")
    if not isinstance(existing, dict):
        raise ValueError("Install the P8 providers before refreshing")
    # Discovery is unauthenticated, so an existing env/command auth reference is preserved, never evaluated.
    base_url = existing.get("baseUrl", "")
    base = next((b for b in (PUBLIC_BASE,)
                 if base_url in (b + "/agent/v1", b + "/agent-reliable/v1")), None)
    if base is None:
        raise ValueError("Unexpected P8 agent base URL; refusing remote discovery")
    metadata = read_config(metadata_path)
    fresh, summary = build("unused-refresh-placeholder", base, metadata, include_explore=include_explore)
    merged = merge_models(current, fresh)
    for name in fresh["providers"]:
        if name not in providers:
            if "oauth" in existing and "apiKey" not in existing:
                merged["providers"][name].pop("apiKey", None)
            # Keep newly added P8 providers on the same existing authentication.
            for field in ("apiKey", "oauth", "authHeader", "headers"):
                if field in existing:
                    merged["providers"][name][field] = copy.deepcopy(existing[field])
    previous = backup(config)
    private_write(config, merged)
    return {"provider_model_counts": summary["provider_model_counts"],
            "malformed_records_excluded": summary["malformed_records_excluded"],
            "backup": str(previous), "auth_preserved": True}


def benchmark_coverage(root):
    path = root / "state/index-benchmarks/benchmarks.sqlite"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT model,measured,payload FROM samples").fetchall()
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    valid = []
    for model, measured, payload in rows:
        row = json.loads(payload)
        if 0 <= now - measured <= 86400 and row.get("generation_passed") is True and row.get("tool_passed") is True:
            valid.append(model)
    return {"freshness_seconds": 86400, "generation_and_tool_passed_pairs": len(valid),
            "distinct_model_ids": len(set(valid)),
            "distinct_base_names": len({m.split("/")[-1].split(":")[0] for m in valid}),
            "base_name_definition": "Last path segment before the tag; aliases may share weights",
            "note": "Probe evidence, not four-task Pi coverage or a quality leaderboard"}



def recipient_key(source):
    """Read the explicitly selected recipient source, never the production registry."""
    source = Path(source)
    config = read_config(source)
    provider = config.get("providers", config.get("provider", {})).get("p8-agent", {})
    value = provider.get("settings", provider.get("options", {})).get("apiKey")
    if isinstance(value, str) and value.startswith("{file:") and value.endswith("}"):
        key_path = Path(value[6:-1]).expanduser()
        if not key_path.is_absolute():
            key_path = source.parent / key_path
        value = safe_path(key_path).read_text().strip()
    return literal_key(value)


PI_LAUNCH = r'''#!/usr/bin/env python3
"""Load only the P8 key into the child process. The env file is never shell code."""
import os
from pathlib import Path
import re
import sys

def main():
    split = sys.argv.index("--")
    command = sys.argv[1:split]
    if not command:
        raise ValueError("Supply the existing Pi executable before --")
    directory = Path(__file__).resolve().parent
    text = (directory / "key.env").read_text().strip()
    if not re.fullmatch(r"P8_ROUTER_API_KEY=[A-Za-z0-9_.-]+", text):
        raise ValueError("Invalid P8 key file")
    env = dict(os.environ)
    env["P8_ROUTER_API_KEY"] = text.split("=", 1)[1]
    env["PI_CODING_AGENT_DIR"] = str(directory)
    os.execvpe(command[0], command + sys.argv[split + 1:], env)

if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError):
        print("P8 launcher failed; check the private key file and Pi executable.", file=sys.stderr)
        sys.exit(1)
'''

INSTALL_SH = '''#!/bin/sh
set -eu
bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v uv >/dev/null 2>&1; then
  exec uv run --no-project python "$bundle_dir/install.py" install "$@"
elif command -v python3 >/dev/null 2>&1; then
  exec python3 "$bundle_dir/install.py" install "$@"
fi
printf '%s\\n' 'Install uv or Python 3.10+ first.' >&2
exit 1
'''
INSTALL_PS1 = '''$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'install.py'
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run --no-project python $script install @args
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $script install @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $script install @args
} else {
    throw 'Install uv or Python 3.10+ first.'
}
exit $LASTEXITCODE
'''


def bundle_export(root, output, source_config, base, include_explore=False):
    if output.exists() and output.is_symlink():
        raise ValueError("Bundle destination must not be a symlink")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    # Read only this target provider's recipient key. Never load .env or the key registry.
    recipient = recipient_key(source_config)
    metadata = metadata_snapshot(root)
    models, manifest = build(recipient, base, metadata, include_explore=include_explore)
    private_write(output / "key.env", ("P8_ROUTER_API_KEY=" + recipient + "\n").encode())
    private_write(output / "p8-launch.py", PI_LAUNCH.encode())
    manifest["benchmark_coverage"] = benchmark_coverage(root)
    private_write(output / "models.json", models)
    private_write(output / "settings.json", SETTINGS)
    private_write(output / "model-metadata.json", metadata)
    for name in ("install.py", "refresh-models.py"):
        private_write(output / name, Path(__file__).read_bytes())
    private_write(output / "install.sh", INSTALL_SH.encode())
    private_write(output / "install.ps1", INSTALL_PS1.encode())
    for name in ("package.json", "bun.lock"):
        private_write(output / name, (root / "tools/pi-agent" / name).read_bytes())
    private_write(output / "README.md", (root / "docs/PI-FIRST-SETUP.md").read_bytes())
    manifest["sha256"] = {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                          for name in FILES if name != "manifest.json"}
    private_write(output / "manifest.json", manifest)
    verify_bundle(output)
    archive = output.parent / "P8-Pi-20261021.zip"
    safe_path(archive)
    fd, temporary = tempfile.mkstemp(prefix=".pi-bundle-", dir=output)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as package:
            for name in FILES:
                path = safe_path(output / name)
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                package.writestr(info, path.read_bytes())
        os.chmod(temporary, 0o600)
        os.replace(temporary, archive)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    private_write(archive.with_suffix(".zip.sha256"), (digest + "  " + archive.name + "\n").encode())
    return {"archive": str(archive), "sha256": digest, "provider_model_counts": manifest["provider_model_counts"],
            "benchmark_coverage": manifest["benchmark_coverage"],
            "pi_full_task_qualified_models": sorted(metadata["pi_full_task_qualified"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--root", type=Path, default=ROOT)
    export.add_argument("--output", type=Path, default=ROOT / "state/distribution/p8-pi-private-20261021")
    export.add_argument("--source-config", type=Path, default=ROOT / "state/distribution/p8-opencode-20261021/opencode.json")
    export.add_argument("--include-explore", action="store_true")
    export.add_argument("--base", choices=(PUBLIC_BASE,), default=PUBLIC_BASE)
    install = commands.add_parser("install")
    install.add_argument("--config-dir", type=Path, default=config_dir())
    install.add_argument("--bundle", type=Path, default=Path(__file__).parent)
    install.add_argument("--offline", action="store_true")
    install.add_argument("--include-explore", action="store_true")
    install.add_argument("--config-only", action="store_true")
    install.add_argument("--make-default", action="store_true")
    install.add_argument("--cli-dir", type=Path, default=Path.home() / ".local/share/p8-pi/pi-1.3.0")
    install.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    update = commands.add_parser("refresh")
    update.add_argument("--include-explore", action="store_true")
    update.add_argument("--config", type=Path, default=config_dir() / "models.json")
    update.add_argument("--metadata", type=Path, default=Path(__file__).with_name("model-metadata.json"))
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    if args.command == "export":
        result = bundle_export(args.root, args.output, args.source_config, args.base, args.include_explore)
    elif args.command == "install":
        verify_bundle(args.bundle)
        # Parse both destination files before changing CLI or config state.
        read_config(args.config_dir / "models.json", missing=True)
        read_config(args.config_dir / "settings.json", missing=True)
        config_result = install_config(args.bundle, args.config_dir, args.make_default, args.offline, args.include_explore)
        cli = {} if args.config_only else install_cli(args.bundle, args.cli_dir, args.bin_dir, args.config_dir)
        result = {**config_result, **cli}
    elif args.command == "refresh":
        result = refresh(args.config, args.metadata, args.include_explore)
    else:
        manifest = verify_bundle(args.bundle)
        result = {"verified": True, "pi_version": manifest["pi_version"],
                  "provider_model_counts": manifest["provider_model_counts"]}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # Do not echo config values, provider payloads, keys or command expansions.
        print("P8 Pi operation failed; check paths, JSON, bundle checksums and connectivity. No configuration secrets were printed.", file=sys.stderr)
        sys.exit(1)
