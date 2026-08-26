#!/usr/bin/env python3
"""
Dashboard RSS kiểu RSS.app — trang HTML tĩnh để "overview" nhanh.

Dùng lại đúng pipeline của digest.py (đọc feeds.yaml -> collect) rồi dựng một
trang dashboard.html (kèm manifest + service worker để cài như app) có:
  - Lưới thẻ 2 cột, ảnh thumbnail, viền màu theo chủ đề, avatar nguồn
  - Tab ngôn ngữ (Tất cả / EN / JP) + chip chủ đề + lọc theo nguồn
  - Tìm kiếm tức thì  ·  bấm "/" để tìm, Esc để xoá
  - Sáng/Tối, mật độ Thoáng/Gọn, sắp xếp Mới nhất/Theo nguồn, xem Danh sách/Theo chủ đề
  - Đánh dấu "MỚI" so với lần xem trước, lưu bài (★) — nhớ bằng localStorage
  - Có thể "Add to Home Screen" (PWA), tự làm mới mỗi giờ

    python dashboard.py                 # tạo dashboard.html (48h gần nhất)
    python dashboard.py --hours 72      # mở rộng cửa sổ thời gian
    python dashboard.py --open          # tạo xong mở luôn trên trình duyệt
    python dashboard.py --out public/index.html   # dùng cho GitHub Pages

KHÔNG gửi mail, KHÔNG ghi state — chỉ tạo file HTML tĩnh để xem.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import logging
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Dùng lại nguyên si logic của digest.py để không lặp code.
from digest import collect, load_config

LOG = logging.getLogger("dashboard")
ROOT = Path(__file__).resolve().parent

LANG_LABEL = {"en": "EN", "ja": "JP"}
TOPIC_PALETTE = ["#8b5cf6", "#10b981", "#3b82f6", "#f59e0b",
                 "#ec4899", "#06b6d4", "#ef4444", "#84cc16"]
AVATAR_PALETTE = ["#7c6cf0", "#0ea5a5", "#e0803c", "#d95a8a",
                  "#4f86e6", "#59a14f", "#c1483f", "#8a6d3b"]

# Icon PWA — một SVG đơn giản, không cần file ảnh ngoài.
ICON_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'>"
            "<rect width='512' height='512' rx='96' fill='#0d0f14'/>"
            "<g fill='#f39c12'><circle cx='168' cy='344' r='40'/>"
            "<path d='M128 232a152 152 0 0 1 152 152h-56a96 96 0 0 0-96-96z'/>"
            "<path d='M128 144a240 240 0 0 1 240 240h-56a184 184 0 0 0-184-184z'/></g></svg>")


def rel_time(published: datetime | None, tz: ZoneInfo) -> str:
    if not published:
        return "—"
    diff = datetime.now(timezone.utc) - published
    mins = int(diff.total_seconds() // 60)
    if mins < 1:
        return "vừa xong"
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days < 7:
        return f"{days}d"
    return published.astimezone(tz).strftime("%m/%d")


def stable_color(name: str, palette: list[str]) -> str:
    h = int(hashlib.sha1(name.encode("utf-8")).hexdigest(), 16)
    return palette[h % len(palette)]


def build_items(topics: list[dict], topic_color: dict, tz: ZoneInfo) -> list[dict]:
    items: list[dict] = []
    for topic in topics:
        for it in topic["items"]:
            published = it["published"]
            items.append({
                "id": hashlib.sha1(it["link"].encode("utf-8")).hexdigest()[:12],
                "title": it["title"], "link": it["link"], "summary": it["summary"],
                "source": it["source"], "lang": it["lang"], "topic": topic["name"],
                "emoji": topic.get("emoji", "•"), "color": topic_color[topic["name"]],
                "image": it.get("image", ""),
                "when": rel_time(published, tz),
                "ts": int(published.timestamp()) if published else 0,
            })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def render_dashboard(topics: list[dict], tz: ZoneInfo, hours: int,
                     failures: list[str]) -> str:
    topic_color = {t["name"]: TOPIC_PALETTE[i % len(TOPIC_PALETTE)]
                   for i, t in enumerate(topics)}
    items = build_items(topics, topic_color, tz)
    today = datetime.now(tz)
    total = len(items)
    en_count = sum(1 for i in items if i["lang"] == "en")
    ja_count = sum(1 for i in items if i["lang"] == "ja")

    topic_meta = [{"name": t["name"], "emoji": t.get("emoji", "•"),
                   "count": len(t["items"]), "color": topic_color[t["name"]]}
                  for t in topics if t["items"]]
    topics_json = json.dumps(
        [{"name": tm["name"], "emoji": tm["emoji"], "color": tm["color"]} for tm in topic_meta],
        ensure_ascii=False)

    # Chip chủ đề
    topic_chips = ['<button class="chip active" data-topic="all">Tất cả '
                   f'<span class="n">{total}</span></button>']
    for tm in topic_meta:
        topic_chips.append(
            f'<button class="chip" data-topic="{html_lib.escape(tm["name"], quote=True)}" '
            f'style="--c:{tm["color"]}">{tm["emoji"]} {html_lib.escape(tm["name"])} '
            f'<span class="n">{tm["count"]}</span></button>')

    # Dropdown nguồn
    sources = sorted({i["source"] for i in items})
    src_opts = ['<option value="all">Tất cả nguồn</option>'] + [
        f'<option value="{html_lib.escape(s, quote=True)}">{html_lib.escape(s)}</option>'
        for s in sources]

    # Thẻ bài viết
    cards = []
    for it in items:
        badge = LANG_LABEL.get(it["lang"], "··")
        summary = (f'<p class="summary">{html_lib.escape(it["summary"])}</p>'
                   if it["summary"] else "")
        search_blob = html_lib.escape(
            f'{it["title"]} {it["summary"]} {it["source"]}'.lower(), quote=True)
        avatar_bg = stable_color(it["source"], AVATAR_PALETTE)
        initial = html_lib.escape(it["source"][:1].upper())
        thumb = (f'<img class="thumb" src="{html_lib.escape(it["image"], quote=True)}" '
                 f'alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">'
                 if it["image"] else "")
        cards.append(f"""      <article class="card" data-id="{it['id']}" data-lang="{it['lang']}"
        data-topic="{html_lib.escape(it['topic'], quote=True)}"
        data-source="{html_lib.escape(it['source'], quote=True)}"
        data-ts="{it['ts']}" data-search="{search_blob}" style="--c:{it['color']}">
        <div class="body">
          <div class="meta">
            <span class="avatar" style="background:{avatar_bg}">{initial}</span>
            <span class="src">{html_lib.escape(it['source'])}</span>
            <span class="badge lang-{it['lang']}">{badge}</span>
            <span class="when">{it['when']}</span>
            <button class="star" title="Lưu bài (★)" aria-label="Lưu">☆</button>
          </div>
          <a class="title" href="{html_lib.escape(it['link'], quote=True)}" target="_blank" rel="noopener">
            {html_lib.escape(it['title'])}</a>
          {summary}
        </div>
        {thumb}
      </article>""")

    warn = (f'<div class="warn">⚠ Không lấy được: '
            f'{html_lib.escape(", ".join(failures))}</div>' if failures else "")

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="3600">
<meta name="theme-color" content="#0d0f14">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg">
<title>Tech Feed — {today.strftime('%Y/%m/%d')}</title>
<style>
  :root {{
    --bg:#0d0f14; --panel:#161a22; --panel2:#1c212b; --border:#262c38;
    --text:#e6e9ef; --muted:#8b94a3; --accent:#3b7ded; --en:#2f6fd0; --ja:#d94f4f;
    --header:rgba(13,15,20,.94); --star:#f5c451;
  }}
  [data-theme="light"] {{
    --bg:#f4f5f7; --panel:#ffffff; --panel2:#eef1f5; --border:#dfe3ea;
    --text:#1a2230; --muted:#5a6675; --accent:#2f6fd0; --header:rgba(244,245,247,.94);
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family:-apple-system,'Segoe UI','Hiragino Sans','Noto Sans JP',Roboto,Arial,sans-serif;
    -webkit-font-smoothing:antialiased; }}
  header {{ position:sticky; top:0; z-index:10; background:var(--header);
    backdrop-filter:blur(8px); border-bottom:1px solid var(--border); padding:14px 22px 12px; }}
  .top, .bar {{ max-width:1140px; margin:0 auto; }}
  .top {{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; }}
  .logo {{ width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#f39c12,#e67e22);
    display:flex;align-items:center;justify-content:center;font-size:17px; }}
  h1 {{ font-size:18px; margin:0; font-weight:700; }}
  .sub {{ color:var(--muted); font-size:13px; margin-left:auto; }}
  .search {{ margin-top:11px; width:100%; background:var(--panel); border:1px solid var(--border);
    color:var(--text); padding:10px 14px; border-radius:9px; font-size:14px; outline:none; }}
  .search:focus {{ border-color:var(--accent); }}
  .tabs {{ display:flex; gap:8px; margin-top:11px; flex-wrap:wrap; }}
  .tab {{ background:var(--panel); border:1px solid var(--border); color:var(--muted);
    padding:7px 15px; border-radius:8px; font-size:13.5px; font-weight:600; cursor:pointer; }}
  .tab.active {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
  .tab .n {{ opacity:.7; font-weight:500; margin-left:4px; }}
  .tab.saved.active {{ background:var(--star); border-color:var(--star); color:#3a2e05; }}
  .chips {{ display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }}
  .chip {{ background:transparent; border:1px solid var(--border); color:var(--muted);
    padding:5px 12px; border-radius:20px; font-size:12.5px; cursor:pointer; }}
  .chip.active {{ background:var(--panel2); border-color:var(--c,var(--accent));
    color:var(--text); box-shadow:inset 3px 0 0 var(--c,var(--accent)); }}
  .chip .n {{ opacity:.6; margin-left:3px; }}
  .controls {{ display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; align-items:center; }}
  .controls select, .ctrl-btn {{ background:var(--panel); border:1px solid var(--border);
    color:var(--text); padding:6px 10px; border-radius:8px; font-size:12.5px; cursor:pointer; }}
  .ctrl-btn:hover {{ border-color:var(--accent); }}
  .spacer {{ flex:1; }}
  main {{ max-width:1140px; margin:0 auto; padding:18px 16px 70px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,360px),1fr)); gap:13px; }}
  .group-header {{ grid-column:1/-1; font-size:14px; font-weight:700; color:var(--text);
    padding:6px 2px; border-bottom:2px solid var(--border); margin-top:4px;
    box-shadow:inset 4px 0 0 var(--c); padding-left:12px; }}
  .group-header .n {{ color:var(--muted); font-weight:500; font-size:12px; }}
  .card {{ display:flex; gap:12px; background:var(--panel); border:1px solid var(--border);
    border-left:3px solid var(--c,var(--accent)); border-radius:12px; padding:13px 15px;
    transition:border-color .15s, transform .06s; overflow:hidden; }}
  .card:hover {{ border-color:#33465f; transform:translateY(-1px); }}
  .card.is-new {{ box-shadow:0 0 0 1px var(--accent) inset; }}
  .body {{ flex:1; min-width:0; }}
  .meta {{ display:flex; align-items:center; gap:7px; font-size:12px; color:var(--muted); margin-bottom:7px; }}
  .avatar {{ width:18px;height:18px;border-radius:5px;color:#fff;font-size:11px;font-weight:700;
    display:inline-flex;align-items:center;justify-content:center;flex:none; }}
  .src {{ color:#9fb3c8; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  [data-theme="light"] .src {{ color:#556; }}
  .badge {{ font-size:9.5px; font-weight:700; color:#fff; padding:1px 5px; border-radius:4px; flex:none; }}
  .lang-en {{ background:var(--en); }} .lang-ja {{ background:var(--ja); }}
  .new-badge {{ font-size:9px; font-weight:800; letter-spacing:.4px; color:#fff;
    background:var(--accent); padding:1px 5px; border-radius:4px; flex:none; }}
  .when {{ margin-left:auto; color:#6b7686; flex:none; }}
  .star {{ background:none; border:none; color:var(--muted); font-size:15px; cursor:pointer;
    padding:0 2px; flex:none; line-height:1; }}
  .star.on {{ color:var(--star); }}
  .title {{ display:block; color:var(--text); font-size:15px; font-weight:650; line-height:1.4;
    text-decoration:none; }}
  .title:hover {{ color:#7fb0ff; }}
  .summary {{ color:var(--muted); font-size:13px; line-height:1.5; margin:6px 0 0;
    display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }}
  .thumb {{ width:88px; height:88px; object-fit:cover; border-radius:9px; flex:none; background:var(--panel2); }}
  /* Mật độ gọn */
  body.compact .card {{ padding:9px 12px; }}
  body.compact .summary {{ display:none; }}
  body.compact .thumb {{ width:56px; height:56px; }}
  body.compact .grid {{ gap:8px; }}
  .empty {{ grid-column:1/-1; text-align:center; color:var(--muted); padding:60px 0; display:none; }}
  .warn {{ background:#2a2410; border:1px solid #4a4020; color:#d9b64f; font-size:12.5px;
    padding:10px 14px; border-radius:9px; margin-bottom:14px; }}
  footer {{ text-align:center; color:#5a6373; font-size:12px; padding:24px; }}
  footer code {{ background:var(--panel2); padding:2px 6px; border-radius:4px; }}
  @media (max-width:520px) {{ .thumb {{ width:66px; height:66px; }} .sub {{ display:none; }} }}
</style>
</head>
<body>
<header>
  <div class="top">
    <div class="logo">📡</div>
    <h1>Tech Feed</h1>
    <span class="sub">Cập nhật {today.strftime('%Y/%m/%d %H:%M')} · {total} tin / {hours}h · tự làm mới mỗi 5h</span>
  </div>
  <div class="bar">
    <input class="search" id="search" type="text" placeholder="🔍 Tìm tiêu đề, mô tả, nguồn…  (bấm /)" autocomplete="off">
    <div class="tabs">
      <button class="tab active" data-lang="all">Tất cả <span class="n">{total}</span></button>
      <button class="tab" data-lang="en">🇬🇧 EN <span class="n">{en_count}</span></button>
      <button class="tab" data-lang="ja">🇯🇵 JP <span class="n">{ja_count}</span></button>
      <button class="tab saved" id="savedTab">★ Đã lưu <span class="n" id="savedN">0</span></button>
    </div>
    <div class="chips">{''.join(topic_chips)}</div>
    <div class="controls">
      <select id="sourceSel" title="Lọc theo nguồn">{''.join(src_opts)}</select>
      <select id="sortSel" title="Sắp xếp">
        <option value="new">↓ Mới nhất</option>
        <option value="source">Theo nguồn</option>
      </select>
      <select id="viewSel" title="Kiểu xem">
        <option value="list">Danh sách</option>
        <option value="topic">Theo chủ đề</option>
      </select>
      <div class="spacer"></div>
      <button class="ctrl-btn" id="densityBtn" title="Mật độ">↕ Thoáng</button>
      <button class="ctrl-btn" id="themeBtn" title="Sáng/Tối">🌙</button>
    </div>
  </div>
</header>
<main>
  {warn}
  <div class="grid" id="grid">
{chr(10).join(cards)}
    <div class="empty" id="empty">Không có tin nào khớp bộ lọc.</div>
  </div>
</main>
<footer>Tự động tạo từ RSS · nguồn cấu hình trong <code>feeds.yaml</code></footer>
<script>
  const TOPICS = {topics_json};
  const LS = {{
    get:(k,d)=>{{ try{{ return JSON.parse(localStorage.getItem(k)) ?? d; }}catch{{ return d; }} }},
    set:(k,v)=>{{ try{{ localStorage.setItem(k, JSON.stringify(v)); }}catch{{}} }},
  }};

  // ---- Giao diện: chủ đề sáng/tối, mật độ ----
  const root = document.documentElement, body = document.body;
  const themeStored = LS.get('theme', null);
  if (themeStored) root.dataset.theme = themeStored;
  else if (matchMedia('(prefers-color-scheme: light)').matches) root.dataset.theme = 'light';
  const themeBtn = document.getElementById('themeBtn');
  const syncTheme = () => themeBtn.textContent = root.dataset.theme === 'light' ? '☀️' : '🌙';
  syncTheme();
  themeBtn.onclick = () => {{ root.dataset.theme = root.dataset.theme === 'light' ? '' : 'light';
    LS.set('theme', root.dataset.theme || 'dark'); syncTheme(); }};

  const densityBtn = document.getElementById('densityBtn');
  if (LS.get('density','') === 'compact') body.classList.add('compact');
  const syncDensity = () => densityBtn.textContent = body.classList.contains('compact') ? '≡ Gọn' : '↕ Thoáng';
  syncDensity();
  densityBtn.onclick = () => {{ body.classList.toggle('compact');
    LS.set('density', body.classList.contains('compact') ? 'compact' : 'comfortable'); syncDensity(); }};

  // ---- Bài đã lưu (★) ----
  const saved = new Set(LS.get('saved', []));
  const savedN = document.getElementById('savedN');
  const refreshSavedN = () => savedN.textContent = saved.size;
  refreshSavedN();

  // ---- Đánh dấu "MỚI" so với lần xem trước ----
  const lastVisit = LS.get('lastVisit', null);

  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty');
  const search = document.getElementById('search');
  const cardEls = [...document.querySelectorAll('.card')];

  cardEls.forEach(c => {{
    const id = c.dataset.id;
    if (saved.has(id)) {{ c.querySelector('.star').classList.add('on');
      c.querySelector('.star').textContent = '★'; }}
    if (lastVisit && Number(c.dataset.ts) * 1000 > lastVisit) {{
      c.classList.add('is-new');
      const b = document.createElement('span'); b.className = 'new-badge'; b.textContent = 'MỚI';
      c.querySelector('.meta').prepend(b);
    }}
    c.querySelector('.star').onclick = () => {{
      if (saved.has(id)) {{ saved.delete(id); c.querySelector('.star').classList.remove('on');
        c.querySelector('.star').textContent = '☆'; }}
      else {{ saved.add(id); c.querySelector('.star').classList.add('on');
        c.querySelector('.star').textContent = '★'; }}
      LS.set('saved', [...saved]); refreshSavedN(); if (state.savedOnly) render();
    }};
  }});
  LS.set('lastVisit', Date.now());  // đặt mốc cho lần xem sau

  // ---- Trạng thái lọc + sắp xếp ----
  const state = {{ lang:'all', topic:'all', source:'all', q:'', sort:'new', view:'list', savedOnly:false }};

  const visible = c =>
       (state.lang==='all'  || c.dataset.lang === state.lang)
    && (state.topic==='all' || c.dataset.topic === state.topic)
    && (state.source==='all'|| c.dataset.source === state.source)
    && (!state.q || c.dataset.search.includes(state.q))
    && (!state.savedOnly || saved.has(c.dataset.id));

  function render() {{
    const vis = cardEls.filter(visible);
    vis.sort(state.sort === 'source'
      ? (a,b) => a.dataset.source.localeCompare(b.dataset.source) || b.dataset.ts - a.dataset.ts
      : (a,b) => b.dataset.ts - a.dataset.ts);

    grid.querySelectorAll('.group-header').forEach(h => h.remove());
    cardEls.forEach(c => c.remove());

    if (state.view === 'topic') {{
      const groups = {{}};
      vis.forEach(c => (groups[c.dataset.topic] ||= []).push(c));
      TOPICS.forEach(t => {{
        const g = groups[t.name]; if (!g || !g.length) return;
        const h = document.createElement('div');
        h.className = 'group-header'; h.style.setProperty('--c', t.color);
        h.innerHTML = t.emoji + ' ' + t.name + ' <span class="n">(' + g.length + ')</span>';
        grid.appendChild(h); g.forEach(c => grid.appendChild(c));
      }});
    }} else {{
      vis.forEach(c => grid.appendChild(c));
    }}
    empty.style.display = vis.length ? 'none' : 'block';
    grid.appendChild(empty);
  }}

  // ---- Nối sự kiện ----
  document.querySelectorAll('.tab[data-lang]').forEach(t => t.onclick = () => {{
    document.querySelectorAll('.tab[data-lang]').forEach(x => x.classList.remove('active'));
    t.classList.add('active'); state.lang = t.dataset.lang; render();
  }});
  const savedTab = document.getElementById('savedTab');
  savedTab.onclick = () => {{ state.savedOnly = !state.savedOnly;
    savedTab.classList.toggle('active', state.savedOnly); render(); }};
  document.querySelectorAll('.chip').forEach(c => c.onclick = () => {{
    document.querySelectorAll('.chip').forEach(x => x.classList.remove('active'));
    c.classList.add('active'); state.topic = c.dataset.topic; render();
  }});
  document.getElementById('sourceSel').onchange = e => {{ state.source = e.target.value; render(); }};
  document.getElementById('sortSel').onchange = e => {{ state.sort = e.target.value; render(); }};
  document.getElementById('viewSel').onchange = e => {{ state.view = e.target.value; render(); }};
  search.addEventListener('input', e => {{ state.q = e.target.value.trim().toLowerCase(); render(); }});
  document.addEventListener('keydown', e => {{
    if (e.key === '/' && document.activeElement !== search) {{ e.preventDefault(); search.focus(); }}
    if (e.key === 'Escape' && document.activeElement === search) {{
      search.value = ''; state.q = ''; render(); search.blur(); }}
  }});

  if ('serviceWorker' in navigator && location.protocol === 'https:')
    navigator.serviceWorker.register('sw.js').catch(() => {{}});
</script>
</body>
</html>"""


def write_pwa_assets(out_dir: Path) -> None:
    """Ghi manifest + service worker + icon cạnh trang để cài như app (PWA)."""
    manifest = {
        "name": "Tech Feed", "short_name": "Tech Feed",
        "start_url": ".", "scope": ".", "display": "standalone",
        "background_color": "#0d0f14", "theme_color": "#0d0f14",
        "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }
    (out_dir / "manifest.webmanifest").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "icon.svg").write_text(ICON_SVG, encoding="utf-8")
    # Service worker tối giản: cache-first cho trang, để mở lại được khi offline.
    sw = (
        "const C='techfeed-v1';\n"
        "self.addEventListener('install',e=>{self.skipWaiting();"
        "e.waitUntil(caches.open(C).then(c=>c.addAll(['.','index.html','icon.svg'])))});\n"
        "self.addEventListener('activate',e=>e.waitUntil("
        "caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k))))));\n"
        "self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;"
        "e.respondWith(fetch(e.request).then(r=>{const cp=r.clone();"
        "caches.open(C).then(c=>c.put(e.request,cp));return r})"
        ".catch(()=>caches.match(e.request)))});\n"
    )
    (out_dir / "sw.js").write_text(sw, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Tạo dashboard RSS tĩnh kiểu RSS.app.")
    ap.add_argument("--config", default=str(ROOT / "feeds.yaml"))
    ap.add_argument("--hours", type=int, default=48, help="Cửa sổ thời gian (mặc định 48h)")
    ap.add_argument("--out", default=str(ROOT / "dashboard.html"))
    ap.add_argument("--open", action="store_true", help="Mở file sau khi tạo")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    cfg = load_config(Path(args.config))
    tz = ZoneInfo(cfg["settings"].get("timezone", "Asia/Tokyo"))
    cfg["settings"]["max_items_per_feed"] = max(int(cfg["settings"].get("max_items_per_feed", 6)), 12)
    cfg["settings"]["max_items_per_topic"] = max(int(cfg["settings"].get("max_items_per_topic", 15)), 60)

    started = time.time()
    topics, _state, failures = collect(cfg, args.hours, use_state=False)
    total = sum(len(t["items"]) for t in topics)
    LOG.info("Thu được %d tin (%.1fs).", total, time.time() - started)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard(topics, tz, args.hours, failures), encoding="utf-8")
    write_pwa_assets(out.parent)
    LOG.info("Đã tạo %s (+ manifest.webmanifest, sw.js, icon.svg)", out)

    if args.open:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
