# claude-video

Tạo video dọc 9:16 (Reels / TikTok / Shorts) từ bài viết trên web. Chạy cục bộ: FFmpeg + edge-tts.
Phần viết kịch bản do Claude làm trong phiên chat (không cần API key).

> Hướng dẫn vận hành dành cho AI/agent (quy trình, quy tắc viết kịch bản, lưu ý môi trường): xem [AGENTS.md](AGENTS.md).

## Luồng
```
A. fetch   URL  ──► jobs/<slug>/article.json + images/
B. Claude  đọc article.json, xem ảnh ──► jobs/<slug>/script.json
C. render  script.json ──► output/<slug>.mp4
```

## Lệnh
```bash
.venv\Scripts\python.exe -m src.fetch <url> [--slug ten]
.venv\Scripts\python.exe -m src.render <slug> [--keep]
```

## script.json
```json
{
  "scenes": [
    {"title": "Tiêu đề ngắn trên đầu khung", "text": "Lời đọc (viết số/ngày thành chữ)", "image": "images/01.jpg"}
  ]
}
```
Tùy chọn ở cấp ngoài: `voice`, `rate`, `pitch` (ghi đè `config.yaml`).
Tùy chọn mỗi cảnh: `"focus": [0.2, 0.55]` (điểm quan tâm trên ảnh, toạ độ 0..1, ảnh sẽ zoom dần tới đó) và `"zoom": 1.6` (mức zoom cuối).
Khi bài chỉ có 1–2 ảnh, dùng `focus` để mỗi cảnh nhìn vào một phần khác nhau của cùng một ảnh.
Zoom chỉ diễn ra trong khung ảnh, không đè lên tiêu đề hay phụ đề.

## Tùy biến
- `config.yaml`: giọng, tốc độ, bố cục, cỡ chữ, zoom, bộ mã hóa (`h264_qsv` để nhanh hơn).
- `templates/music/*.mp3`: nhạc nền (file đầu tiên). `templates/logo.png`: logo góc trên phải.
- Giọng đọc được cache theo nội dung trong `jobs/<slug>/cache/`: sửa lời đọc thì chỉ lượt đọc đó được tạo lại.

## Giọng đọc (provider trong config.yaml)
Cả bài được đọc **một mạch** (không đọc riêng từng cảnh) để ngữ điệu liền; hình đổi cảnh vào đúng khoảng lặng giữa hai ý.
Khoảng lặng giữa các cụm từ bị rút còn tối đa `*_max_pause` giây (mặc định 0,3) cho đỡ ngắt quãng.
- `edge` (mặc định): miễn phí, không cần key hay tài khoản, cần Internet. Tốc độ chỉnh bằng `rate` (vd `"+8%"`).
  edge-tts tự trả mốc thời gian từng từ nên không cần dịch vụ nào khác; mốc được dời theo các đoạn lặng đã cắt.
- `openai` (tùy chọn): `--provider openai`. Model `gpt-4o-mini-tts`, chỉnh giọng điệu bằng `openai_instructions`.
  Cần `OPENAI_API_KEY` và tài khoản API có số dư. OpenAI không trả mốc từ, nên dùng thêm `whisper-1` để lấy mốc
  rồi ghép vào chữ của kịch bản (`align_words`).
- Đổi provider/giọng/tốc độ thì giọng được tạo lại (cache theo cấu hình).

## Cài đặt (Windows)
```powershell
winget install Gyan.FFmpeg
python -m venv .venv
.venv\Scripts\python.exe -m pip install edge-tts requests beautifulsoup4 trafilatura Pillow pyyaml openai
python tools\make_demo_job.py
.venv\Scripts\python.exe -m src.render demo
```
Repo không kèm font: lần chạy đầu code tự chép Segoe UI từ `C:\Windows\Fonts` (đổi `font` trong `config.yaml` nếu dùng font khác).
Thư mục `jobs/` và `output/` chứa dữ liệu từng video nên không được đưa lên repo.

## Ghi chú
- Cần FFmpeg (`winget install Gyan.FFmpeg`); script tự tìm trong thư mục WinGet.
- edge-tts là endpoint không chính thức của Microsoft, cần Internet.
- Tốc độ dựng: khoảng 2x thời lượng video trên i5-7500 (CPU).
- `tools/make_demo_job.py` tạo job `demo` để thử bố cục.
