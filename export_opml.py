#!/usr/bin/env python3
"""Xuất feeds.yaml ra feeds.opml để import vào Inoreader / Feedly / NetNewsWire..."""

from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import quoteattr

import yaml

ROOT = Path(__file__).resolve().parent


def main() -> None:
    cfg = yaml.safe_load((ROOT / "feeds.yaml").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head>",
        "    <title>Tech Digest — RSS sources</title>",
        f"    <dateCreated>{now}</dateCreated>",
        "  </head>",
        "  <body>",
    ]

    count = 0
    for topic in cfg["topics"]:
        lines.append(f"    <outline text={quoteattr(topic['name'])} title={quoteattr(topic['name'])}>")
        for feed in topic.get("feeds", []):
            if feed.get("enabled") is False:
                continue
            lines.append(
                "      <outline type=\"rss\" "
                f"text={quoteattr(feed['name'])} title={quoteattr(feed['name'])} "
                f"xmlUrl={quoteattr(feed['url'])} language={quoteattr(feed.get('lang', 'en'))} />"
            )
            count += 1
        lines.append("    </outline>")

    lines += ["  </body>", "</opml>", ""]
    out = ROOT / "feeds.opml"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Đã ghi {out} — {count} nguồn.")


if __name__ == "__main__":
    main()
