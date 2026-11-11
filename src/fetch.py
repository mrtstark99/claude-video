"""Bước A: lấy bài viết từ URL -> jobs/<slug>/article.json + images/

Dùng:  .venv\\Scripts\\python.exe -m src.fetch <url> [--slug ten-job] [--min-width 400]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; claude-video/1.0)"}


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:60] or "bai-viet"


def best_src(img, base: str) -> str | None:
    for attr in ("data-src", "data-lazy-src", "data-original"):
        if img.get(attr):
            return urljoin(base, img[attr])
    srcset = img.get("srcset") or img.get("data-srcset")
    if srcset:
        cands = []
        for part in srcset.split(","):
            bits = part.strip().split()
            if not bits:
                continue
            w = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit() else 0
            cands.append((w, bits[0]))
        if cands:
            return urljoin(base, max(cands)[1])
    if img.get("src") and not img["src"].startswith("data:"):
        return urljoin(base, img["src"])
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--slug")
    ap.add_argument("--min-width", type=int, default=400)
    args = ap.parse_args()

    r = requests.get(args.url, headers=UA, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
    html = r.text

    meta = trafilatura.extract_metadata(html)
    text = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
    soup = BeautifulSoup(html, "html.parser")
    title = (meta.title if meta and meta.title else None) or (soup.title.string.strip() if soup.title else "")

    slug = args.slug or slugify(title or Path(urlparse(args.url).path).name)
    job = ROOT / "jobs" / slug
    (job / "images").mkdir(parents=True, exist_ok=True)

    root = soup.find("article") or soup.find("main") or soup.body or soup
    srcs: list[tuple[str, str]] = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        srcs.append((urljoin(args.url, og["content"]), title))
    for img in root.find_all("img"):
        s = best_src(img, args.url)
        if s:
            srcs.append((s, (img.get("alt") or "").strip()))

    images, seen = [], set()
    for src, alt in srcs:
        try:
            resp = requests.get(src, headers=UA, timeout=30)
            resp.raise_for_status()
            im = Image.open(io.BytesIO(resp.content))
            im.load()
        except Exception:
            continue
        if im.width < args.min_width or im.format not in ("JPEG", "PNG", "WEBP"):
            continue
        h = hashlib.md5(resp.content).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        n = len(images) + 1
        ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[im.format]
        fn = f"images/{n:02d}.{ext}"
        (job / fn).write_bytes(resp.content)
        images.append({"file": fn, "alt": alt, "width": im.width, "height": im.height, "source": src})

    data = {
        "url": args.url, "slug": slug, "title": title,
        "date": meta.date if meta else None,
        "text": text, "images": images,
    }
    (job / "article.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"slug: {slug}\ntiêu đề: {title}\nđộ dài bài: {len(text.split())} từ\nảnh tải được: {len(images)}\n-> {job / 'article.json'}")


if __name__ == "__main__":
    main()
