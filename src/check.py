"""Bước B2: kiểm tra jobs/<slug>/script.json theo quy tắc trong AGENTS.md, trước khi dựng.

Dùng:  .venv\\Scripts\\python.exe -m src.check <slug>

LỖI  = chắc chắn hỏng (render cũng sẽ từ chối): phải sửa.
NHẮC = vi phạm quy tắc viết kịch bản: nên sửa, rồi chạy lại cho tới khi sạch.
Mã thoát: 1 nếu có LỖI, 0 nếu không.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WORDS_PER_SEC = 4.5          # đo với edge-tts vi-VN, rate +8%: ~294 từ ≈ 65 giây
DUR_RANGE = (45, 75)         # giây
SCENES_RANGE = (5, 9)
TITLE_WORDS_MAX = 6
SCENE_KEYS = {"title", "text", "image", "focus", "zoom"}
TOP_KEYS = {"scenes", "title", "voice", "rate", "pitch", "provider", "openai_model", "openai_voice",
            "openai_instructions", "openai_tempo", "openai_max_pause", "edge_max_pause", "chunk_chars"}


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s, flags=re.UNICODE).lower().strip()


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate(spec, job: Path) -> tuple[list[str], list[str]]:
    """Trả về (lỗi, nhắc). `job` là thư mục jobs/<slug> (để kiểm tra file ảnh)."""
    errors: list[str] = []
    warns: list[str] = []
    if not isinstance(spec, dict) or not isinstance(spec.get("scenes"), list) or not spec["scenes"]:
        return ['script.json phải có dạng {"scenes": [ ... ]} với ít nhất một cảnh.'], warns
    for k in sorted(set(spec) - TOP_KEYS):
        warns.append(f'cấp ngoài: khoá lạ "{k}" sẽ bị bỏ qua (gõ nhầm?).')

    scenes = spec["scenes"]
    total_words = 0
    prev_view = None
    for i, sc in enumerate(scenes, 1):
        tag = f"cảnh {i}"
        if not isinstance(sc, dict):
            errors.append(f"{tag}: phải là một object.")
            prev_view = None
            continue
        for k in sorted(set(sc) - SCENE_KEYS):
            warns.append(f'{tag}: khoá lạ "{k}" sẽ bị bỏ qua (gõ nhầm?).')

        text = sc.get("text")
        if not isinstance(text, str) or not text.split():
            errors.append(f"{tag}: thiếu `text` (lời đọc) hoặc `text` rỗng.")
            text = ""
        toks = text.split()
        total_words += len(toks)
        digits = [t for t in toks if re.search(r"\d", t)]
        if digits:
            warns.append(f"{tag}: có chữ số, hãy viết thành chữ: {', '.join(digits)}")
        caps = [t for t in toks if t not in digits and len(_norm(t)) >= 2 and _norm(t) and t.strip(".,;:!?…\"'()").isupper()]
        if caps:
            warns.append(f"{tag}: có vẻ là từ viết tắt, hãy viết đầy đủ như cách đọc: {', '.join(caps)}")
        n_sent = len(re.findall(r"[.!?…]+(?:\s|$)", text))
        if n_sent > 2:
            warns.append(f"{tag}: {n_sent} câu; mỗi cảnh chỉ nên 1–2 câu dài liền ý (nhiều dấu chấm làm giọng đọc giật).")

        title = sc.get("title")
        if title is not None and not isinstance(title, str):
            errors.append(f"{tag}: `title` phải là chuỗi.")
        elif not title or not title.strip():
            warns.append(f"{tag}: thiếu `title`.")
        else:
            if len(title.split()) > TITLE_WORDS_MAX:
                warns.append(f"{tag}: `title` dài {len(title.split())} từ; nên ≤ {TITLE_WORDS_MAX} từ.")
            if len(_norm(title).split()) >= 2 and _norm(title) in _norm(text):
                warns.append(f"{tag}: `title` trùng nguyên văn một đoạn lời đọc; hãy diễn đạt khác đi.")

        image = sc.get("image")
        has_img = False
        if image:
            if not isinstance(image, str):
                errors.append(f"{tag}: `image` phải là chuỗi đường dẫn.")
            elif not (job / image).is_file():
                errors.append(f"{tag}: không tìm thấy ảnh `{image}` (đường dẫn tính từ {job}).")
            else:
                has_img = True

        focus = sc.get("focus")
        if focus is not None:
            if not (isinstance(focus, list) and len(focus) == 2 and all(_is_num(v) and 0 <= v <= 1 for v in focus)):
                errors.append(f"{tag}: `focus` phải là [x, y] với x, y trong khoảng 0..1 (gốc ở góc trên trái ảnh).")
                focus = None
            elif not image:
                warns.append(f"{tag}: có `focus` nhưng không có `image` nên không có tác dụng.")
        zoom = sc.get("zoom")
        if zoom is not None:
            if not _is_num(zoom) or not 1 <= zoom <= 4:
                errors.append(f"{tag}: `zoom` phải là số trong khoảng 1..4 (thường 1.3–2.0).")
            elif focus is None:
                warns.append(f"{tag}: `zoom` chỉ có tác dụng khi có `focus`.")

        view = (image, tuple(focus) if focus else None) if has_img else None
        if view and view == prev_view:
            warns.append(f"{tag}: lặp lại ảnh `{image}` của cảnh trước mà không đổi `focus`.")
        prev_view = view

    lo, hi = SCENES_RANGE
    if not lo <= len(scenes) <= hi:
        warns.append(f"có {len(scenes)} cảnh; nên {lo}–{hi} cảnh.")
    est = total_words / WORDS_PER_SEC
    lo, hi = DUR_RANGE
    if not lo <= est <= hi:
        warns.append(f"ước tính ~{est:.0f} giây ({total_words} từ); nên {lo}–{hi} giây, "
                     f"tức khoảng {int(lo * WORDS_PER_SEC)}–{int(hi * WORDS_PER_SEC)} từ.")
    return errors, warns


def main() -> None:
    ap = argparse.ArgumentParser(description="Kiểm tra script.json trước khi dựng video.")
    ap.add_argument("slug")
    args = ap.parse_args()
    job = ROOT / "jobs" / args.slug
    path = job / "script.json"
    if not path.is_file():
        raise SystemExit(f"Không thấy {path}")
    try:
        spec = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"LỖI: script.json không phải JSON hợp lệ: {e}")
    errors, warns = validate(spec, job)
    for e in errors:
        print("LỖI :", e)
    for w in warns:
        print("NHẮC:", w)
    n_words = sum(len(str(s.get("text", "")).split()) for s in spec.get("scenes", []) if isinstance(s, dict)) \
        if isinstance(spec, dict) and isinstance(spec.get("scenes"), list) else 0
    print(f"Tổng: {len(errors)} lỗi, {len(warns)} nhắc; {n_words} từ, ước tính ~{n_words / WORDS_PER_SEC:.0f} giây.")
    if not errors and not warns:
        print("Kịch bản đạt, có thể dựng.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
