#!/usr/bin/env python3
"""
Kiểm tra toàn bộ nguồn trong feeds.yaml: nguồn nào sống, nguồn nào chết.
Chạy trước khi bật lịch tự động, và định kỳ vài tháng một lần.

    python check_feeds.py
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent
AGENT = "Mozilla/5.0 (compatible; DailyRSSDigest/1.0; RSS reader)"


def check(feed: dict) -> tuple[str, str, str]:
    try:
        d = feedparser.parse(feed["url"], agent=AGENT)
    except Exception as exc:  # noqa: BLE001
        return "LỖI", feed["name"], str(exc)[:70]

    status = getattr(d, "status", 0)
    if status >= 400:
        return "CHẾT", feed["name"], f"HTTP {status}"
    if not d.entries:
        return "TRỐNG", feed["name"], f"HTTP {status}, 0 bài"

    newest = ""
    for entry in d.entries[:1]:
        st = entry.get("published_parsed") or entry.get("updated_parsed")
        if st:
            newest = datetime(*st[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
    return "OK", feed["name"], f"{len(d.entries):>3} bài, mới nhất {newest or '?'}"


def main() -> None:
    cfg = yaml.safe_load((ROOT / "feeds.yaml").read_text(encoding="utf-8"))
    feeds = [
        f for t in cfg["topics"] for f in t.get("feeds", [])
        if f.get("enabled") is not False
    ]

    print(f"Đang kiểm tra {len(feeds)} nguồn...\n")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, feeds))

    bad = 0
    for state, name, detail in results:
        mark = "✓" if state == "OK" else "✗"
        if state != "OK":
            bad += 1
        print(f" {mark} [{state:<5}] {name:<34} {detail}")

    print(f"\n{len(results) - bad}/{len(results)} nguồn hoạt động.")
    if bad:
        print("Hãy đặt enabled: false hoặc thay URL cho các nguồn lỗi trong feeds.yaml.")


if __name__ == "__main__":
    main()
