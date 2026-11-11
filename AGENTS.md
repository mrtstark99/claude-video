# AGENTS.md — hướng dẫn cho AI/agent vận hành repo này

Repo tạo **video dọc 9:16 (Reels/TikTok/Shorts)** từ một bài viết web. Chạy cục bộ trên Windows, không cần API trả phí.
Người dùng nói tiếng Việt: trả lời và viết nội dung bằng tiếng Việt.

## Quy trình khi người dùng đưa một URL bài viết
1. **Lấy bài** (script Python):
   `.venv\Scripts\python.exe -m src.fetch "<url>" --slug <ten-ngan-khong-dau>`
   → tạo `jobs/<slug>/article.json` (tiêu đề, `text`, danh sách ảnh) và `jobs/<slug>/images/`.
2. **Bạn (AI) viết kịch bản**: đọc `article.json`, **xem từng ảnh** (mở file ảnh), rồi ghi `jobs/<slug>/script.json`
   (định dạng và quy tắc bên dưới). Bước này là phần việc của AI, không có script tự làm.
3. **Dựng video**:
   `.venv\Scripts\python.exe -m src.render <slug>`  (đặt `PYTHONUTF8=1` nếu console báo lỗi mã hoá)
   → `output/<slug>.mp4`. Dựng 1 video ~75 giây mất ~1–2 phút trên CPU.
4. **Kiểm tra** rồi báo đường dẫn file cho người dùng (xem mục Kiểm tra). Bạn không nghe được âm thanh,
   nên nói rõ là chưa nghe và nhờ người dùng nghe thử.

Chạy lại bước 3 sau khi sửa `script.json` hoặc `config.yaml`: giọng đọc có cache theo nội dung nên chỉ đọc lại khi chữ/giọng đổi.

## script.json
```json
{
  "scenes": [
    {"title": "Tiêu đề ngắn trên đầu khung",
     "text": "Lời đọc của cảnh.",
     "image": "images/01.jpg",
     "focus": [0.2, 0.55], "zoom": 1.6}
  ]
}
```
- `image`: đường dẫn tính từ `jobs/<slug>/`. Bỏ trống thì cảnh dùng nền gradient.
- `focus` (tùy chọn): điểm quan tâm trên ảnh, toạ độ 0..1; ảnh sẽ zoom dần tới đó (trong khung ảnh). `zoom` = mức zoom cuối.
  Bài chỉ có 1–2 ảnh thì dùng `focus` để mỗi cảnh nhìn vào một phần khác nhau của ảnh.
- Cấp ngoài có thể ghi đè `voice`, `rate`, `pitch`, `provider` (xem `config.yaml`).

### Quy tắc viết kịch bản (quan trọng)
- Dài **45–75 giây**, **5–9 cảnh**; mỗi cảnh 1–2 câu. Chỉ dùng thông tin có trong bài, **không bịa số liệu**.
- **Viết số, ngày, đơn vị thành chữ** ("hai nghìn không trăm linh một", "mười bảy tuổi", "một trăm hai mươi triệu đồng");
  edge-tts đọc chữ số và từ viết tắt rất tệ. Tên riêng ngoại ngữ giữ nguyên; viết "Manchester United" thay cho "MU".
- Giọng đọc nghe **giật nếu có quá nhiều câu cụt và dấu chấm**: ưu tiên câu dài, liền ý, nối bằng "và", "nhưng", "còn".
  Ít dấu phẩy/chấm; dấu câu quyết định chỗ ngắt nghỉ.
- `title` ngắn (≤ ~6 từ), không trùng nguyên văn lời đọc.
- Chọn ảnh theo nội dung cảnh; đừng lặp cùng một ảnh nguyên trạng nhiều cảnh liền nhau mà không đổi `focus`.

## Cấu hình (`config.yaml`)
- `provider`: `edge` (mặc định, miễn phí, không key, cần Internet) hoặc `openai` (cần biến môi trường `OPENAI_API_KEY`,
  chạy bằng `--provider openai`). **Không bao giờ in hoặc ghi key vào file/chat.**
- Nhịp đọc: `rate` (edge), `edge_max_pause` / `openai_max_pause` (khoảng lặng tối đa giữa cụm từ, giây), `openai_tempo`.
- Bố cục khung (`image_box`, cỡ chữ, lề phụ đề tránh vùng giao diện TikTok), `zoom`, `encoder` (`libx264` hoặc `h264_qsv`).
- Nhạc nền: bỏ `.mp3` vào `templates/music/`. Logo: `templates/logo.png`.

## Kiến trúc ngắn gọn
- `src/fetch.py`: tải bài (trafilatura + BeautifulSoup), tải ảnh (lọc ảnh nhỏ/trùng) → `article.json`.
- `src/render.py`: đọc **cả bài một mạch** (một lần gọi TTS, chia nhỏ nếu quá `chunk_chars`) → cắt khoảng lặng dài
  → lấy mốc từng từ (edge: có sẵn; openai: Whisper) → `align_words` ghép mốc vào chữ kịch bản → ranh giới cảnh đặt giữa
  khoảng lặng → phụ đề ASS tô vàng từng từ → FFmpeg dựng 1 lần (nền mờ + ảnh bo góc + zoom trong khung + phụ đề + nhạc).
- Mọi file trung gian nằm ở `jobs/<slug>/cache/` (an toàn để xoá). `output/` chứa video.
- `tools/make_demo_job.py` tạo job `demo` (ảnh giả) để thử bố cục nhanh.

## Kiểm tra sau khi dựng
- `ffprobe`: độ dài stream video ≈ audio (chênh < 0,2 giây).
- Lấy vài khung hình (vd `ffmpeg -i output/x.mp4 -vf "fps=1/10,scale=360:-1,tile=4x2" -frames:v 1 sheet.png`) và xem:
  phụ đề không đè ảnh, tiêu đề không bị cắt, ảnh không méo.
- Đừng khẳng định chất lượng giọng khi chưa ai nghe.

## Lưu ý môi trường
- Windows + PowerShell. FFmpeg cài bằng `winget install Gyan.FFmpeg`; code tự tìm trong thư mục WinGet nên không cần PATH.
- PowerShell 5 ghi UTF-8 **có BOM** khi dùng `Set-Content -Encoding UTF8`; code đọc `script.json` bằng `utf-8-sig` nên không sao.
  Muốn ghi file JSON tiếng Việt thì dùng công cụ ghi file của agent thay vì `echo`/redirect.
- edge-tts là endpoint không chính thức, thỉnh thoảng trả "No audio was received": code đã tự thử lại; nếu vẫn lỗi hãy chạy lại sau ít phút.
- Máy không có GPU rời; không dùng model TTS/ASR nặng chạy local.

## Bản quyền / đạo đức
- Chỉ dùng ảnh và bài của chính người dùng, hoặc nguồn đã được phép. Nếu người dùng đưa bài của báo khác, nhắc rằng
  ảnh/nội dung thuộc nguồn đó và không nên đăng công khai khi chưa xin phép.
- Không đăng video lên mạng xã hội, không gửi dữ liệu ra ngoài trừ các lệnh gọi TTS đã nêu ở trên.
