"""Bước C: dựng video từ jobs/<slug>/script.json.

Dùng:  .venv\\Scripts\\python.exe -m src.render <slug>

script.json:
{
  "title": "Tiêu đề (không bắt buộc)",
  "voice": "vi-VN-HoaiMyNeural",      # tùy chọn, ghi đè config
  "rate": "+0%",                      # tùy chọn
  "scenes": [
    {"title": "Tiêu đề ngắn", "text": "Lời đọc của cảnh", "image": "images/01.jpg"}
  ]
}
Đường dẫn ảnh tính từ thư mục jobs/<slug>/.
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import edge_tts
import yaml
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- tiện ích
def find_tool(name: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for hit in base.rglob(f"{name}.exe"):
        return str(hit)
    raise SystemExit(f"Không tìm thấy {name}. Cài FFmpeg: winget install Gyan.FFmpeg")


def ensure_fonts() -> None:
    """Repo không chứa file font (bản quyền). Chép Segoe UI từ Windows vào templates/fonts nếu chưa có."""
    d = ROOT / "templates" / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    if any(d.glob("*.ttf")):
        return
    win_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("segoeuib.ttf", "segoeui.ttf"):
        if (win_fonts / name).exists():
            shutil.copy(win_fonts / name, d / name)
    if not any(d.glob("*.ttf")):
        print("Cảnh báo: chưa có font trong templates/fonts (đặt font hỗ trợ tiếng Việt, đúng tên `font` trong config.yaml).")


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def probe_duration(ffprobe: str, path: Path) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


# ---------------------------------------------------------------- giọng đọc
async def _synth(text: str, voice: str, rate: str, pitch: str, out_mp3: Path) -> list[dict]:
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    words: list[dict] = []
    with open(out_mp3, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append({
                    "start": ch["offset"] / 1e7,
                    "dur": ch["duration"] / 1e7,
                    "text": ch["text"],
                })
    return words


def _norm(tok: str) -> str:
    """Chuẩn hoá để so khớp từ: bỏ dấu câu, chữ thường."""
    return re.sub(r"[^\w]", "", tok, flags=re.UNICODE).lower()


def align_words(script_tokens: list[str], asr: list[dict], audio_dur: float) -> list[dict]:
    """Gắn mốc thời gian (từ nhận dạng giọng nói) vào đúng các từ của kịch bản.
    Từ khớp thì lấy mốc thật; đoạn không khớp thì chia đều theo độ dài chữ giữa hai mốc kề nhau."""
    a = [_norm(t) for t in script_tokens]
    b = [_norm(w["text"]) for w in asr]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(a)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            w = asr[blk.b + k]
            times[blk.a + k] = (w["start"], w["start"] + w["dur"])
    i = 0
    while i < len(a):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(a) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else (asr[0]["start"] if asr else 0.0)
        hi = times[j][0] if j < len(a) else (asr[-1]["start"] + asr[-1]["dur"] if asr else audio_dur)
        hi = max(hi, lo + 0.05 * (j - i))
        total = sum(max(len(a[k]), 1) for k in range(i, j))
        t = lo
        for k in range(i, j):
            d = (hi - lo) * max(len(a[k]), 1) / total
            times[k] = (t, t + d)
            t += d
        i = j
    out, prev_end = [], 0.0
    for tok, (st, en) in zip(script_tokens, times):
        if en - st < 0.1:  # Whisper hay "nén" từ đầu câu về ~0s: kéo mốc bắt đầu lùi vào khoảng lặng trước đó
            st = max(min(st, en - 0.12), prev_end)
        out.append({"start": st, "dur": max(en - st, 0.05), "text": tok})
        prev_end = st + out[-1]["dur"]
    return out


def _openai_client():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "Thiếu biến môi trường OPENAI_API_KEY.\n"
            "  Đặt bằng PowerShell:  setx OPENAI_API_KEY \"sk-...\"  rồi mở lại terminal.\n"
            "  Hoặc dùng tạm edge-tts:  python -m src.render <slug> --provider edge"
        )
    from openai import OpenAI
    return OpenAI()


def openai_tts_file(text: str, tts: dict, dst: Path) -> None:
    client = _openai_client()
    with client.audio.speech.with_streaming_response.create(
        model=tts["openai_model"], voice=tts["openai_voice"], input=text,
        instructions=tts["openai_instructions"], response_format="mp3",
    ) as r:
        r.stream_to_file(dst)


def openai_word_times(mp3: Path, tokens: list[str], ffprobe: str) -> list[dict]:
    """Whisper chỉ để lấy mốc thời gian từng từ, rồi ghép vào đúng chữ của kịch bản."""
    client = _openai_client()
    with open(mp3, "rb") as f:
        tr = client.audio.transcriptions.create(
            model="whisper-1", file=f, response_format="verbose_json",
            timestamp_granularities=["word"], language="vi",
        )
    asr = [{"start": w.start, "dur": w.end - w.start, "text": w.word} for w in (tr.words or [])]
    dur = probe_duration(ffprobe, mp3)
    return align_words(tokens, asr, dur) if asr else fallback_words(" ".join(tokens), dur)


def _retry(fn, what: str, tries: int = 6):
    last = None
    for attempt in range(tries):
        try:
            return fn()
        except SystemExit:
            raise
        except Exception as e:
            msg = str(e)
            if any(x in msg for x in ("no credits", "insufficient_quota", "invalid_api_key", "Incorrect API key", "401")):
                raise SystemExit(f"OpenAI từ chối (thử lại cũng vô ích): {msg}")
            last = e
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"{what} lỗi: {last}")


def detect_silences(ffmpeg: str, path: Path, min_dur: float) -> list[tuple[float, float, float]]:
    """Các khoảng lặng >= min_dur giây: [(bắt đầu, kết thúc, độ dài)]."""
    r = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-af", f"silencedetect=noise=-40dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return [(float(e) - float(d), float(e), float(d))
            for e, d in re.findall(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", r.stderr)]


def remap_words(words: list[dict], sil: list[tuple[float, float, float]], max_pause: float) -> list[dict]:
    """Dời mốc thời gian từng từ sau khi các khoảng lặng dài đã bị cắt còn tối đa max_pause giây."""
    def shift(t: float) -> float:
        return sum(max(0.0, d - max_pause) for (_, e, d) in sil if e <= t + 1e-6)
    out = []
    for w in words:
        a, b = w["start"], w["start"] + w["dur"]
        na, nb = a - shift(a), b - shift(b)
        out.append({**w, "start": na, "dur": max(nb - na, 0.04)})
    return out


def synth_narration(scenes_spec: list[dict], tts: dict, cache_dir: Path, ffmpeg: str, ffprobe: str):
    """Đọc cả bài như MỘT mạch (ngữ điệu liền, không ngắt theo cảnh).
    Trả về (file âm thanh, từ theo từng cảnh với mốc tuyệt đối, tổng thời lượng)."""
    prov = tts["provider"]
    limit = int(tts["chunk_chars"])
    chunks: list[list[int]] = []  # chỉ số cảnh trong từng lượt gọi TTS (giới hạn độ dài đầu vào)
    cur, n = [], 0
    for i, sc in enumerate(scenes_spec):
        if cur and n + len(sc["text"]) > limit:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(i)
        n += len(sc["text"]) + 1
    chunks.append(cur)

    openai = prov == "openai"
    tempo = float(tts["openai_tempo"]) if openai else 1.0  # edge: tăng tốc bằng tham số rate của chính nó
    files: list[Path] = []
    abs_words: list[list[dict]] = [[] for _ in scenes_spec]
    offset = 0.0
    for ci, idxs in enumerate(chunks):
        text = " ".join(scenes_spec[i]["text"] for i in idxs)
        mp = float(tts["openai_max_pause"] if openai else tts["edge_max_pause"])
        sig = (f"narr|openai|{text}|{tts['openai_model']}|{tts['openai_voice']}|{tts['openai_instructions']}|{tempo}|{mp}"
               if openai else f"narr|edge|{text}|{tts['voice']}|{tts['rate']}|{tts['pitch']}|{mp}")
        key = hashlib.sha1(sig.encode()).hexdigest()[:10]
        mp3 = cache_dir / f"narr{ci:02d}_{key}.mp3"
        meta = cache_dir / f"narr{ci:02d}_{key}.json"
        if mp3.exists() and meta.exists():
            words = json.loads(meta.read_text(encoding="utf-8"))
        else:
            print(f"  đọc lượt {ci + 1}/{len(chunks)} ({len(text)} ký tự)...")
            raw = cache_dir / f"narr{ci:02d}_{key}_raw.mp3"
            if openai:
                _retry(lambda: openai_tts_file(text, tts, raw), "OpenAI TTS")
            else:  # edge-tts trả luôn mốc từng từ, không cần Whisper (không gọi API nào khác)
                raw_words = _retry(lambda: asyncio.run(_synth(text, tts["voice"], tts["rate"], tts["pitch"], raw)), "edge-tts")
                if mp > 0 and raw_words:  # mốc từ của edge tính trên bản chưa cắt lặng -> dời theo các đoạn bị cắt
                    raw_words = remap_words(raw_words, detect_silences(ffmpeg, raw, mp / 2), mp)
            filt = []
            if abs(tempo - 1.0) > 0.01:  # tăng tốc mà giữ cao độ
                filt.append(f"atempo={tempo}")
            if mp > 0:  # khoảng lặng giữa các cụm từ dài nhất = max_pause giây (đỡ cảm giác ngắt quãng)
                h = mp / 2
                filt.append(f"silenceremove=stop_periods=-1:stop_duration={h:.3f}:stop_threshold=-40dB:stop_silence={h:.3f}")
            if filt:
                subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw),
                                "-filter:a", ",".join(filt), "-b:a", "128k", str(mp3)], check=True)
                raw.unlink(missing_ok=True)
            else:
                raw.replace(mp3)
            tokens = text.split()
            if openai:
                words = _retry(lambda: openai_word_times(mp3, tokens, ffprobe), "Whisper")
            else:
                dur_ = probe_duration(ffprobe, mp3)
                words = align_words(tokens, raw_words, dur_) if raw_words else fallback_words(text, dur_)
            meta.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        files.append(mp3)
        k = 0  # chia lại từ cho từng cảnh theo số từ của cảnh
        for i in idxs:
            cnt = len(scenes_spec[i]["text"].split())
            for w in words[k:k + cnt]:
                abs_words[i].append({**w, "start": w["start"] + offset})
            k += cnt
        offset += probe_duration(ffprobe, mp3)

    if len(files) == 1:
        audio = files[0]
    else:
        audio = cache_dir / "narration.mp3"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        for f in files:
            cmd += ["-i", str(f)]
        cmd += ["-filter_complex", "".join(f"[{i}:a]" for i in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[o]",
                "-map", "[o]", "-b:a", "128k", str(audio)]
        subprocess.run(cmd, check=True)
    return audio, abs_words, probe_duration(ffprobe, audio)


def fallback_words(text: str, audio_dur: float) -> list[dict]:
    """Nếu không có mốc từ: chia thời lượng theo độ dài chữ."""
    toks = text.split()
    total = sum(len(t) for t in toks) or 1
    t0, out = 0.0, []
    for t in toks:
        d = audio_dur * len(t) / total
        out.append({"start": t0, "dur": d, "text": t})
        t0 += d
    return out


# ---------------------------------------------------------------- phụ đề ASS
def ass_time(t: float) -> str:
    t = max(t, 0)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(s: str) -> str:
    return s.replace("{", "(").replace("}", ")").replace("\n", " ").replace("\\", "")


def chunk_words(words: list[dict], max_words: int, max_chars: int = 0) -> list[list[dict]]:
    """Chia từ thành các dòng phụ đề. max_chars > 0: dòng không dài quá số ký tự đó (tránh tràn khung)."""
    chunks, cur = [], []
    for i, w in enumerate(words):
        if cur and max_chars > 0 and sum(len(x["text"]) + 1 for x in cur) + len(w["text"]) > max_chars:
            chunks.append(cur)
            cur = []
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - (w["start"] + w["dur"])) if nxt else 99
        if len(cur) >= max_words or gap > 0.25 or re.search(r"[,.;:!?…]$", w["text"]):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(cfg: dict, scenes: list[dict]) -> str:
    W, H = cfg["width"], cfg["height"]
    font = cfg["font"]
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{cfg['caption_size']},&H0000E0FF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,2,2,80,80,{cfg['caption_margin_bottom']},1
Style: Title,{font},{cfg['title_size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,8,90,90,{cfg['title_margin_top']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for sc in scenes:
        t0, dur = sc["start"], sc["dur"]
        if sc.get("title"):
            lines.append(
                f"Dialogue: 0,{ass_time(t0)},{ass_time(t0 + dur)},Title,,0,0,0,,"
                f"{{\\fad(250,200)}}{ass_escape(sc['title'])}"
            )
        chunks = chunk_words(sc["words"], cfg["caption_words_max"], int(cfg.get("caption_chars_max", 0)))
        for ci, ch in enumerate(chunks):
            c_start = t0 + ch[0]["start"]
            if ci + 1 < len(chunks):
                c_end = t0 + chunks[ci + 1][0]["start"]
            else:
                c_end = min(t0 + ch[-1]["start"] + ch[-1]["dur"] + 0.2, t0 + dur)
            c_end = max(c_end, c_start + 0.15)
            parts = []
            for wi, w in enumerate(ch):
                nxt_start = ch[wi + 1]["start"] if wi + 1 < len(ch) else (c_end - t0)
                k = max(int(round((nxt_start - w["start"]) * 100)), 1)
                txt = ass_escape(w["text"])
                if cfg["caption_upper"]:
                    txt = txt.upper()
                parts.append(f"{{\\k{k}}}{txt}")
            lines.append(
                f"Dialogue: 1,{ass_time(c_start)},{ass_time(c_end)},Caption,,0,0,0,,{' '.join(parts)}"
            )
    return head + "\n".join(lines) + "\n"


# ---------------------------------------------------------------- khung hình tĩnh
def compose_still(cfg: dict, src: Path | None, dst: Path):
    """Vẽ nền 1080x1920 (không gồm ảnh chính) vào dst.
    Có ảnh: ghi thêm mặt nạ bo góc <dst>_mask.png, trả về (x, y, w, h) vùng đặt ảnh. Không có ảnh: trả None."""
    W, H = cfg["width"], cfg["height"]
    bx, by, bw, bh = cfg["image_box"]
    if src is None or not src.exists():
        bg = Image.new("RGB", (W, H), (18, 24, 44))
        d = ImageDraw.Draw(bg)
        for y in range(H):  # gradient dọc đơn giản
            c = int(18 + 30 * y / H)
            d.line([(0, y), (W, y)], fill=(c, c + 6, c + 26))
        bg.save(dst)
        return None
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    small = ImageOps.fit(im, (W // 6, H // 6), Image.LANCZOS).filter(ImageFilter.GaussianBlur(10))
    bg = ImageEnhance.Brightness(small.resize((W, H), Image.BICUBIC)).enhance(0.45)
    bg.save(dst)
    s = min(bw / im.width, bh / im.height)
    fw, fh = int(im.width * s) // 2 * 2, int(im.height * s) // 2 * 2  # số chẵn cho yuv420
    mask = Image.new("L", (fw, fh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, fw - 1, fh - 1], radius=36, fill=255)
    mask.save(dst.with_name(dst.stem + "_mask.png"))
    return bx + (bw - fw) // 2, by + (bh - fh) // 2, fw, fh


# ---------------------------------------------------------------- chính
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--keep", action="store_true", help="giữ file trung gian (still, ass)")
    ap.add_argument("--provider", choices=["edge", "openai"], help="ghi đè provider trong config.yaml")
    args = ap.parse_args()

    cfg = load_config()
    ensure_fonts()
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    job = ROOT / "jobs" / args.slug
    spec = json.loads((job / "script.json").read_text(encoding="utf-8-sig"))  # chịu được BOM
    missing = [f"  cảnh {i + 1}: {sc['image']}" for i, sc in enumerate(spec["scenes"])
               if sc.get("image") and not (job / sc["image"]).is_file()]
    if missing:  # kiểm tra trước khi gọi TTS; trước đây cảnh âm thầm chuyển sang nền gradient
        raise SystemExit("Không tìm thấy ảnh (đường dẫn tính từ " + str(job) + "):\n" + "\n".join(missing)
                         + "\nSửa `image` trong script.json, hoặc bỏ trống để dùng nền gradient.")
    cache = job / "cache"
    cache.mkdir(exist_ok=True)

    tts = {k: spec.get(k, cfg[k]) for k in (
        "provider", "voice", "rate", "pitch", "openai_model", "openai_voice", "openai_instructions",
        "openai_tempo", "openai_max_pause", "edge_max_pause", "chunk_chars")}
    if args.provider:
        tts["provider"] = args.provider
    print(f"Giọng đọc: {tts['provider']}")
    fps = cfg["fps"]
    W, H = cfg["width"], cfg["height"]

    nsc = len(spec["scenes"])
    scenes = []
    t = 0.0
    print("Đọc liền mạch cả bài...")
    n_audio, n_words, n_dur = synth_narration(spec["scenes"], tts, cache, ffmpeg, ffprobe)
    bounds = [0.0]  # ranh giới cảnh nằm giữa khoảng lặng, để hình đổi đúng lúc chuyển ý
    for i in range(1, nsc):
        prev_end = n_words[i - 1][-1]["start"] + n_words[i - 1][-1]["dur"]
        bounds.append((prev_end + n_words[i][0]["start"]) / 2)
    bounds.append(n_dur + max(cfg["pad"], 0.6))
    F = [0]  # mốc khung hình tích luỹ, tránh sai số làm tròn dồn
    for b in bounds[1:]:
        F.append(max(round(b * fps), F[-1] + 1))
    for i, sc in enumerate(spec["scenes"]):
        frames = F[i + 1] - F[i]
        t = F[i] / fps
        words = [{**w, "start": w["start"] - t} for w in n_words[i]]
        dur = frames / fps
        still = cache / f"still{i:02d}.png"
        img = (job / sc["image"]) if sc.get("image") else None
        place = compose_still(cfg, img, still)
        focus = None  # điểm zoom tới (toạ độ 0..1 trên chính tấm ảnh) + mức zoom cuối
        if sc.get("focus") and place:
            fx, fy = sc["focus"]
            focus = (fx, fy, float(sc.get("zoom", 1.6)))
        scenes.append({
            "title": sc.get("title"), "words": words, "still": still,
            "mask": still.with_name(still.stem + "_mask.png"), "image": img if place else None,
            "place": place, "frames": frames, "dur": dur, "start": t, "focus": focus,
        })
    total = F[-1] / fps
    print(f"Tổng thời lượng: {total:.1f}s")

    (cache / "captions.ass").write_text(build_ass(cfg, scenes), encoding="utf-8")

    # ---- đồ thị FFmpeg
    def rel(p: Path) -> str:
        return str(p.relative_to(job)).replace("\\", "/")

    inputs: list[str] = []
    graph: list[str] = []
    idx = 0
    for sc in scenes:
        sc["bg_in"] = idx
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{sc['dur']:.4f}", "-i", rel(sc["still"])]
        idx += 1
        if sc["place"]:
            sc["fg_in"] = idx
            inputs += ["-i", rel(sc["image"])]
            sc["mk_in"] = idx + 1
            inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{sc['dur']:.4f}", "-i", rel(sc["mask"])]
            idx += 2
    au_in = idx
    inputs += ["-i", rel(n_audio)]
    idx += 1
    n = len(scenes)
    zoom = cfg["zoom"]
    for i, sc in enumerate(scenes):
        N = sc["frames"]
        if sc["place"]:
            px, py, fw, fh = sc["place"]
            if sc["focus"]:  # zoom dần tới một điểm quan tâm, chỉ trong khung ảnh
                fx, fy, ze = sc["focus"]
                z, x, y = f"1+{ze - 1:.3f}*on/{N}", f"iw*{fx:.4f}-iw/zoom/2", f"ih*{fy:.4f}-ih/zoom/2"
            else:
                z = (f"1+{zoom}*on/{N}" if i % 2 == 0 else f"{1 + zoom}-{zoom}*on/{N}") if zoom > 0 else "1"
                x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            graph.append(
                f"[{sc['fg_in']}:v]scale={fw * 2}:{fh * 2}:flags=bicubic,"
                f"zoompan=z='{z}':x='{x}':y='{y}':d={N}:s={fw}x{fh}:fps={fps}[fz{i}]"
            )
            graph.append(f"[{sc['mk_in']}:v]format=gray[mm{i}]")
            graph.append(f"[fz{i}][mm{i}]alphamerge[fr{i}]")
            graph.append(f"[{sc['bg_in']}:v][fr{i}]overlay={px}:{py}:shortest=1,fps={fps},setsar=1[v{i}]")
        else:
            graph.append(f"[{sc['bg_in']}:v]fps={fps},setsar=1[v{i}]")
    graph.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vc]")
    graph.append(  # một mạch âm thanh liên tục cho cả video
        f"[{au_in}:a]aresample=44100,aformat=channel_layouts=stereo,"
        f"apad=whole_dur={total:.4f},atrim=0:{total:.4f}[ac]"
    )

    vlabel = "vc"
    fontsdir = "../../templates/fonts"
    graph.append(f"[{vlabel}]ass=cache/captions.ass:fontsdir={fontsdir}[vs]")
    vlabel = "vs"
    next_in = idx

    logo = ROOT / "templates" / "logo.png"
    if logo.exists():
        inputs += ["-i", str(logo)]
        graph.append(f"[{next_in}:v]scale={cfg['logo_width']}:-1[lg]")
        graph.append(f"[{vlabel}][lg]overlay=W-w-50:70[vo]")
        vlabel = "vo"
        next_in += 1

    alabel = "ac"
    music = sorted((ROOT / "templates" / "music").glob("*.mp3"))
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music[0])]
        graph.append(
            f"[{next_in}:a]aresample=44100,aformat=channel_layouts=stereo,volume={cfg['music_volume']},"
            f"atrim=0:{total:.3f},afade=t=out:st={max(total - 2, 0):.3f}:d=2[mu]"
        )
        graph.append("[ac][mu]amix=inputs=2:duration=first:normalize=0[am]")
        alabel = "am"

    out = ROOT / "output" / f"{args.slug}.mp4"
    out.parent.mkdir(exist_ok=True)
    if cfg["encoder"] == "h264_qsv":
        venc = ["-c:v", "h264_qsv", "-global_quality", "22", "-look_ahead", "0"]
    else:
        venc = ["-c:v", "libx264", "-preset", "medium", "-crf", str(cfg["crf"])]
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-stats",
        *inputs,
        "-filter_complex", ";".join(graph),
        "-map", f"[{vlabel}]", "-map", f"[{alabel}]",
        *venc, "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(out),
    ]
    print("Đang dựng video bằng FFmpeg...")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=job)
    if r.returncode != 0:
        raise SystemExit("FFmpeg lỗi (xem thông báo ở trên)")
    print(f"Xong sau {time.time() - t0:.0f}s: {out}")
    if not args.keep:
        for sc in scenes:
            sc["still"].unlink(missing_ok=True)


if __name__ == "__main__":
    main()
