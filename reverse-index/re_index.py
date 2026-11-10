#!/usr/bin/env python3
"""reverse-index: local adapter for awesome-reverse-engineered.

Features
--------
- sync: pull data/entries.json from GitHub (conditional GET), rebuild the local
  Windows / Android / protocol / firmware subset and INDEX.md.
- translate: Chinese query -> English search terms (offline dictionary).
- search: English-first, popularity-aware search over the local index.
- subset / stats / doctor / export-rea.
- mcp: a small stdio MCP server (standard library only).
- install-hook / uninstall-hook: register a Reasonix SessionStart hook so the
  index is refreshed whenever a Reasonix session starts.

Design notes
------------
- Standard library only; UTF-8 is always explicit (Windows GBK safe).
- All writes are atomic; a failed sync never destroys the previous data.
- The adapter is deliberately read-only with respect to GitHub: it only pulls
  the CC0 data set. Pushing a mirror would require credentials and is not done.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

RAW_URL = (
    "https://raw.githubusercontent.com/alexgaoth/"
    "awesome-reverse-engineered/main/data/entries.json"
)
FALLBACK_URLS = (
    RAW_URL,
    "https://cdn.jsdelivr.net/gh/alexgaoth/awesome-reverse-engineered@main/data/entries.json",
    "https://github.com/alexgaoth/awesome-reverse-engineered/raw/main/data/entries.json",
)
SOURCE_REPO = "https://github.com/alexgaoth/awesome-reverse-engineered"
DEFAULT_ROOT = Path(__file__).resolve().parents[1]
FACET_ORDER = ("windows", "android", "protocol", "firmware")
FIELD_WEIGHTS = (
    ("name", 3.0),
    ("slug", 2.0),
    ("topics", 1.2),
    ("desc", 1.5),
    ("subcategory", 1.0),
    ("category", 1.0),
    ("platform", 1.0),
)
HEALTH_ACTIVE_DAYS = 365
HEALTH_SLOW_DAYS = 3 * 365
LOCK_STALE_SECONDS = 600


# --------------------------------------------------------------------------- #
# basic helpers
# --------------------------------------------------------------------------- #

def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def resolve_root(root: str | os.PathLike | None = None) -> Path:
    if root:
        return Path(root).resolve()
    env = os.environ.get("REVERSE_INDEX_HOME")
    return Path(env).resolve() if env else DEFAULT_ROOT


def paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "data": root / "data",
        "entries": root / "data" / "entries.json",
        "subset": root / "data" / "subset.json",
        "state": root / "data" / "state.json",
        "aliases": root / "data" / "aliases.json",
        "resurrected": root / "data" / "resurrected.json",
        "vendor": root / "vendor",
        "log": root / "sync.log",
        "index_md": root / "INDEX.md",
        "skill": root / "SKILL.md",
        "lock": root / "data" / ".sync.lock",
    }


def configure_stdio() -> None:
    """Make Chinese output safe on Windows (GBK console)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return default


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_json_atomic(path: Path, obj) -> None:
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def log_line(root: Path, message: str) -> None:
    p = paths(root)
    p["data"].mkdir(parents=True, exist_ok=True)
    with open(p["log"], "a", encoding="utf-8") as fh:
        fh.write(f"{iso_now()} {message}\n")


def norm_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    for ch in "，。、；：（）【】《》“”‘’！？·":
        value = value.replace(ch, " ")
    return re.sub(r"\s+", " ", value).strip()


def entry_slug(entry: dict) -> str:
    return entry.get("url", "").rstrip("/").split("/")[-1].lower()


def entry_text(entry: dict, include_platform: bool = False) -> str:
    keys = ("name", "desc", "category", "subcategory", "lang")
    if include_platform:
        keys = keys + ("platform",)
    topics = " ".join(entry.get("topics") or [])
    return norm_text(
        " ".join(str(entry.get(k) or "") for k in keys) + " " + topics
    )


def has_kw(text: str, kw: str) -> bool:
    kw = norm_text(kw)
    if not kw:
        return False
    if re.search(r"[^a-z0-9]", kw):
        return kw in text
    return (
        re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text)
        is not None
    )


def hit_term(term: str, text: str, strict: bool = False) -> bool:
    term = term.lower()
    if not term:
        return False
    if strict:
        # Standalone token, allowing a short suffix (decomp -> decompilation,
        # router -> routers), but not a hyphenated compound (vue-router).
        if len(term) <= 3:
            pattern = r"(?<![a-z0-9-])" + re.escape(term) + r"(?![a-z0-9-])"
        else:
            pattern = r"(?<![a-z0-9-])" + re.escape(term) + r"[a-z]{0,6}(?![a-z0-9-])"
        return re.search(pattern, text) is not None
    if len(term) <= 3:
        return has_kw(text, term)
    return term in text


def health(entry: dict) -> str:
    if entry.get("archived"):
        return "🗄️"
    pushed = entry.get("pushed") or ""
    if not pushed:
        return "⚪"
    try:
        age = (today() - dt.date.fromisoformat(pushed)).days
    except ValueError:
        return "⚪"
    if age <= HEALTH_ACTIVE_DAYS:
        return "🟢"
    if age <= HEALTH_SLOW_DAYS:
        return "🟡"
    return "🔴"


def health_label(entry: dict) -> str:
    return {
        "🟢": "active",
        "🟡": "slowing",
        "🔴": "dormant",
        "🗄️": "archived",
        "⚪": "unknown",
    }[health(entry)]


# --------------------------------------------------------------------------- #
# translation
# --------------------------------------------------------------------------- #

def load_aliases(root: Path | None = None) -> dict:
    root = resolve_root(root)
    data = read_json(paths(root)["aliases"], {}) or {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("phrases", {})
    data.setdefault("synonyms", {})
    data.setdefault("facets", {})
    data.setdefault("stopwords", [])
    return data


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if str(x).strip()]
    return []


def translate_query(query: str, aliases: dict | None = None) -> dict:
    """Translate a mostly-Chinese query into English search terms.

    Dictionary matching is longest-first and offline (no network, no model).
    A phrase is kept as one term ("packet capture") so multi-word English
    concepts still match descriptions. Unknown CJK runs are reported.
    """
    aliases = aliases or {}
    phrases: dict = aliases.get("phrases") or {}
    synonyms: dict = aliases.get("synonyms") or {}
    stopwords = {str(s).lower() for s in (aliases.get("stopwords") or [])}

    q = norm_text(query)
    mapping: list[dict] = []
    primary: list[str] = []

    for key in sorted(phrases, key=lambda k: len(k), reverse=True):
        key_norm = norm_text(key)
        if not key_norm or key_norm not in q:
            continue
        values = _as_list(phrases[key])
        if not values:
            continue
        # ASCII-only keys need a word boundary; CJK / mixed keys are substrings.
        if re.fullmatch(r"[a-z0-9 .+#_-]+", key_norm):
            pattern = r"(?<![a-z0-9])" + re.escape(key_norm) + r"(?![a-z0-9])"
            if not re.search(pattern, q):
                continue
            q = re.sub(pattern, " ", q)
        else:
            q = q.replace(key_norm, " ")
        primary.append(values[0])
        mapping.append({"source": key, "terms": values})

    # remaining ASCII tokens (C++, .NET-style tokens keep + # .)
    tokens = re.findall(r"[a-z0-9][a-z0-9+#._/-]*", q)
    terms: list[str] = []
    for item in primary + tokens:
        item = item.strip().strip(".,;:!?()[]{}")
        if not item or item in stopwords:
            continue
        if item not in terms:
            terms.append(item)

    # Synonyms are alternatives, not extra mandatory terms.
    alternatives: dict[str, list[str]] = {}
    for term in terms:
        alts: list[str] = []
        for alt in _as_list(synonyms.get(term)):
            alt = alt.lower().strip()
            if alt and alt != term and alt not in alts:
                alts.append(alt)
        # secondary translations of the same phrase are alternatives too
        for row in mapping:
            if row["terms"] and row["terms"][0] == term:
                for alt in row["terms"][1:]:
                    alt = alt.lower().strip()
                    if alt and alt != term and alt not in alts:
                        alts.append(alt)
        if alts:
            alternatives[term] = alts[:3]

    unknown = re.findall(r"[\u4e00-\u9fff]+", q)
    return {
        "query": query,
        "terms": terms[:12],
        "alternatives": alternatives,
        "unknown": unknown,
        "mapping": mapping,
    }


# --------------------------------------------------------------------------- #
# facets / subset
# --------------------------------------------------------------------------- #

def facet_matches(entry: dict, facet_rules: dict) -> dict[str, list[str]]:
    text = entry_text(entry)
    platform = norm_text(entry.get("platform") or "")
    category = entry.get("category") or ""
    found: dict[str, list[str]] = {}
    for facet, rule in facet_rules.items():
        if category in (rule.get("exclude_categories") or []):
            continue
        reasons: list[str] = []
        cats = rule.get("categories") or []
        if category in cats:
            reasons.append(f"category:{category}")
        for kw in rule.get("keywords") or []:
            if has_kw(text, kw):
                reasons.append(f"kw:{kw}")
        platform_hits = [
            f"platform:{plat}" for plat in (rule.get("platforms") or [])
            if plat and norm_text(plat) in platform
        ]
        if platform_hits and (reasons or not rule.get("platform_requires_keyword")):
            reasons = platform_hits + reasons
        if reasons:
            # de-duplicate while preserving order, cap for compact JSON
            seen = set()
            uniq = []
            for reason in reasons:
                if reason not in seen:
                    seen.add(reason)
                    uniq.append(reason)
            found[facet] = uniq[:6]
    return found


def build_subset(root: Path, entries: list[dict] | None = None, top: int = 50) -> dict:
    p = paths(root)
    entries = entries if entries is not None else (read_json(p["entries"], []) or [])
    aliases = load_aliases(root)
    rules = aliases.get("facets") or {}

    subset: list[dict] = []
    counts = {facet: 0 for facet in FACET_ORDER}
    for entry in entries:
        found = facet_matches(entry, rules)
        if not found:
            continue
        item = dict(entry)
        item["facets"] = list(found.keys())
        item["why"] = found
        subset.append(item)
        for facet in found:
            counts[facet] = counts.get(facet, 0) + 1

    # stable two-pass sort: pushed desc within the same star count
    subset.sort(key=lambda e: str(e.get("pushed") or ""), reverse=True)
    subset.sort(key=lambda e: -(e.get("stars") or 0))

    state = read_json(p["state"], {}) or {}
    payload = {
        "generated_at": iso_now(),
        "source": state.get("source_url") or RAW_URL,
        "source_sha256": state.get("sha256"),
        "rules_version": aliases.get("version", 1),
        "total_entries": len(entries),
        "total_subset": len(subset),
        "counts": counts,
        "entries": subset,
    }
    write_json_atomic(p["subset"], payload)
    state["subset_total"] = len(subset)
    state["facet_counts"] = counts
    state["subset_generated_at"] = payload["generated_at"]
    write_json_atomic(p["state"], state)
    write_index_md(root, subset, counts, len(entries), top=top)
    return {
        "total_entries": len(entries),
        "total_subset": len(subset),
        "counts": counts,
    }


def _md_escape(value: str) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def write_index_md(
    root: Path,
    subset: list[dict],
    counts: dict,
    total_entries: int,
    top: int = 50,
) -> None:
    p = paths(root)
    state = read_json(p["state"], {}) or {}
    aliases = load_aliases(root)
    rules = aliases.get("facets") or {}
    lines = [
        "# 逆向索引本地清单（Windows / Android / 协议 / 固件）",
        "",
        f"- 生成时间：{iso_now()}",
        f"- 数据源：[awesome-reverse-engineered]({SOURCE_REPO})（CC0 数据）",
        f"- 全量条目：{total_entries:,}；命中子集：{len(subset):,}（可重复计入多个 facet）",
        f"- 数据 SHA256：`{state.get('sha256') or '未同步'}`",
        f"- 自动同步：Reasonix `SessionStart` hook（`sync --hook --max-age 3600`）",
        "",
        "> 说明：按 star 降序展示每个 facet 的前 %d 条；完整结果见 `data/subset.json`。"
        "许可证为空表示仓库未声明许可证（默认保留所有权利），使用前请核对。"
        % top,
        "",
        "## 统计",
        "",
        "| Facet | 数量 | 说明 |",
        "|---|---:|---|",
    ]
    for facet in FACET_ORDER:
        rule = rules.get(facet, {})
        lines.append(
            f"| {rule.get('label', facet)} | {counts.get(facet, 0):,} | "
            f"{_md_escape(rule.get('description', ''))} |"
        )

    for facet in FACET_ORDER:
        rule = rules.get(facet, {})
        rows = [e for e in subset if facet in (e.get("facets") or [])][:top]
        if not rows:
            continue
        lines += [
            "",
            f"## {rule.get('label', facet)}（{counts.get(facet, 0):,}）",
            "",
            "| 项目 | ★ | 健康 | 语言 | 许可 | 最近推送 | 说明 |",
            "|---|---:|---|---|---|---|---|",
        ]
        for e in rows:
            name = _md_escape(e.get("name") or entry_slug(e))
            desc = _md_escape(e.get("desc") or "")
            lines.append(
                f"| [{name}]({e.get('url', '')}) | {e.get('stars') or 0:,} | "
                f"{health(e)} | {_md_escape(e.get('lang') or '')} | "
                f"{_md_escape(e.get('license') or '')} | "
                f"{_md_escape(e.get('pushed') or '')} | {desc} |"
            )
        lines.append("")

    lines += [
        "## 用法",
        "",
        "```bash",
        "# 中文查询会自动转英文检索（离线词典）",
        "python scripts/re_index.py search \"微信 协议\" --facet protocol",
        "python scripts/re_index.py search \"安卓 固件 提取\" --facet android --facet firmware",
        "python scripts/re_index.py translate \"路由器 抓包\"",
        "",
        "# 只查本地子集，按 star 排序",
        "python scripts/re_index.py search \"windows driver\" --subset-only --sort stars",
        "",
        "# 查看某条目的索引信息",
        "python scripts/re_index.py info make-all/tuya-local",
        "```",
        "",
        "## 来源与许可",
        "",
        "- 索引数据：CC0-1.0；每个被收录项目有自己的许可证。",
        "- 本项目仅做本地过滤、翻译与检索，不修改上游数据。",
        "- 自动同步只从 GitHub 拉取，不向 GitHub 推送任何内容。",
        "",
    ]
    write_text_atomic(p["index_md"], "\n".join(lines))


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #

def http_get(url: str, etag: str | None = None, last_modified: str | None = None,
             timeout: int = 60, retries: int = 2) -> tuple[int, bytes, dict]:
    headers = {"User-Agent": "reverse-index-adapter/1.0"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as ex:
            if ex.code == 304:
                return 304, b"", dict(ex.headers or {})
            last_error = ex
            if ex.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as ex:
            last_error = ex
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise last_error or RuntimeError(f"GET failed: {url}")


def curl_get(url: str, etag: str | None = None, last_modified: str | None = None,
             timeout: int = 60) -> tuple[int, bytes, dict]:
    """Download with the system curl (works where Python urllib gets reset)."""
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        raise RuntimeError("curl not found")
    cmd = [
        curl, "-sSL", "--compressed", "--max-time", str(timeout),
        "-A", "Mozilla/5.0",
        "-w", "\n%{http_code}\n%header{etag}\n%header{last-modified}",
    ]
    if etag:
        cmd += ["-H", f"If-None-Match: {etag}"]
    if last_modified:
        cmd += ["-H", f"If-Modified-Since: {last_modified}"]
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 15)
    if proc.returncode != 0:
        error = proc.stderr.decode("utf-8", "replace")[:300]
        raise RuntimeError(f"curl failed ({proc.returncode}): {error}")
    parts = proc.stdout.rsplit(b"\n", 3)
    if len(parts) != 4:
        raise RuntimeError("curl output missing status")
    body, code_raw, etag_raw, lm_raw = parts
    try:
        code = int(code_raw.strip())
    except ValueError as ex:
        raise RuntimeError(f"curl invalid status: {code_raw!r}") from ex
    headers: dict = {}
    if etag_raw.strip():
        headers["ETag"] = etag_raw.decode("utf-8", "replace").strip()
    if lm_raw.strip():
        headers["Last-Modified"] = lm_raw.decode("utf-8", "replace").strip()
    if code == 304:
        return 304, b"", headers
    if code >= 400:
        raise RuntimeError(f"HTTP {code}")
    return code, body, headers


def fetch_url(url: str, etag: str | None = None, last_modified: str | None = None,
              timeout: int = 60, retries: int = 2) -> tuple[int, bytes, dict]:
    """Prefer curl on this host; fall back to urllib when curl is absent/fails."""
    if shutil.which("curl") or shutil.which("curl.exe"):
        try:
            return curl_get(url, etag=etag, last_modified=last_modified, timeout=timeout)
        except Exception:
            pass
    return http_get(url, etag=etag, last_modified=last_modified,
                    timeout=timeout, retries=retries)


def _acquire_lock(p: dict) -> bool:
    lock: Path = p["lock"]
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                lock.unlink(missing_ok=True)
                return _acquire_lock(p)
        except OSError:
            pass
        return False


def _release_lock(p: dict) -> None:
    try:
        p["lock"].unlink(missing_ok=True)
    except OSError:
        pass


def _validate_entries(data) -> None:
    if not isinstance(data, list) or len(data) < 1000:
        raise ValueError(f"unexpected entries payload: {type(data).__name__}, "
                         f"len={len(data) if hasattr(data, '__len__') else '?'}")
    required = ("name", "url", "desc", "category")
    missing = sum(1 for e in data[:200] if not all(e.get(k) for k in required))
    if missing > 10:
        raise ValueError(f"entries missing required fields: {missing}/200")


RESURRECTED_URLS = (
    "https://raw.githubusercontent.com/alexgaoth/"
    "awesome-reverse-engineered/main/data/resurrected.json",
    "https://cdn.jsdelivr.net/gh/alexgaoth/"
    "awesome-reverse-engineered@main/data/resurrected.json",
)


def _sync_resurrected(root: Path, timeout: int = 60) -> int:
    """Best-effort refresh of the `resurrected` dataset (dead products, revived).

    It changes slowly and the main sync must not fail when it is unreachable, so this
    keeps the previous file and re-raises only when every source failed.
    """
    p = paths(root)
    last: Exception | None = None
    for url in RESURRECTED_URLS:
        try:
            status, body, _headers = fetch_url(url, timeout=timeout, retries=1)
            if status == 304:
                return len(load_resurrected(root))
            rows = json.loads(body.decode("utf-8"))
            if not isinstance(rows, list) or not rows:
                raise ValueError("not a non-empty list of rows")
            write_json_atomic(p["resurrected"], rows)
            log_line(root, f"sync: resurrected updated rows={len(rows)}")
            return len(rows)
        except Exception as ex:
            last = ex
            log_line(root, f"sync: resurrected source failed {url}: {type(ex).__name__}: {ex}")
    if last:
        raise last
    return len(load_resurrected(root))


def sync(root: Path | None = None, source: str | None = None, force: bool = False,
         max_age: int = 0, timeout: int = 60, quiet: bool = False,
         retries: int = 2) -> dict:
    root = resolve_root(root)
    p = paths(root)
    p["data"].mkdir(parents=True, exist_ok=True)
    state = read_json(p["state"], {}) or {}

    if not force and max_age > 0 and p["entries"].exists():
        last = float(state.get("last_checked_ts") or 0)
        if time.time() - last < max_age:
            return {
                "status": "skipped",
                "reason": f"last checked {int(time.time() - last)}s ago",
                "total": state.get("total"),
            }

    if not _acquire_lock(p):
        return {"status": "skipped", "reason": "another sync is running"}

    try:
        headers: dict = {}
        body = b""
        data = None
        canonical_source = source or RAW_URL
        fetched_from = canonical_source
        if source and not re.match(r"^https?://", source):
            body = Path(source).read_bytes()
            data = json.loads(body.decode("utf-8"))
            _validate_entries(data)
        else:
            urls = [source] if source else list(FALLBACK_URLS)
            last_error: Exception | None = None
            for url in urls:
                try:
                    status, candidate, headers = fetch_url(
                        url,
                        etag=None if force else state.get("etag"),
                        last_modified=None if force else state.get("last_modified"),
                        timeout=timeout,
                        retries=retries,
                    )
                    if status == 304:
                        state["last_checked"] = iso_now()
                        state["last_checked_ts"] = time.time()
                        write_json_atomic(p["state"], state)
                        log_line(root, "sync: 304 not modified")
                        return {"status": "not-modified", "total": state.get("total")}
                    parsed = json.loads(candidate.decode("utf-8"))
                    _validate_entries(parsed)
                    body, data, fetched_from = candidate, parsed, url
                    break
                except Exception as ex:
                    last_error = ex
                    log_line(root, f"sync: source failed {url}: {type(ex).__name__}: {ex}")
                    continue
            if data is None:
                raise last_error or RuntimeError("no data source available")

        digest = sha256_bytes(body)
        if (
            not force
            and digest == state.get("sha256")
            and p["subset"].exists()
            and p["index_md"].exists()
        ):
            state["last_checked"] = iso_now()
            state["last_checked_ts"] = time.time()
            write_json_atomic(p["state"], state)
            log_line(root, "sync: content unchanged")
            return {"status": "unchanged", "total": state.get("total")}

        data = json.loads(body.decode("utf-8"))
        _validate_entries(data)

        # preserve previous file until validation passed
        write_json_atomic(p["entries"], data)
        state.update(
            {
                "last_checked": iso_now(),
                "last_checked_ts": time.time(),
                "last_synced": iso_now(),
                "source_url": RAW_URL if (source is None or not re.match(r"^https?://", source)) else source,
                "imported_from": source if source and not re.match(r"^https?://", source) else None,
                "fetched_from": fetched_from,
                "sha256": digest,
                "total": len(data),
                "etag": headers.get("ETag") or state.get("etag"),
                "last_modified": headers.get("Last-Modified") or state.get("last_modified"),
            }
        )
        write_json_atomic(p["state"], state)
        subset = build_subset(root, data)
        state["subset_total"] = subset["total_subset"]
        state["facet_counts"] = subset["counts"]
        write_json_atomic(p["state"], state)
        summary = {
            "status": "updated",
            "total": len(data),
            "subset_total": subset["total_subset"],
            "counts": subset["counts"],
            "sha256": digest,
        }
        log_line(root, f"sync: updated total={len(data)} subset={subset['total_subset']} sha={digest[:12]}")
        try:
            summary["resurrected_total"] = _sync_resurrected(root, timeout=timeout)
        except Exception as ex:
            log_line(root, f"sync: resurrected skipped {type(ex).__name__}: {ex}")
            summary["resurrected_total"] = len(load_resurrected(root))
        return summary
    except Exception as ex:
        log_line(root, f"sync: ERROR {type(ex).__name__}: {ex}")
        if p["entries"].exists():
            return {"status": "error", "error": str(ex), "kept_previous": True}
        raise
    finally:
        _release_lock(p)


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #

def load_entries(root: Path | None = None) -> list[dict]:
    root = resolve_root(root)
    data = read_json(paths(root)["entries"], [])
    return data if isinstance(data, list) else []


def load_subset(root: Path | None = None) -> list[dict]:
    root = resolve_root(root)
    data = read_json(paths(root)["subset"], {}) or {}
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def _field_texts(entry: dict) -> list[tuple[str, str]]:
    return [
        ("name", norm_text(entry.get("name") or "")),
        ("slug", norm_text(entry_slug(entry))),
        ("topics", norm_text(" ".join(entry.get("topics") or []))),
        ("desc", norm_text(entry.get("desc") or "")),
        ("subcategory", norm_text(entry.get("subcategory") or "")),
        ("category", norm_text(entry.get("category") or "")),
        ("platform", norm_text(entry.get("platform") or "")),
    ]


def score_entry(entry: dict, terms: list[str], alternatives: dict[str, list[str]],
                facet: str | None = None) -> dict:
    fields = dict(_field_texts(entry))
    base = 0.0
    matched = 0
    matched_terms: list[str] = []
    for term in terms:
        variants = [term] + list(alternatives.get(term) or [])
        best = 0.0
        for index, variant in enumerate(variants):
            penalty = 1.0 if index == 0 else 0.8
            for field, weight in FIELD_WEIGHTS:
                # Strict matching everywhere: standalone tokens/short suffixes,
                # never hyphenated compounds such as vue-router.
                if hit_term(variant, fields[field], strict=True):
                    best = max(best, weight * penalty)
        if best > 0:
            matched += 1
            matched_terms.append(term)
            base += best

    missing = max(0, len(terms) - matched)
    stars = entry.get("stars") or 0
    popularity = min(6.0, 1.3 * math.log10(stars + 1))
    fresh = 0.0
    pushed = entry.get("pushed") or ""
    if pushed:
        try:
            if (today() - dt.date.fromisoformat(pushed)).days <= HEALTH_ACTIVE_DAYS:
                fresh = 0.5
        except ValueError:
            pass
    archived = -1.5 if entry.get("archived") else 0.0
    facet_boost = 0.25 if facet and facet in (entry.get("facets") or []) else 0.0
    score = base + popularity + fresh + archived - 1.2 * missing + facet_boost
    return {
        "score": round(score, 4),
        "matched": matched,
        "matched_terms": matched_terms,
        "partial": missing > 0,
    }


def search(
    root: Path | None = None,
    query: str = "",
    facets: list[str] | None = None,
    category: str | None = None,
    lang: str | None = None,
    min_stars: int = 0,
    active_only: bool = False,
    include_archived: bool = False,
    limit: int = 20,
    sort: str = "relevance",
    do_translate: bool = True,
    subset_only: bool = False,
) -> dict:
    root = resolve_root(root)
    aliases = load_aliases(root)
    translated = (
        translate_query(query, aliases)
        if do_translate
        else {"query": query, "terms": [t for t in norm_text(query).split() if t],
              "alternatives": {}, "unknown": [], "mapping": []}
    )
    terms = translated["terms"]
    if not terms:
        return {"query": query, "translated": translated, "results": [], "match_mode": "none"}

    entries = load_subset(root) if subset_only else load_entries(root)
    if not entries:
        raise FileNotFoundError("index data missing; run: re_index.py sync")

    wanted = set(facets or [])
    rules = aliases.get("facets") or {}
    results: list[dict] = []
    for entry in entries:
        if wanted:
            found = entry.get("facets") or facet_matches(entry, rules)
            if not wanted.intersection(found):
                continue
            entry = dict(entry, facets=list(found))
        if category and category.lower() not in (
            (entry.get("category") or "") + " " + (entry.get("subcategory") or "")
        ).lower():
            continue
        if lang and (entry.get("lang") or "").lower() != lang.lower():
            continue
        if (entry.get("stars") or 0) < min_stars:
            continue
        if entry.get("archived") and not include_archived:
            continue
        if active_only:
            pushed = entry.get("pushed") or ""
            try:
                if not pushed or (today() - dt.date.fromisoformat(pushed)).days > HEALTH_ACTIVE_DAYS:
                    continue
            except ValueError:
                continue

        scored = score_entry(entry, terms, translated["alternatives"],
                             next(iter(wanted), None) if len(wanted) == 1 else None)
        if scored["matched"] == 0 or scored["score"] <= 0:
            continue
        results.append(dict(entry, _score=scored))

    if sort == "stars":
        results.sort(key=lambda e: (-(e.get("stars") or 0), -(e.get("_score") or {}).get("score", 0)))
    elif sort == "updated":
        results.sort(key=lambda e: (str(e.get("pushed") or "")), reverse=True)
    else:
        # Partial matches with more matched terms must outrank high-star
        # projects that only matched one word.
        results.sort(
            key=lambda e: (
                -((e.get("_score") or {}).get("matched") or 0),
                -((e.get("_score") or {}).get("score") or 0),
                -(e.get("stars") or 0),
            )
        )

    full = [
        e for e in results
        if (e.get("_score") or {}).get("matched") == len(terms)
    ]
    partial = [
        e for e in results
        if (e.get("_score") or {}).get("matched") != len(terms)
    ]
    if full:
        # Full matches always win; partial matches only fill the remaining slots.
        chosen = full + partial[: max(0, limit - len(full))]
        mode = "all"
    else:
        chosen = partial
        mode = "partial"
    top = chosen[: max(1, limit)]
    for e in top:
        e.pop("_score", None)
    return {
        "query": query,
        "translated": translated,
        "facets": sorted(wanted),
        "match_mode": mode,
        "full_count": len(full),
        "partial_count": len(partial),
        "result_count": len(results),
        "results": top,
    }


def fmt_result(index: int, entry: dict, verbose: bool = False) -> str:
    meta = [
        f"★{entry.get('stars') or 0:,}",
        entry.get("lang") or "",
        entry.get("license") or "no license",
        f"pushed {entry.get('pushed')}" if entry.get("pushed") else "",
        health(entry),
        "ARCHIVED" if entry.get("archived") else "",
    ]
    where = entry.get("category") or ""
    if entry.get("subcategory"):
        where += f" › {entry['subcategory']}"
    facets = entry.get("facets") or []
    line = (
        f"{index}. {entry.get('name')}  " + " · ".join(m for m in meta if m)
        + f"\n   {entry.get('url')}\n   {entry.get('desc') or ''}  [{where}]"
    )
    if facets:
        line += f"  facets={','.join(facets)}"
    return line


def find_entry(root: Path, ref: str) -> dict | None:
    """Match an entry by `owner/repo`, a GitHub URL, or a bare name.

    `owner/repo` is what people and the upstream docs write, so the scheme and the
    `github.com/` prefix are stripped from both sides before comparing.
    """
    ref_norm = ref.strip().rstrip("/").lower()
    ref_norm = re.sub(r"^https?://(www\.)?", "", ref_norm)
    ref_norm = re.sub(r"^github\.com/", "", ref_norm)
    if not ref_norm:
        return None
    for entry in load_entries(root):
        url = re.sub(r"^https?://(www\.)?", "", (entry.get("url") or "").rstrip("/").lower())
        url = re.sub(r"^github\.com/", "", url)
        if url == ref_norm or (entry.get("name") or "").lower() == ref_norm:
            return entry
    return None


def load_resurrected(root: Path) -> list[dict]:
    """Dead products their makers killed, kept alive by open-source projects."""
    rows = read_json(paths(root)["resurrected"], default=None)
    return rows if isinstance(rows, list) else []


def search_resurrected(root: Path, query: str = "", limit: int = 20, kind: str = "") -> list[dict]:
    """Match a dead product, its maker, what was shut down, or a reviving project."""
    rows = load_resurrected(root)
    terms = [t for t in re.split(r"\s+", (query or "").strip().lower()) if t]
    out = []
    for row in rows:
        if kind and (row.get("kind") or "").lower() != kind.lower():
            continue
        revived = " ".join(row.get("revived_by") or [])
        haystack = " ".join(str(row.get(k) or "") for k in ("product", "maker", "what", "kind")).lower()
        haystack = f"{haystack} {revived.lower()}"
        if terms and not all(t in haystack for t in terms):
            continue
        out.append(row)
    out.sort(key=lambda r: (r.get("killed") or 0), reverse=True)
    return out[: max(1, limit)]


def fmt_resurrected(row: dict, index: int = 1) -> str:
    revived = row.get("revived_by") or []
    lines = [
        f"[{index}] {row.get('product', '?')}",
        f"    被 {row.get('maker') or '?'} 终止于 {row.get('killed') or '?'} 年（{row.get('kind') or '?'}）",
    ]
    if row.get("what"):
        lines.append(f"    经过: {row['what']}")
    for url in revived[:6]:
        lines.append(f"    复活: {url}")
    if len(revived) > 6:
        lines.append(f"    …另有 {len(revived) - 6} 个复活项目")
    if row.get("source"):
        lines.append(f"    来源: {row['source']}")
    return "\n".join(lines)


def clone_project(root: Path, ref: str, dest: str | None = None) -> dict:
    """Shallow-clone an indexed project so its code can be read, not just linked.

    Refuses anything the index does not list: this is a research shortcut, not a
    general-purpose cloner. Runs `git clone --depth 1`; nothing is built or executed.
    """
    entry = find_entry(root, ref)
    if not entry:
        raise KeyError(ref)
    url = entry.get("url") or ""
    match = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", url)
    if not match:
        raise ValueError(url)
    slug = f"{match[1]}__{re.sub(r'[^A-Za-z0-9._-]+', '-', match[2])}"
    target = Path(dest).expanduser() if dest else paths(root)["vendor"] / slug
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and any(target.iterdir()):
        return {"status": "exists", "path": str(target), "url": url, "name": entry.get("name")}
    cmd = ["git", "clone", "--depth", "1", "--single-branch", url, str(target)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[:500])
    return {
        "status": "cloned",
        "path": str(target),
        "url": url,
        "name": entry.get("name"),
        "stars": entry.get("stars"),
        "license": entry.get("license"),
    }


def fetch_readme(url: str, lines: int = 80) -> str:
    match = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", url)
    if not match:
        return "(not a GitHub URL; README not fetched)"
    api = f"https://api.github.com/repos/{match[1]}/{match[2]}/readme"
    req = urllib.request.Request(api, headers={"Accept": "application/vnd.github.raw",
                                               "User-Agent": "reverse-index-adapter/1.0"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as ex:
        return f"(could not fetch README: {ex})"
    return "\n".join(text.splitlines()[:lines])


# --------------------------------------------------------------------------- #
# stats / doctor / export
# --------------------------------------------------------------------------- #

def stats(root: Path | None = None) -> dict:
    root = resolve_root(root)
    entries = load_entries(root)
    state = read_json(paths(root)["state"], {}) or {}
    subset = read_json(paths(root)["subset"], {}) or {}
    languages: dict[str, int] = {}
    categories: dict[str, int] = {}
    licenses: dict[str, int] = {}
    for entry in entries:
        languages[entry.get("lang") or "(none)"] = languages.get(entry.get("lang") or "(none)", 0) + 1
        categories[entry.get("category") or ""] = categories.get(entry.get("category") or "", 0) + 1
        licenses[entry.get("license") or "(none)"] = licenses.get(entry.get("license") or "(none)", 0) + 1
    return {
        "total": len(entries),
        "stars": sum(e.get("stars") or 0 for e in entries),
        "archived": sum(1 for e in entries if e.get("archived")),
        "last_synced": state.get("last_synced"),
        "last_checked": state.get("last_checked"),
        "source": state.get("source_url"),
        "sha256": state.get("sha256"),
        "subset_total": subset.get("total_subset", 0),
        "facet_counts": subset.get("counts", {}),
        "categories": dict(sorted(categories.items(), key=lambda x: -x[1])),
        "languages": dict(sorted(languages.items(), key=lambda x: -x[1])[:15]),
        "licenses": dict(sorted(licenses.items(), key=lambda x: -x[1])[:12]),
    }


def settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    candidates = []
    if appdata:
        candidates.append(Path(appdata) / "reasonix" / "settings.json")
    candidates.append(Path.home() / ".reasonix" / "settings.json")
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def hook_entry(root: Path) -> dict:
    script = paths(root)["root"] / "scripts" / "re_index.py"
    command = (
        f'"{sys.executable}" "{script}" sync --hook --max-age 3600 '
        f'--timeout 45 --retries 1 --detach'
    )
    return {"command": command, "description": "reverse-index sync (auto-generated)"}


def install_hook(root: Path | None = None) -> dict:
    root = resolve_root(root)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config = read_json(path, {}) or {}
    if path.exists():
        backup = path.with_name(path.name + "." + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
        shutil.copy2(path, backup)
    else:
        backup = None
    hooks = config.setdefault("hooks", {})
    session = hooks.setdefault("SessionStart", [])
    if not isinstance(session, list):
        session = []
        hooks["SessionStart"] = session
    session[:] = [
        h for h in session
        if not (isinstance(h, dict) and str(h.get("description", "")).startswith("reverse-index"))
    ]
    session.append(hook_entry(root))
    write_text_atomic(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")

    skill_src = paths(root)["skill"]
    skill_dst = Path.home() / ".reasonix" / "skills" / "reverse-index" / "SKILL.md"
    if skill_src.exists():
        skill_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_src, skill_dst)
    return {
        "settings": str(path),
        "backup": str(backup) if backup else None,
        "hook": hook_entry(root),
        "skill": str(skill_dst),
    }


def uninstall_hook(root: Path | None = None) -> dict:
    root = resolve_root(root)
    path = settings_path()
    config = read_json(path, {}) or {}
    hooks = config.get("hooks") or {}
    session = hooks.get("SessionStart") or []
    if not isinstance(session, list):
        session = []
    kept = [
        h for h in session
        if not (isinstance(h, dict) and str(h.get("description", "")).startswith("reverse-index"))
    ]
    hooks["SessionStart"] = kept
    config["hooks"] = hooks
    backup = None
    if path.exists():
        backup = path.with_name(path.name + "." + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
        shutil.copy2(path, backup)
    write_text_atomic(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return {"settings": str(path), "removed": len(session) - len(kept),
            "backup": str(backup) if backup else None}


def doctor(root: Path | None = None) -> dict:
    root = resolve_root(root)
    p = paths(root)
    state = read_json(p["state"], {}) or {}
    entries = load_entries(root)
    subset = read_json(p["subset"], {}) or {}
    config = read_json(settings_path(), {}) or {}
    session = (config.get("hooks") or {}).get("SessionStart") or []
    hook_ok = any(
        isinstance(h, dict) and str(h.get("description", "")).startswith("reverse-index")
        for h in session
    )
    age = None
    if state.get("last_checked_ts"):
        age = int(time.time() - float(state["last_checked_ts"]))
    return {
        "root": str(root),
        "entries": len(entries),
        "entries_exists": p["entries"].exists(),
        "subset_total": subset.get("total_subset", 0),
        "facet_counts": subset.get("counts", {}),
        "aliases": len((load_aliases(root).get("phrases") or {})),
        "last_checked": state.get("last_checked"),
        "last_synced": state.get("last_synced"),
        "seconds_since_check": age,
        "hook_registered": hook_ok,
        "settings": str(settings_path()),
        "python": sys.executable,
        "encoding": sys.getdefaultencoding(),
    }


def export_rea(root: Path | None = None, facets: list[str] | None = None) -> None:
    root = resolve_root(root)
    entries = load_subset(root) or load_entries(root)
    wanted = set(facets or [])
    for entry in entries:
        if wanted and not wanted.intersection(entry.get("facets") or []):
            continue
        row = {
            "name": entry.get("name"),
            "url": entry.get("url"),
            "desc": entry.get("desc"),
            "category": entry.get("category"),
            "subcategory": entry.get("subcategory"),
            "stars": entry.get("stars") or 0,
            "license": entry.get("license") or "",
            "pushed": entry.get("pushed") or "",
            "lang": entry.get("lang") or "",
            "archived": bool(entry.get("archived")),
            "facets": entry.get("facets") or [],
        }
        print(json.dumps(row, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# MCP server
# --------------------------------------------------------------------------- #

MCP_TOOLS = [
    {
        "name": "search_reverse_index",
        "description": (
            "Search 17,000+ open-source projects built by reverse engineering closed apps, "
            "APIs, devices, protocols, file formats and games. Chinese queries are translated "
            "to English offline. Use before reverse engineering something."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "facet": {"type": "string", "enum": list(FACET_ORDER)},
                "category": {"type": "string"},
                "min_stars": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "translate": {"type": "boolean", "default": True},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_reverse_index_entry",
        "description": "Return one entry (stars, license, health, facets) and optionally its README.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "readme_lines": {"type": "integer", "minimum": 0, "maximum": 200, "default": 0},
            },
            "required": ["repo"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "sync_reverse_index",
        "description": "Pull the latest CC0 index from GitHub and rebuild the local subset.",
        "inputSchema": {
            "type": "object",
            "properties": {"force": {"type": "boolean", "default": False}},
        },
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "list_reverse_index_facets",
        "description": "List facet counts (Windows / Android / protocol / firmware).",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "translate_reverse_query",
        "description": "Translate a Chinese query into English search terms without searching.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "search_resurrected_products",
        "description": (
            "Products their makers killed and open-source projects brought back (129 services, "
            "devices and games, with the repos that revived each one). Use when a target is dead "
            "or shut down: someone may have already reverse engineered a replacement."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {"type": "string", "description": "service, device, game, app…"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
]


def mcp_call(root: Path, name: str, args: dict) -> tuple[str, dict]:
    if name == "search_reverse_index":
        facet = args.get("facet")
        out = search(
            root,
            query=str(args.get("query") or ""),
            facets=[facet] if facet else None,
            category=args.get("category"),
            min_stars=int(args.get("min_stars") or 0),
            limit=max(1, min(50, int(args.get("limit") or 10))),
            do_translate=bool(args.get("translate", True)),
        )
        text = "\n\n".join(fmt_result(i, e) for i, e in enumerate(out["results"], 1))
        if not text:
            text = "No matches. Try fewer words, a vendor name, or a facet."
        if out["match_mode"] == "partial":
            text = "(partial matches)\n\n" + text
        return text, out
    if name == "get_reverse_index_entry":
        ref = str(args.get("repo") or "")
        entry = find_entry(root, ref)
        text = fmt_result(1, entry) if entry else f"(not in index) {ref}"
        lines = int(args.get("readme_lines") or 0)
        if entry and lines:
            text += "\n\n--- README ---\n" + fetch_readme(entry["url"], lines)
        return text, {"entry": entry}
    if name == "sync_reverse_index":
        out = sync(root, force=bool(args.get("force")), max_age=0, quiet=True)
        return json.dumps(out, ensure_ascii=False, indent=1), out
    if name == "list_reverse_index_facets":
        subset = read_json(paths(root)["subset"], {}) or {}
        data = {"counts": subset.get("counts", {}), "total_subset": subset.get("total_subset", 0)}
        return json.dumps(data, ensure_ascii=False, indent=1), data
    if name == "translate_reverse_query":
        data = translate_query(str(args.get("query") or ""), load_aliases(root))
        return json.dumps(data, ensure_ascii=False, indent=1), data
    if name == "search_resurrected_products":
        rows = search_resurrected(
            root,
            str(args.get("query") or ""),
            limit=max(1, min(100, int(args.get("limit") or 20))),
            kind=str(args.get("kind") or ""),
        )
        text = "\n\n".join(fmt_resurrected(r, i) for i, r in enumerate(rows, 1))
        if not text:
            text = "No matches. Try a product name, a vendor (Nintendo, Spotify), or drop the query."
        return text, {"results": rows, "total_local": len(load_resurrected(root))}
    raise LookupError(f"unknown tool: {name}")


def mcp_serve(root: Path) -> None:
    configure_stdio()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "parse error"}}),
                  flush=True)
            continue
        if msg.get("id") is None:
            continue  # notification
        method = msg.get("method")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                versions = ("2025-06-18", "2025-03-26", "2024-11-05")
                result = {
                    "protocolVersion": asked if asked in versions else versions[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "reverse-index", "version": "1.0.0"},
                    "instructions": (
                        "Search this index before reverse engineering a closed product. "
                        "Chinese queries are translated to English automatically."
                    ),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": MCP_TOOLS}
            elif method == "tools/call":
                name = params.get("name")
                if name not in {t["name"] for t in MCP_TOOLS}:
                    raise LookupError(f"unknown tool: {name}")
                text, data = mcp_call(root, name, params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": text}],
                          "structuredContent": data, "isError": False}
            else:
                raise NotImplementedError(method)
            print(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": result},
                             ensure_ascii=False), flush=True)
        except NotImplementedError as ex:
            print(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"),
                              "error": {"code": -32601, "message": str(ex)}}), flush=True)
        except LookupError as ex:
            print(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"),
                              "error": {"code": -32602, "message": str(ex)}}), flush=True)
        except Exception as ex:
            print(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"),
                              "result": {"content": [{"type": "text",
                                                      "text": f"error: {ex}"}],
                                         "isError": True}}, ensure_ascii=False), flush=True)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def print_stats(data: dict) -> None:
    print(f"条目总数: {data['total']:,}")
    print(f"星标合计: {data['stars']:,}")
    print(f"归档: {data['archived']:,}")
    print(f"最近检查: {data.get('last_checked')}")
    print(f"最近同步: {data.get('last_synced')}")
    print(f"子集: {data.get('subset_total')}  {data.get('facet_counts')}")
    print("\n分类:")
    for name, count in list(data["categories"].items())[:20]:
        print(f"  {name}: {count:,}")
    print("\n语言:")
    for name, count in data["languages"].items():
        print(f"  {name}: {count:,}")
    print("\n许可:")
    for name, count in data["licenses"].items():
        print(f"  {name}: {count:,}")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(
        prog="re_index.py",
        description="Local adapter for awesome-reverse-engineered (Chinese->English, facet subset, Reasonix sync).",
    )
    parser.add_argument("--root", help="project root (default: this repository)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="pull latest data and rebuild subset/INDEX.md")
    p_sync.add_argument("--source", help="local entries.json or URL")
    p_sync.add_argument("--force", action="store_true")
    p_sync.add_argument("--max-age", type=int, default=0,
                        help="skip if checked within N seconds")
    p_sync.add_argument("--timeout", type=int, default=60)
    p_sync.add_argument("--retries", type=int, default=2)
    p_sync.add_argument("--quiet", action="store_true")
    p_sync.add_argument("--hook", action="store_true",
                        help="hook mode: never fail, never block a session")
    p_sync.add_argument("--detach", action="store_true",
                        help="spawn a detached sync and return immediately (SessionStart hook)")

    p_search = sub.add_parser("search", help="search the index")
    p_search.add_argument("query", nargs="+")
    p_search.add_argument("--facet", action="append", choices=list(FACET_ORDER))
    p_search.add_argument("--category")
    p_search.add_argument("--lang")
    p_search.add_argument("--min-stars", type=int, default=0)
    p_search.add_argument("--active", action="store_true")
    p_search.add_argument("--include-archived", action="store_true")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--sort", choices=("relevance", "stars", "updated"), default="relevance")
    p_search.add_argument("--json", action="store_true")
    p_search.add_argument("--no-translate", action="store_true")
    p_search.add_argument("--subset-only", action="store_true")
    p_search.add_argument("--explain", action="store_true")

    p_translate = sub.add_parser("translate", help="show the English search terms")
    p_translate.add_argument("query", nargs="+")

    p_info = sub.add_parser("info", help="show one entry")
    p_info.add_argument("repo")
    p_info.add_argument("--lines", type=int, default=0)

    p_clone = sub.add_parser("clone", help="shallow-clone an indexed project into vendor/ so its code can be read")
    p_clone.add_argument("repo")
    p_clone.add_argument("--dest", help="target folder (default: vendor/<owner>__<repo>)")

    p_res = sub.add_parser("resurrected", help="products their makers killed, kept alive by open-source reverse engineering")
    p_res.add_argument("query", nargs="*")
    p_res.add_argument("--kind", help="service, device, app…")
    p_res.add_argument("--limit", type=int, default=20)
    p_res.add_argument("--json", action="store_true")

    p_subset = sub.add_parser("subset", help="rebuild subset from local entries")
    p_subset.add_argument("--top", type=int, default=50)

    sub.add_parser("stats", help="show local data statistics")
    sub.add_parser("doctor", help="diagnose data, hook and encoding")
    sub.add_parser("install-hook", help="register Reasonix SessionStart hook + skill")
    sub.add_parser("uninstall-hook", help="remove the SessionStart hook")
    sub.add_parser("mcp", help="run the stdio MCP server")

    p_export = sub.add_parser("export-rea", help="JSONL export for REA/agent workflows")
    p_export.add_argument("--facet", action="append", choices=list(FACET_ORDER))

    args = parser.parse_args(argv)
    root = resolve_root(args.root)

    if args.command == "sync":
        if args.detach:
            cmd = [
                sys.executable,
                str(paths(root)["root"] / "scripts" / "re_index.py"),
                "sync", "--hook", "--max-age", str(args.max_age),
                "--timeout", str(args.timeout), "--retries", str(args.retries),
                "--quiet",
            ]
            if args.source:
                cmd += ["--source", args.source]
            if args.force:
                cmd.append("--force")
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = 0x00000008 | 0x08000000  # DETACHED | NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, **kwargs,
            )
            return 0
        try:
            out = sync(root, source=args.source, force=args.force,
                       max_age=args.max_age, timeout=args.timeout, quiet=args.quiet,
                       retries=args.retries)
            if not args.quiet:
                print(json.dumps(out, ensure_ascii=False, indent=1))
            return 0
        except Exception as ex:
            log_line(root, f"sync: FATAL {type(ex).__name__}: {ex}")
            if args.hook:
                return 0
            print(f"sync failed: {ex}", file=sys.stderr)
            return 1

    if args.command == "search":
        query = " ".join(args.query)
        try:
            out = search(
                root, query=query, facets=args.facet, category=args.category,
                lang=args.lang, min_stars=args.min_stars, active_only=args.active,
                include_archived=args.include_archived, limit=args.limit,
                sort=args.sort, do_translate=not args.no_translate,
                subset_only=args.subset_only,
            )
        except FileNotFoundError as ex:
            print(str(ex), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return 0
        tr = out["translated"]
        if args.explain or tr["mapping"]:
            mapped = " | ".join(
                f"{row['source']}→{','.join(row['terms'])}" for row in tr["mapping"]
            )
            print(f"英文检索: {' '.join(tr['terms'])}")
            if mapped:
                print(f"映射: {mapped}")
            if tr["unknown"]:
                print(f"未收录中文: {', '.join(tr['unknown'])}（可在 data/aliases.json 补充）")
            print()
        if not out["results"]:
            print("没有匹配。换更少的词、厂商名，或用 `translate` 查看英文检索词。")
            return 0
        if out["match_mode"] == "partial":
            print("(部分匹配)\n")
        for i, entry in enumerate(out["results"], 1):
            print(fmt_result(i, entry))
            print()
        print(
            f"全匹配 {out.get('full_count', 0)} 条，部分匹配 {out.get('partial_count', 0)} 条；"
            f"显示 {len(out['results'])} 条。"
        )
        return 0

    if args.command == "translate":
        result = translate_query(" ".join(args.query), load_aliases(root))
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0

    if args.command == "info":
        entry = find_entry(root, args.repo)
        if not entry:
            print(f"(not in index) {args.repo}")
            return 1
        print(fmt_result(1, entry))
        if args.lines:
            print("\n--- README ---")
            print(fetch_readme(entry["url"], args.lines))
        return 0

    if args.command == "clone":
        try:
            out = clone_project(root, args.repo, args.dest)
        except KeyError:
            print(f"(not in index) {args.repo}", file=sys.stderr)
            print("本命令只克隆索引里已收录的项目：先 search 确认，再 info 看许可。", file=sys.stderr)
            return 1
        except ValueError as ex:
            print(f"不是可克隆的 GitHub 项目: {ex}", file=sys.stderr)
            return 1
        except Exception as ex:
            print(f"clone failed: {ex}", file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False, indent=1))
        if out["status"] == "cloned":
            print("\n下一步（读源码，而不是只读链接）：")
            print(f'  craft-anything-cli --deep core-target-open --path "{out["path"]}"')
        return 0

    if args.command == "resurrected":
        rows = search_resurrected(root, " ".join(args.query), limit=args.limit, kind=args.kind or "")
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
            return 0
        if not rows:
            print("没有匹配。试用产品名、厂商名（Nintendo、Spotify），或不带关键词列出全部。")
            return 0
        for i, row in enumerate(rows, 1):
            print(fmt_resurrected(row, i))
            print()
        print(f"共 {len(rows)} 条（本地 resurrected 数据集共 {len(load_resurrected(root))} 条）")
        return 0

    if args.command == "subset":
        out = build_subset(root, top=args.top)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    if args.command == "stats":
        print_stats(stats(root))
        return 0

    if args.command == "doctor":
        print(json.dumps(doctor(root), ensure_ascii=False, indent=1))
        return 0

    if args.command == "install-hook":
        print(json.dumps(install_hook(root), ensure_ascii=False, indent=1))
        return 0

    if args.command == "uninstall-hook":
        print(json.dumps(uninstall_hook(root), ensure_ascii=False, indent=1))
        return 0

    if args.command == "mcp":
        mcp_serve(root)
        return 0

    if args.command == "export-rea":
        export_rea(root, args.facet)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
