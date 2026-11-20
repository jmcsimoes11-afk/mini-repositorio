#!/usr/bin/env python3
"""Aggregates public service alerts from Coimbra transit operators into avisos.json."""
from __future__ import annotations

import argparse
import datetime as dt
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from typing import Iterable
from urllib.parse import urlparse
import urllib.error
import urllib.request

ALLOWED_DOMAINS = frozenset({
    "smtuc.pt",
    "www.smtuc.pt",
    "sit-regiaodecoimbra.pt",
    "www.sit-regiaodecoimbra.pt",
    "metromondego.pt",
    "www.metromondego.pt",
})

DEFAULT_FALLBACK_URL = "https://jmcsimoes11-afk.github.io/mini-repositorio/avisos.json"

SOURCES = (
    (
        "SMTUC",
        "https://www.smtuc.pt/wp-json/wp/v2/posts?_fields=id,date_gmt,modified_gmt,link,title,excerpt&per_page=20",
        45,
        False,
    ),
    (
        "SIT",
        "https://sit-regiaodecoimbra.pt/wp-json/wp/v2/posts?_fields=id,date_gmt,modified_gmt,link,title,excerpt&per_page=20",
        180,
        True,
    ),
)

USER_AGENT = "MobilidadeCoimbraAlerts/1.1 (+https://github.com/jmcsimoes11-afk/mini-repositorio)"
MAX_SUMMARY_CHARS = 280
MAX_ALERTS = 50
DEFAULT_MAX_AGE_DAYS = 45
FETCH_TIMEOUT_SECONDS = 25
FETCH_RETRIES = 3


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        cleaned = re.sub(r"\s+", " ", html.unescape(raw)).strip()
        cleaned = re.sub(r"\s*\[\s*…\s*\]\s*$", "…", cleaned)
        return cleaned


def strip_html(value: str) -> str:
    if not value:
        return ""
    extractor = _TextExtractor()
    extractor.feed(value)
    extractor.close()
    return extractor.text()


def validate_official_url(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_DOMAINS:
        return None
    return url.strip()


def classify_category(title: str, summary: str) -> str:
    text = f"{title} {summary}".casefold()
    if any(k in text for k in ("greve", "paralisação", "paralizacao", "pré-aviso", "pre-aviso")):
        return "STRIKE"
    if any(
        k in text
        for k in (
            "desvio",
            "alteração de percurso",
            "alteracao de percurso",
            "alteração percurso",
            "alteracao percurso",
            "alteração paragem",
            "alteracao paragem",
            "percurso alternativo",
            "corte",
            "cortada",
            "cortado",
            "suprimida",
            "suprimido",
            "interrompida",
            "interrompido",
            "condicionamento",
            "constrangimento",
            "suspensa",
            "suspenso",
        )
    ):
        return "DISRUPTION"
    if any(k in text for k in ("horário", "horario", "cais de embarque", "frequência", "frequencia")):
        return "SCHEDULE"
    return "INFO"


_LINE_LIST_RE = re.compile(
    r"(?:\b[Ll]inhas?\b|\bL(?=\d))\s*([0-9A-Za-z]+(?:\s*(?:,|;|/|\be\b)\s*(?:L)?[0-9A-Za-z]+)*)"
)
_NAMED_LINE_RE = re.compile(r"\b[Ll]inha\s+(Azul|Verde|Vermelha|Amarela|Bot[âa]nico)\b")
_LINE_TOKEN_RE = re.compile(r"^(?:L)?(\d{1,3}[A-Z]{0,2})$", re.IGNORECASE)


def extract_affected_lines(title: str, summary: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    text = f"{title} {summary}"

    for match in _NAMED_LINE_RE.finditer(text):
        token = f"Linha {match.group(1).capitalize()}"
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            found.append(token)

    for match in _LINE_LIST_RE.finditer(text):
        group = match.group(1)
        for part in re.split(r"\s*(?:,|;|/|\be\b)\s*", group):
            m_tok = _LINE_TOKEN_RE.match(part.strip())
            if m_tok:
                code = m_tok.group(1).upper()
                if code not in seen:
                    seen.add(code)
                    found.append(code)
    return found


def parse_wp_gmt_timestamp(raw: str) -> dt.datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def truncate_summary(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return (cut or text[: limit - 1]) + "…"


def is_avisos_url(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return "/avisos/" in path


def parse_wp_posts(
    operator: str,
    posts: Iterable[dict],
    now: dt.datetime,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    prefer_avisos_path: bool = False,
) -> list[dict]:
    cutoff = now - dt.timedelta(days=max_age_days)
    alerts: list[dict] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        post_id = post.get("id")
        url = validate_official_url(str(post.get("link") or ""))
        published = parse_wp_gmt_timestamp(str(post.get("date_gmt") or ""))
        modified = parse_wp_gmt_timestamp(str(post.get("modified_gmt") or ""))
        effective_ts = max(published, modified) if (published and modified) else (published or modified)
        title_raw = (post.get("title") or {}).get("rendered", "")
        excerpt_raw = (post.get("excerpt") or {}).get("rendered", "")
        title = strip_html(str(title_raw))
        summary = truncate_summary(strip_html(str(excerpt_raw)))
        if not post_id or not url or not effective_ts or not title:
            continue
        if effective_ts < cutoff:
            continue
        category = classify_category(title, summary)
        lines = extract_affected_lines(title, summary)
        alerts.append(
            {
                "id": f"{operator.lower()}-{post_id}",
                "operator": operator,
                "category": category,
                "title": title,
                "summary": summary,
                "url": url,
                "publishedAt": effective_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "affectedLines": lines,
                "active": True,
            }
        )
    if prefer_avisos_path:
        avisos_only = [item for item in alerts if is_avisos_url(item["url"])]
        if avisos_only:
            return avisos_only
    return alerts


def fetch_json_object(url: str, timeout: int = FETCH_TIMEOUT_SECONDS, retries: int = FETCH_RETRIES) -> object:
    last_exc: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Accept-Language": "pt-PT,pt;q=0.9",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(512 * 1024).decode("utf-8", errors="replace")
            return json.loads(raw)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(attempt * 2)
    assert last_exc is not None
    raise last_exc


def fetch_json(url: str, timeout: int = FETCH_TIMEOUT_SECONDS, retries: int = FETCH_RETRIES) -> list[dict]:
    data = fetch_json_object(url, timeout=timeout, retries=retries)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array from {url}")
    return data


def load_fallback_alerts_by_operator(fallback_url: str | None) -> dict[str, list[dict]]:
    if not fallback_url:
        return {}
    try:
        data = fetch_json_object(fallback_url, timeout=15, retries=2)
    except Exception as exc:
        print(f"[warn] Could not load fallback feed {fallback_url}: {exc}", file=sys.stderr)
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("alerts"), list):
        return {}
    grouped: dict[str, list[dict]] = {}
    for item in data["alerts"]:
        if not isinstance(item, dict):
            continue
        op = str(item.get("operator") or "").upper()
        url = validate_official_url(str(item.get("url") or ""))
        if not op or not url or not item.get("id") or not item.get("title"):
            continue
        grouped.setdefault(op, []).append(item)
    return grouped


def build_feed(
    now: dt.datetime | None = None,
    max_age_days: int | None = None,
    fallback_url: str | None = DEFAULT_FALLBACK_URL,
) -> dict:
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    collected: list[dict] = []
    seen_ids: set[str] = set()
    failed_operators: list[str] = []

    for operator, url, default_days, prefer_avisos_path in SOURCES:
        effective_days = max_age_days if max_age_days is not None else default_days
        try:
            posts = fetch_json(url)
            for item in parse_wp_posts(
                operator,
                posts,
                current,
                max_age_days=effective_days,
                prefer_avisos_path=prefer_avisos_path,
            ):
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    collected.append(item)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            print(f"[warn] Failed to fetch {operator}: {exc}", file=sys.stderr)
            failed_operators.append(operator)

    if failed_operators and fallback_url:
        fallback_by_op = load_fallback_alerts_by_operator(fallback_url)
        for operator in failed_operators:
            preserved = fallback_by_op.get(operator, [])
            if preserved:
                print(
                    f"[info] Preserved {len(preserved)} previous {operator} alerts from fallback feed",
                    file=sys.stderr,
                )
            for item in preserved:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    collected.append(item)

    collected.sort(key=lambda a: (a["publishedAt"], a["id"]), reverse=True)
    return {
        "version": 1,
        "generatedAt": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alerts": collected[:MAX_ALERTS],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate avisos.json for Mobilidade Coimbra")
    parser.add_argument("--output", type=Path, required=True, help="Output JSON path")
    parser.add_argument("--max-age-days", type=int, default=None)
    parser.add_argument("--fallback-url", type=str, default=DEFAULT_FALLBACK_URL)
    args = parser.parse_args(argv)

    feed = build_feed(max_age_days=args.max_age_days, fallback_url=args.fallback_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(feed, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {len(feed['alerts'])} alerts to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
