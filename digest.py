#!/usr/bin/env python3
"""
Daily RSS digest -> email.

Đọc danh sách nguồn trong feeds.yaml, lấy các bài đăng trong N giờ gần nhất,
khử trùng lặp, gom theo chủ đề rồi gửi một email HTML duy nhất.

Thông tin đăng nhập SMTP KHÔNG được ghi trong code — chỉ đọc từ biến môi trường:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM, MAIL_TO

Ví dụ:
    python digest.py                 # lấy tin + gửi mail
    python digest.py --dry-run       # chỉ ghi ra preview.html, KHÔNG gửi mail
    python digest.py --hours 48      # mở rộng cửa sổ thời gian
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import html as html_lib
import json
import logging
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import feedparser
except ImportError:  # pragma: no cover
    sys.exit("Thiếu thư viện. Chạy: pip install -r requirements.txt")

import yaml

LOG = logging.getLogger("digest")

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / ".state" / "seen.json"
STATE_TTL_DAYS = 14

USER_AGENT = "Mozilla/5.0 (compatible; DailyRSSDigest/1.0; RSS reader)"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)

LANG_BADGE = {"ja": ("JP", "#d94f4f"), "en": ("EN", "#2f6fd0")}


# ---------------------------------------------------------------- helpers

def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg.setdefault("settings", {})
    cfg.setdefault("filters", {"include": [], "exclude": []})
    cfg.setdefault("topics", [])
    return cfg


def entry_datetime(entry) -> datetime | None:
    """feedparser trả *_parsed dưới dạng struct_time theo giờ UTC."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
            except (ValueError, OverflowError, TypeError):
                continue
    return None


def clean_text(raw: str, limit: int | None = None) -> str:
    txt = html_lib.unescape(TAG_RE.sub(" ", raw or ""))
    txt = WS_RE.sub(" ", txt).strip()
    if limit and len(txt) > limit:
        txt = txt[:limit].rstrip() + "…"
    return txt


def entry_image(entry) -> str:
    """Tìm ảnh đại diện của bài: media:thumbnail/content, enclosure, hoặc <img> đầu tiên."""
    for key in ("media_thumbnail", "media_content"):
        media = entry.get(key)
        if media and isinstance(media, list):
            url = media[0].get("url")
            if url:
                return url
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image"):
            if link.get("href"):
                return link["href"]
    for key in ("content", "summary", "description"):
        val = entry.get(key)
        if isinstance(val, list) and val:
            val = val[0].get("value", "")
        if isinstance(val, str):
            m = IMG_SRC_RE.search(val)
            if m:
                return m.group(1)
    return ""


def canonical_link(link: str) -> str:
    """Bỏ tham số theo dõi để hai bản sao cùng một bài trùng khớp nhau."""
    link = (link or "").strip()
    link = re.sub(r"[?&](utm_[^=]+|fbclid|gclid|ref|rss)=[^&]*", "", link)
    return link.rstrip("?&").rstrip("/")


def item_keys(link: str, title: str) -> tuple[str, str]:
    lk = hashlib.sha1(canonical_link(link).encode("utf-8")).hexdigest()
    tk = hashlib.sha1(WS_RE.sub(" ", (title or "").strip().lower()).encode("utf-8")).hexdigest()
    return lk, tk


def matches_filters(text: str, include: list[str], exclude: list[str]) -> bool:
    low = text.lower()
    if any(word.lower() in low for word in exclude if word):
        return False
    if include:
        return any(word.lower() in low for word in include if word)
    return True


# ---------------------------------------------------------------- state

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        LOG.warning("Không đọc được state (%s), bỏ qua.", exc)
        return {}


def save_state(state: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STATE_TTL_DAYS)).isoformat()
    pruned = {k: v for k, v in state.items() if v >= cutoff}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(pruned, ensure_ascii=False), encoding="utf-8")
    LOG.info("Đã lưu state: %d mục.", len(pruned))


# ---------------------------------------------------------------- fetching

def fetch_feed(feed_cfg: dict, timeout: int) -> list:
    url = feed_cfg["url"]
    try:
        parsed = feedparser.parse(url, agent=USER_AGENT, request_headers={"Cache-Control": "no-cache"})
    except Exception as exc:  # noqa: BLE001 - một nguồn hỏng không được làm chết cả job
        LOG.warning("[LỖI] %s — %s", feed_cfg["name"], exc)
        return []

    status = getattr(parsed, "status", None)
    if status and status >= 400:
        LOG.warning("[HTTP %s] %s — %s", status, feed_cfg["name"], url)
        return []
    if parsed.bozo and not parsed.entries:
        LOG.warning("[PARSE] %s — %s", feed_cfg["name"], parsed.bozo_exception)
        return []

    LOG.info("[OK] %-32s %3d mục", feed_cfg["name"], len(parsed.entries))
    return parsed.entries


def collect(cfg: dict, hours: int, use_state: bool) -> tuple[list[dict], dict, list[str]]:
    settings = cfg["settings"]
    filters = cfg.get("filters") or {}
    include = filters.get("include") or []
    exclude = filters.get("exclude") or []
    timeout = int(settings.get("fetch_timeout", 20))
    summary_len = int(settings.get("summary_length", 180))
    per_feed = int(settings.get("max_items_per_feed", 6))
    per_topic = int(settings.get("max_items_per_topic", 15))

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    state = load_state() if use_state else {}
    seen: set[str] = set(state.keys())
    now_iso = datetime.now(timezone.utc).isoformat()

    topics_out: list[dict] = []
    failures: list[str] = []

    for topic in cfg["topics"]:
        items: list[dict] = []
        for feed_cfg in topic.get("feeds", []):
            if feed_cfg.get("enabled") is False:
                continue

            entries = fetch_feed(feed_cfg, timeout)
            if not entries:
                failures.append(feed_cfg["name"])
                continue

            kept = 0
            for entry in entries:
                if kept >= per_feed:
                    break

                published = entry_datetime(entry)
                # Nguồn không có timestamp: vẫn nhận, dựa vào state để khỏi lặp
                if published and published < cutoff:
                    continue

                title = clean_text(entry.get("title", ""))
                link = entry.get("link", "")
                if not title or not link:
                    continue

                summary = clean_text(
                    entry.get("summary", "") or entry.get("description", ""), summary_len
                )

                if not matches_filters(f"{title} {summary}", include, exclude):
                    continue

                link_key, title_key = item_keys(link, title)
                if link_key in seen or title_key in seen:
                    continue
                seen.add(link_key)
                seen.add(title_key)
                state[link_key] = now_iso
                state[title_key] = now_iso

                items.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "source": feed_cfg["name"],
                    "lang": feed_cfg.get("lang", "en"),
                    "published": published,
                    "image": entry_image(entry),
                })
                kept += 1

        items.sort(key=lambda it: it["published"] or datetime.min.replace(tzinfo=timezone.utc),
                   reverse=True)
        topics_out.append({
            "name": topic["name"],
            "emoji": topic.get("emoji", "•"),
            "items": items[:per_topic],
        })

    return topics_out, state, failures


# ---------------------------------------------------------------- rendering

def render_html(topics: list[dict], tz: ZoneInfo, hours: int, failures: list[str]) -> str:
    today = datetime.now(tz)
    total = sum(len(t["items"]) for t in topics)

    parts = [f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f5f7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:20px 10px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#ffffff;border-radius:10px;overflow:hidden;font-family:-apple-system,'Segoe UI','Hiragino Sans','Noto Sans JP',Roboto,Arial,sans-serif;">
  <tr><td style="background:#12263f;padding:22px 26px;">
    <div style="color:#ffffff;font-size:19px;font-weight:700;">Tech Digest</div>
    <div style="color:#9fb3c8;font-size:13px;margin-top:5px;">
      {today.strftime('%Y/%m/%d (%a)')} &nbsp;·&nbsp; {total} tin trong {hours} giờ qua
    </div>
  </td></tr>
"""]

    if total == 0:
        parts.append("""  <tr><td style="padding:32px 26px;color:#5a6b7d;font-size:14px;">
    Không có tin mới trong khoảng thời gian này.
  </td></tr>""")

    for topic in topics:
        if not topic["items"]:
            continue
        parts.append(f"""  <tr><td style="padding:22px 26px 8px;">
    <div style="font-size:15px;font-weight:700;color:#12263f;border-bottom:2px solid #e4e8ee;padding-bottom:7px;">
      {topic['emoji']} {html_lib.escape(topic['name'])}
      <span style="font-weight:400;color:#94a3b3;font-size:12px;">({len(topic['items'])})</span>
    </div>
  </td></tr>""")

        for item in topic["items"]:
            badge, color = LANG_BADGE.get(item["lang"], ("··", "#7b8794"))
            when = item["published"].astimezone(tz).strftime("%H:%M") if item["published"] else "—"
            summary_html = (
                f'<div style="font-size:13px;color:#5a6b7d;line-height:1.55;margin-top:5px;">'
                f'{html_lib.escape(item["summary"])}</div>'
                if item["summary"] else ""
            )
            parts.append(f"""  <tr><td style="padding:11px 26px;border-bottom:1px solid #f0f2f5;">
    <div>
      <span style="display:inline-block;background:{color};color:#fff;font-size:10px;font-weight:700;
                   padding:2px 6px;border-radius:3px;vertical-align:middle;">{badge}</span>
      <a href="{html_lib.escape(item['link'], quote=True)}"
         style="color:#12263f;font-size:14.5px;font-weight:600;text-decoration:none;line-height:1.45;">
        {html_lib.escape(item['title'])}</a>
    </div>
    {summary_html}
    <div style="font-size:11.5px;color:#94a3b3;margin-top:6px;">
      {html_lib.escape(item['source'])} &nbsp;·&nbsp; {when}
    </div>
  </td></tr>""")

    if failures:
        parts.append(f"""  <tr><td style="padding:14px 26px;background:#fff8e6;font-size:11.5px;color:#8a6d1f;">
    ⚠ Không lấy được dữ liệu từ: {html_lib.escape(', '.join(failures))}
  </td></tr>""")

    parts.append("""  <tr><td style="padding:18px 26px;background:#f8f9fb;font-size:11.5px;color:#94a3b3;">
    Tự động tạo từ RSS. Sửa nguồn tin trong <code>feeds.yaml</code>.
  </td></tr>
</table></td></tr></table></body></html>""")

    return "\n".join(parts)


def render_text(topics: list[dict], tz: ZoneInfo) -> str:
    today = datetime.now(tz)
    lines = [f"TECH DIGEST — {today.strftime('%Y/%m/%d')}", ""]
    for topic in topics:
        if not topic["items"]:
            continue
        lines.append(f"== {topic['name']} ==")
        for item in topic["items"]:
            lines.append(f"[{item['lang'].upper()}] {item['title']}")
            lines.append(f"    {item['link']}")
            lines.append(f"    — {item['source']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- sending

def send_email(subject: str, html_body: str, text_body: str) -> None:
    required = ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Thiếu biến môi trường: {', '.join(missing)}")

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    sender = os.environ.get("MAIL_FROM", user)
    recipients = [a.strip() for a in os.environ["MAIL_TO"].split(",") if a.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Tech Digest", sender))
    msg["To"] = ", ".join(recipients)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(msg)

    LOG.info("Đã gửi tới: %s", ", ".join(recipients))


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Gửi bản tin RSS hằng ngày qua email.")
    ap.add_argument("--config", default=str(ROOT / "feeds.yaml"))
    ap.add_argument("--hours", type=int, default=None, help="Ghi đè window_hours")
    ap.add_argument("--dry-run", action="store_true", help="Ghi preview.html thay vì gửi mail")
    ap.add_argument("--no-state", action="store_true", help="Bỏ qua lịch sử khử trùng lặp")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(Path(args.config))
    tz = ZoneInfo(cfg["settings"].get("timezone", "Asia/Tokyo"))
    hours = args.hours or int(cfg["settings"].get("window_hours", 24))

    started = time.time()
    topics, state, failures = collect(cfg, hours, use_state=not args.no_state)
    total = sum(len(t["items"]) for t in topics)
    LOG.info("Tổng cộng %d tin mới sau khi lọc (%.1fs).", total, time.time() - started)

    if total == 0 and not cfg["settings"].get("send_when_empty", False) and not args.dry_run:
        LOG.info("Không có tin mới — bỏ qua việc gửi mail.")
        save_state(state)
        return 0

    html_body = render_html(topics, tz, hours, failures)
    text_body = render_text(topics, tz)
    subject = f"📰 Tech Digest — {datetime.now(tz).strftime('%Y/%m/%d')} ({total} tin)"

    if args.dry_run:
        out = ROOT / "preview.html"
        out.write_text(html_body, encoding="utf-8")
        LOG.info("Dry-run: đã ghi %s (KHÔNG gửi mail, KHÔNG lưu state).", out)
        return 0

    send_email(subject, html_body, text_body)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
