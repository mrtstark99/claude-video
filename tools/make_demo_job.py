"""Tạo job thử nghiệm jobs/demo (ảnh giả + kịch bản mẫu) để kiểm tra bước render."""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

job = Path(__file__).resolve().parent.parent / "jobs" / "demo"
(job / "images").mkdir(parents=True, exist_ok=True)
font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 90)

specs = [  # (tên, rộng, cao, màu) - đủ tỉ lệ ngang / dọc / vuông
    ("01", 1600, 900, (200, 70, 70)),
    ("02", 900, 1400, (60, 130, 200)),
    ("03", 1200, 1200, (70, 160, 100)),
    ("04", 1920, 1080, (200, 150, 50)),
]
for name, w, h, col in specs:
    im = Image.new("RGB", (w, h), col)
    d = ImageDraw.Draw(im)
    for k in range(0, w + h, 120):
        d.line([(k, 0), (0, k)], fill=tuple(min(c + 25, 255) for c in col), width=30)
    d.text((w // 2, h // 2), f"ẢNH {name}\n{w}x{h}", font=font, fill="white", anchor="mm", align="center")
    im.save(job / "images" / f"{name}.jpg", quality=92)

script = {
    "title": "Demo",
    "scenes": [
        {"title": "Học bổng du học Nhật Bản", "text": "Mỗi năm, hàng nghìn du học sinh Việt Nam nhận học bổng để sang Nhật học tập.", "image": "images/01.jpg"},
        {"title": "Điều kiện cơ bản", "text": "Bạn cần có chứng chỉ tiếng Nhật N5 trở lên và kết quả học tập từ sáu phẩy năm điểm.", "image": "images/02.jpg"},
        {"title": "Chi phí dự kiến", "text": "Tổng chi phí năm đầu khoảng một trăm hai mươi triệu đồng, đã gồm học phí và ký túc xá.", "image": "images/03.jpg"},
        {"title": "Bắt đầu ngay hôm nay", "text": "Hãy chuẩn bị hồ sơ sớm để không bỏ lỡ kỳ nhập học tháng mười.", "image": "images/04.jpg"},
    ],
}
(job / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
print("Đã tạo", job)
