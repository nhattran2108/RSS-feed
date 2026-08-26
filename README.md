# Tech Digest — bản tin RSS tự động gửi qua email mỗi ngày

Gom tin từ ~38 nguồn RSS (tiếng Nhật + tiếng Anh) theo 5 chủ đề — AI, lập trình, mobile, laptop/PC, công nghệ tổng hợp — lọc trùng lặp rồi gửi **một email HTML duy nhất** vào giờ bạn chọn. Chạy miễn phí trên GitHub Actions.

```
digest.py          Script chính: đọc RSS → lọc → dựng email → gửi SMTP
dashboard.py       Dựng trang web kiểu RSS.app để "overview" nhanh (không gửi mail)
feeds.yaml         Danh sách nguồn + cấu hình (chỉ cần sửa file này)
check_feeds.py     Kiểm tra nguồn nào còn sống
export_opml.py     Xuất feeds.opml để import vào Inoreader/Feedly
requirements.txt   feedparser (BSD-2), PyYAML (MIT) — đều không copyleft
.github/workflows/daily-digest.yml   Gửi email hằng ngày
.github/workflows/dashboard.yml      Dựng + deploy dashboard lên GitHub Pages mỗi 5h
```

---

## 0. Dashboard web kiểu RSS.app (không cần email)

```bash
pip install -r requirements.txt
python dashboard.py --open        # tạo dashboard.html + mở trên trình duyệt
python dashboard.py --hours 72    # nới cửa sổ thời gian
```

Trang tĩnh, lọc hoàn toàn ở trình duyệt (không cần server): tab **EN / JP**, chip
theo chủ đề, lọc theo nguồn, tìm kiếm tức thì (`/` để tìm), sáng/tối, mật độ
thoáng/gọn, sắp xếp mới nhất/theo nguồn, xem danh sách/theo chủ đề, đánh dấu
**MỚI** so với lần xem trước và lưu bài **★** (nhớ bằng `localStorage`). Có thể
"Add to Home Screen" như một app (PWA).

**Đưa lên mạng miễn phí (GitHub Pages):** sau khi push, vào **Settings → Pages →
Source: GitHub Actions**. Workflow `dashboard.yml` tự dựng lại và deploy **mỗi 5
giờ**. URL: `https://<user>.github.io/<repo>/`.

---

## 1. Chạy thử trên máy của bạn

```bash
pip install -r requirements.txt

python check_feeds.py            # xem nguồn nào sống/chết
python digest.py --dry-run       # tạo preview.html, KHÔNG gửi mail
```

Mở `preview.html` bằng trình duyệt để xem email sẽ trông thế nào. Nếu ít tin quá, thử `--hours 72`.

## 2. Chuẩn bị tài khoản gửi mail

**Gmail** (cách nhanh nhất): bật xác minh 2 bước → tạo **App Password** 16 ký tự tại `myaccount.google.com/apppasswords`. Dùng mật khẩu ứng dụng đó, **không dùng mật khẩu Google chính**.

| Biến | Gmail | Outlook | SendGrid |
|---|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | `smtp.office365.com` | `smtp.sendgrid.net` |
| `SMTP_PORT` | `587` | `587` | `587` |
| `SMTP_USER` | email của bạn | email của bạn | `apikey` |
| `SMTP_PASS` | App Password | mật khẩu ứng dụng | API key |
| `MAIL_TO` | nơi nhận (nhiều địa chỉ ngăn bằng dấu phẩy) | | |

Chạy thử tại máy (đặt biến tạm thời, không lưu vào file):

```bash
export SMTP_HOST=smtp.gmail.com SMTP_PORT=587
export SMTP_USER=ban@gmail.com
export SMTP_PASS='xxxx xxxx xxxx xxxx'
export MAIL_TO=ban@gmail.com
python digest.py
```

> ⚠️ Đừng bao giờ commit App Password vào Git. Script cố tình chỉ đọc từ biến môi trường để tránh việc này. Nếu lỡ commit, hãy thu hồi mật khẩu đó ngay trong tài khoản Google.

## 3. Bật chạy tự động (GitHub Actions, miễn phí)

1. Tạo repo **private** trên GitHub và push toàn bộ thư mục này lên.
2. Vào **Settings → Secrets and variables → Actions → New repository secret**, thêm 6 secret: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`, `MAIL_TO`.
3. Tab **Actions → Daily RSS Digest → Run workflow** để chạy thử ngay.
4. Xong. Từ hôm sau nó tự chạy lúc 07:00 JST.

Đổi giờ nhận trong `.github/workflows/daily-digest.yml`:

```yaml
- cron: "0 22 * * *"     # 22:00 UTC = 07:00 JST = 05:00 giờ VN
```

Cron của GitHub tính theo **UTC** và có thể trễ 5–20 phút vào giờ cao điểm. Muốn 08:00 giờ Việt Nam thì đặt `"0 1 * * *"`.

Giải pháp thay thế nếu bạn có sẵn máy chủ riêng — thêm vào crontab:

```
0 7 * * * cd /path/to/rss-daily-digest && /usr/bin/python3 digest.py >> digest.log 2>&1
```

## 4. Tuỳ chỉnh

Mọi thứ nằm trong `feeds.yaml`:

```yaml
settings:
  window_hours: 24          # chỉ lấy tin 24h gần nhất
  max_items_per_topic: 15   # trần số tin mỗi chủ đề
  timezone: Asia/Tokyo

filters:
  include: []               # để rỗng = nhận tất cả
  exclude: ["セール", "sponsored"]
```

- **Thêm nguồn**: chép một khối `- name/url/lang` vào chủ đề tương ứng.
- **Tắt tạm một nguồn**: thêm `enabled: false`.
- **Chỉ quan tâm vài từ khoá**: điền `include: ["LLM", "Rust", "iPhone"]` — khi đó chỉ tin chứa từ khoá mới được gửi.
- **Nhận 2 lần/ngày**: thêm dòng cron thứ hai và đổi `window_hours: 12`.

Chống trùng lặp hoạt động ở 2 lớp: cửa sổ thời gian, và file `.state/seen.json` ghi nhớ 14 ngày (được cache lại giữa các lần chạy GitHub Actions).

## 5. Bảo trì

RSS hay chết âm thầm — trang đổi URL hoặc bỏ hẳn RSS. Vài tháng chạy `python check_feeds.py` một lần, nguồn nào báo ✗ thì sửa URL hoặc đặt `enabled: false`. Sau khi sửa `feeds.yaml`, chạy lại `python export_opml.py` nếu bạn cũng dùng bản OPML.

## 6. Nếu bạn không muốn tự host

Import `feeds.opml` vào một trong các dịch vụ sau, chúng có sẵn tính năng gửi digest hằng ngày:

- **Inoreader** — hỗ trợ OPML + "Daily digest" qua email, bản free đủ dùng cho ~150 nguồn.
- **Feedly** + Zapier/IFTTT — linh hoạt nhưng bản free giới hạn số lần chạy.
- **Blogtrottr** — nhận từng feed một, gửi mail theo lịch; đơn giản nhưng không gom chung được.

Đánh đổi: nhanh, không cần bảo trì, nhưng bạn không kiểm soát được bố cục email, bộ lọc từ khoá cũng thô hơn, và dữ liệu đọc của bạn nằm ở bên thứ ba.

---

## Ghi chú pháp lý

Email chỉ chứa **tiêu đề, mô tả ngắn do chính nguồn phát trong RSS, và link gốc** — đúng mục đích của RSS. Không tải hay đăng lại toàn văn bài viết. Nếu bạn định chuyển tiếp bản tin này cho người khác hoặc dùng trong nội bộ công ty, hãy đọc điều kiện sử dụng RSS của từng nguồn (ví dụ ITmedia có trang riêng: `corp.itmedia.co.jp/media/rss_condition/`).
