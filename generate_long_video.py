#!/usr/bin/env python3
"""
Gadget Pulse Malayalam — 3x/Week Long-Form Video Generator (Mon / Wed / Sat)
Series Rotation:
  - Monday:    Top 5 Best Tech / Gadgets Under a Budget in India
  - Wednesday: Smart Home & Appliance Buying Guide (TV, Fridge, Washing Machine, Robot Vacuum)
  - Saturday:  This Week's Top 5 New Tech & Gadget Launches in India
"""

import json
import math
import os
import random
import re
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import urllib.parse
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from pydantic import BaseModel, Field

GEMINI_MODEL_CANDIDATES = [
    os.getenv("GEMINI_MODEL", "").strip(),
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
TTS_VOICE = os.getenv("TTS_VOICE", "ml-IN-MidhunNeural")
PRIVACY_STATUS = os.getenv("YT_PRIVACY_STATUS", "private")
CATEGORY_ID = os.getenv("YT_CATEGORY_ID", "28")
GRAPH_API_VERSION = "v21.0"
WORK_DIR = Path("build_long")
HISTORY_FILE = Path("used_long_topics.json")
META_CACHE_FILE = WORK_DIR / "long_meta.json"
IST_TZ = timezone(timedelta(hours=5, minutes=30))

SERIES_CONFIG = {
    "top5_budget": {
        "playlist_title": "Best Tech Under Budget (Malayalam)",
        "prompt_angle": "Compare the top 5 value-for-money smartphones, earbuds, or laptops currently available in India under a clear INR budget.",
    },
    "smart_home_guide": {
        "playlist_title": "Smart Home & Appliance Guides (Malayalam)",
        "prompt_angle": "Practical Indian buyer's guide for smart home appliances (robotic vacuums, smart TVs, fridges, inverter ACs, washing machines) with 5 real models or key buying rules.",
    },
    "weekly_launches": {
        "playlist_title": "Weekly Tech Launches India (Malayalam)",
        "prompt_angle": "Cover 5 notable tech/gadget launches or announcements in India this week with verified INR prices and key specs.",
    },
}


class GadgetChapter(BaseModel):
    chapter_title_ml: str = Field(description="Short Malayalam + English chapter title (3-6 words)")
    product_name: str = Field(description="Exact product name for this chapter (for affiliate links & official photo lookup).")
    available_in_india: bool = Field(description="True if on sale in India now.")
    price_inr_label: str = Field(description="Verified India INR price or guide tip label (e.g. '₹19,999').")
    narration_ml: str = Field(description="170-210 words of natural conversational Kerala Malayalam narration. Keep exact English product/brand names in narration_ml.")
    pronunciation_pairs: List[str] = Field(
        default_factory=list,
        description="List of 'English Term => Phonetic Malayalam' pairs for every English product/brand/model/spec name in narration_ml (e.g. 'iQOO Neo 10R => ഐക്യൂ നിയോ ടെൻ ആർ', 'OnePlus 13R => വൺപ്ലസ് തേർട്ടീൻ ആർ').",
    )
    key_fact_ml: str = Field(description="Crisp 1-line Malayalam takeaway (max 85 chars) for top product callout box.")
    official_page_urls: List[str] = Field(description="1-2 official/launch URLs from search results for this product.")
    search_queries: List[str] = Field(description="3 literal English visual search terms (no abbreviations) for 16:9 stock clips.")


class LongTechPlan(BaseModel):
    topic_key: str = Field(description="Unique concise English topic key for deduplication.")
    title: str = Field(description="Catchy Malayalam + English YouTube title (max 85 chars).")
    thumbnail_hook_ml: str = Field(description="Bold 3-5 word Malayalam curiosity hook for 1280x720 thumbnail.")
    thumbnail_image_query: str = Field(description="2-3 concrete English words for Pixabay thumbnail fallback.")
    intro_narration_ml: str = Field(description="65-85 words of engaging Malayalam intro previewing the 5 items.")
    chapters: List[GadgetChapter] = Field(description="Exactly 5 chapters (~8-9 minutes total).")
    outro_narration_ml: str = Field(
        description=(
            "50-65 words summarizing the picks, noting product links are in the description, and ending with: "
            "'ഇത്തരം ടെക്, ഗാഡ്ജറ്റ് അപ്ഡേറ്റുകൾക്കായി ഗാഡ്ജറ്റ് പൾസ് മലയാളം സബ്സ്ക്രൈബ് ചെയ്യാനും ഫോളോ ചെയ്യാനും മറക്കല്ലേ.'"
        )
    )
    description: str = Field(description="Detailed Malayalam YouTube description.")
    tags: List[str] = Field(description="10-12 relevant Malayalam and English YouTube tags.")


def get_today_category() -> str:
    override = os.getenv("LONG_CATEGORY", "auto_by_day").strip()
    if override in SERIES_CONFIG:
        return override
    wd = datetime.now(IST_TZ).weekday()  # Mon=0, Wed=2, Sat=5
    return {0: "top5_budget", 2: "smart_home_guide", 5: "weekly_launches"}.get(wd, "top5_budget")


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"topics": [], "episodes": []}


def save_history(topic: str, video_id: Optional[str], title: str, category: str) -> None:
    hist = load_history()
    hist["topics"] = (hist.get("topics", []) + [topic])[-200:]
    if video_id:
        hist["episodes"] = (hist.get("episodes", []) + [{"id": video_id, "title": title, "category": category}])[-15:]
    HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_plan(category: str) -> tuple[str, LongTechPlan]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY.")
    client = genai.Client(api_key=api_key)
    hist = load_history()
    today_str = datetime.now(IST_TZ).strftime("%B %d, %Y")
    cfg = SERIES_CONFIG[category]

    research_prompt = f"""
Today is {today_str} in India. Series: {cfg['playlist_title']}.
Goal: {cfg['prompt_angle']}
Avoid repeating: {', '.join(hist.get('topics', [])[-20:])}
Use Google Search to gather 5 real, currently relevant products/topics in India with verified INR prices, key specs, and 1-2 official/tech article URLs per product.
"""
    research_text = ""
    for m in [x for x in GEMINI_MODEL_CANDIDATES if x]:
        try:
            r = client.models.generate_content(
                model=m,
                contents=research_prompt,
                config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.4),
            )
            if r.text:
                research_text = r.text
                break
        except Exception as exc:
            print(f"[WARN] Search pass failed on {m}: {exc}")

    if not research_text.strip():
        try:
            rss = requests.get(
                "https://news.google.com/rss/search?q=India+launch+smartphone+OR+earbuds+OR+laptop+OR+smart+TV+price&hl=en-IN&gl=IN&ceid=IN:en",
                timeout=15,
            )
            if rss.ok:
                root = ET.fromstring(rss.content)
                headlines = []
                for item in root.findall(".//item")[:8]:
                    t_el = item.find("title")
                    l_el = item.find("link")
                    if t_el is not None and t_el.text:
                        headlines.append(f"- {t_el.text.strip()} ({(l_el.text or '').strip() if l_el is not None else ''})")
                if headlines:
                    research_text = "Live Indian Tech Headlines:\n" + "\n".join(headlines)
        except Exception as exc:
            print(f"[WARN] Tech RSS fallback failed: {exc}")

    struct_prompt = f"""
Create a complete 5-chapter Malayalam YouTube video plan (~8-9 min) for "Gadget Pulse Malayalam" based ONLY on these verified facts:
{research_text}
Keep Kerala Malayalam warm, conversational, and practical for everyday buyers.
IMPORTANT:
- Keep exact English product/brand names in narration_ml (so on-screen subtitles show proper English).
- In each chapter's pronunciation_pairs, provide 'English Term => Phonetic Malayalam' for every English brand, model, or tech term so the Malayalam voiceover pronounces them accurately.
"""
    for attempt in range(3):
        for m in [x for x in GEMINI_MODEL_CANDIDATES if x]:
            try:
                r = client.models.generate_content(
                    model=m,
                    contents=struct_prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=LongTechPlan, temperature=0.7),
                )
                plan = LongTechPlan.model_validate_json(r.text)
                return plan.topic_key, plan
            except Exception as exc:
                print(f"[WARN] Struct pass failed on {m} (try {attempt + 1}/3): {exc}")
                if any(k in str(exc) for k in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")):
                    time.sleep(10)
        if attempt < 2:
            time.sleep(12 * (attempt + 1))
    raise RuntimeError("All Gemini models failed.")


# ---------------------------------------------------------------------------
# Fonts, Mixed-Script Rendering, BGM & SFX
# ---------------------------------------------------------------------------
def find_malayalam_font() -> str:
    for path in (
        "/usr/share/fonts/truetype/noto/NotoSansMalayalam-Bold.ttf",
        "/usr/share/fonts/truetype/malayalam/Manjari-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansMalayalam-Regular.ttf",
    ):
        if Path(path).exists():
            return path
    return "DejaVuSans-Bold.ttf"


def find_latin_font() -> str:
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if Path(path).exists():
            return path
    return "DejaVuSans-Bold.ttf"


def _split_runs(text: str):
    return [(p, bool(re.fullmatch(r"[A-Za-z]+", p))) for p in re.findall(r"[A-Za-z]+|[^A-Za-z]+", text)]


def measure_mixed(draw: ImageDraw.ImageDraw, text: str, ml_f, lat_f) -> float:
    return sum(draw.textlength(s, font=lat_f if is_lat else ml_f) for s, is_lat in _split_runs(text))


def wrap_mixed_px(draw: ImageDraw.ImageDraw, text: str, ml_f, lat_f, max_w: int) -> List[str]:
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if measure_mixed(draw, t, ml_f, lat_f) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


def draw_mixed_at(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, ml_f, lat_f, fill, anchor_y: str = "t") -> None:
    cx = x
    for seg, is_lat in _split_runs(text):
        fnt = lat_f if is_lat else ml_f
        draw.text((cx, y), seg, font=fnt, fill=fill, anchor=f"l{anchor_y}")
        cx += draw.textlength(seg, font=fnt)


def synthesize_looping_bgm_60s(out_wav: Path, duration_sec: float = 60.0, sr: int = 44100) -> None:
    chords = [(261.63, 329.63, 392.0, 493.88), (220.0, 261.63, 329.63, 392.0), (174.61, 220.0, 261.63, 329.63), (196.0, 246.94, 293.66, 329.63)]
    total = int(duration_sec * sr)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for start in range(0, total, 4096):
            buf = bytearray()
            for i in range(start, min(total, start + 4096)):
                t = i / sr
                freqs = chords[int(t // 5.0) % len(chords)]
                env = math.sin(math.pi * min(1.0, (t % 5.0) / 5.0)) ** 0.45
                val = sum(math.sin(2.0 * math.pi * f * t) * (0.28 if k == 0 else 0.17) for k, f in enumerate(freqs))
                pcm = int(max(-1.0, min(1.0, val * env * 0.30)) * 32767)
                buf.extend(struct.pack("<hh", pcm, pcm))
            wf.writeframes(bytes(buf))


def synthesize_whoosh(out_wav: Path, duration_sec: float = 0.45, sr: int = 44100) -> None:
    rng = random.Random(42)
    prev = 0.0
    total = int(duration_sec * sr)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        buf = bytearray()
        for i in range(total):
            t = i / total
            env = math.sin(math.pi * t) ** 1.5
            prev += (0.08 + 0.25 * math.sin(math.pi * t)) * (rng.uniform(-1.0, 1.0) - prev)
            pcm = int(max(-1.0, min(1.0, prev * env * 0.5)) * 32767)
            buf.extend(struct.pack("<hh", pcm, pcm))
        wf.writeframes(bytes(buf))


# ---------------------------------------------------------------------------
# Visuals: Chapter Cards, Callouts, Official Product Photos & 16:9 Thumbnail
# ---------------------------------------------------------------------------
def render_chapter_card(num: int, ch: GadgetChapter, out_png: Path, font_path: str) -> None:
    img = Image.new("RGB", (1920, 1080), (14, 24, 48))
    d = ImageDraw.Draw(img)
    lat_path = find_latin_font()
    d.rectangle([140, 380, 165, 700], fill=(0, 229, 255))
    f_lbl_ml, f_lbl_lat = ImageFont.truetype(font_path, 42), ImageFont.truetype(lat_path, 42)
    f_tit_ml, f_tit_lat = ImageFont.truetype(font_path, 68), ImageFont.truetype(lat_path, 68)
    draw_mixed_at(d, 200, 390, f"ഭാഗം {num}  •  {ch.price_inr_label}", f_lbl_ml, f_lbl_lat, (250, 204, 21))
    y = 480
    for ln in wrap_mixed_px(d, ch.chapter_title_ml, f_tit_ml, f_tit_lat, 1520)[:2]:
        draw_mixed_at(d, 200, y, ln, f_tit_ml, f_tit_lat, (255, 255, 255))
        y += 92
    d.text((200, 960), "ഗാഡ്ജറ്റ് പൾസ് മലയാളം", font=ImageFont.truetype(font_path, 32), fill=(148, 163, 184))
    img.save(out_png)


def render_callout_overlay(ch: GadgetChapter, out_png: Path, font_path: str) -> None:
    """Renders the product description box at the TOP of the 1920x1080 frame so bottom subtitles never overlap."""
    img = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lat_path = find_latin_font()
    f_h_ml, f_h_lat = ImageFont.truetype(font_path, 32), ImageFont.truetype(lat_path, 32)
    f_b_ml, f_b_lat = ImageFont.truetype(font_path, 36), ImageFont.truetype(lat_path, 36)
    lines = wrap_mixed_px(d, ch.key_fact_ml, f_b_ml, f_b_lat, 1640)[:2]
    y0 = 28
    y1 = y0 + 82 + len(lines) * 48
    d.rounded_rectangle([80, y0, 1840, y1], radius=24, fill=(10, 18, 36, 228), outline=(0, 229, 255, 235), width=4)
    draw_mixed_at(d, 120, y0 + 20, f"{ch.product_name}  ({ch.price_inr_label})", f_h_ml, f_h_lat, (250, 204, 21))
    cy = y0 + 68
    for ln in lines:
        draw_mixed_at(d, 120, cy, ln, f_b_ml, f_b_lat, (255, 255, 255))
        cy += 48
    img.save(out_png)


def scrape_first_product_image(urls: List[str], out_jpg: Path) -> bool:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for url in urls[:3]:
        try:
            r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if not r.ok:
                continue
            for img_url in re.findall(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']', r.text[:150000], flags=re.I)[:2]:
                abs_u = urllib.parse.urljoin(r.url, img_url)
                if any(b in abs_u.lower() for b in ("logo", "favicon", "icon", ".svg")):
                    continue
                ir = requests.get(abs_u, headers=headers, timeout=20)
                if ir.ok and len(ir.content) > 15000:
                    out_jpg.write_bytes(ir.content)
                    with Image.open(out_jpg) as im:
                        if im.width >= 450 and im.height >= 250:
                            im.convert("RGB").save(out_jpg, quality=92)
                            return True
        except Exception:
            pass
    return False


def search_exact_product_image_single(product_name: str, out_jpg: Path) -> bool:
    if not product_name.strip():
        return False
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    q = urllib.parse.quote_plus(f"{product_name} official product image")
    try:
        r = requests.get(f"https://www.bing.com/images/search?q={q}&qft=+filterui:imagesize-large&form=HDRSC2", headers=headers, timeout=15)
        if r.ok:
            urls = re.findall(r'murl(?:&quot;|")\s*:\s*(?:&quot;|")(https?://[^"&]+)', r.text)
            for img_url in urls[:8]:
                if any(b in img_url.lower() for b in ("logo", "favicon", "icon", ".svg")):
                    continue
                try:
                    ir = requests.get(img_url, headers=headers, timeout=15)
                    if ir.ok and len(ir.content) > 18000:
                        out_jpg.write_bytes(ir.content)
                        with Image.open(out_jpg) as im:
                            if im.width >= 450 and im.height >= 250:
                                im.convert("RGB").save(out_jpg, quality=92)
                                return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def render_dynamic_product_16x9(img_path: Path, out_mp4: Path, duration_sec: float = 6.0, style_idx: int = 0) -> None:
    """Renders 1920x1080 30fps dynamic fly-in + zoom + swipe-out animation for product photos."""
    fps = 30
    total_frames = max(60, int(round(duration_sec * fps)))
    W, H = 1920, 1080
    cx, cy = 960, 535  # Center stage cleanly between top product box and bottom subtitles
    bg = Image.new("RGB", (W, H), (9, 16, 34))
    bg_d = ImageDraw.Draw(bg)
    for gx in range(0, W, 120):
        bg_d.line([(gx, 0), (gx, H)], fill=(16, 28, 56), width=2)
    for gy in range(0, H, 120):
        bg_d.line([(0, gy), (W, gy)], fill=(16, 28, 56), width=2)

    prod = ImageEnhance.Color(Image.open(img_path).convert("RGB")).enhance(1.18)
    base_scale = min(920 / prod.width, 520 / prod.height)
    bw, bh = max(240, int(prod.width * base_scale)), max(200, int(prod.height * base_scale))
    prod_base = prod.resize((bw, bh), Image.Resampling.LANCZOS)
    accent = (0, 229, 255) if style_idx % 2 == 0 else (250, 204, 21)
    mode = style_idx % 4

    cmd = [
        "ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{W}x{H}", "-pix_fmt", "rgb24", "-r", str(fps), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(out_mp4),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    try:
        for f in range(total_frames):
            p = f / max(1, total_frames - 1)
            t = f / fps
            frame = bg.copy()
            d = ImageDraw.Draw(frame)
            ring_r = 280 + int(16 * math.sin(t * 3.5))
            d.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r], outline=accent, width=3)

            dx, dy, scale_f = 0, 0, 1.0
            if p < 0.16:
                ep = 1.0 - (1.0 - p / 0.16) ** 3
                dx = int((1.0 - ep) * (1200 if mode % 2 == 0 else -1200))
                scale_f = 0.85 + 0.15 * ep
            elif p > 0.85:
                sp = ((p - 0.85) / 0.15) ** 2
                dx = int((-1300 if mode % 2 == 0 else 1300) * sp)
            else:
                mid_p = (p - 0.16) / 0.69
                scale_f = 1.0 + 0.10 * mid_p
                dy = int(12 * math.sin(t * 3.0))

            nw, nh = max(140, int(bw * scale_f)), max(120, int(bh * scale_f))
            p_res = prod_base.resize((nw, nh), Image.Resampling.BILINEAR)
            px, py = cx + dx - nw // 2, cy + dy - nh // 2
            d.rounded_rectangle([px - 14, py - 14, px + nw + 14, py + nh + 14], radius=24, fill=(18, 30, 58), outline=accent, width=4)
            frame.paste(p_res, (px, py))
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("16:9 dynamic product animation failed")


def fetch_clip_or_photo(query: str, urls: List[str], out_mp4: Path, out_photo_opt: Optional[Path] = None, product_name: str = "") -> bool:
    photo_tmp = out_photo_opt or out_mp4.with_suffix(".jpg")
    got_photo = (urls and scrape_first_product_image(urls, photo_tmp)) or (product_name and search_exact_product_image_single(product_name, photo_tmp))
    if got_photo:
        render_dynamic_product_16x9(photo_tmp, out_mp4, duration_sec=6.0, style_idx=abs(hash(product_name or query)) % 4)
        return True

    if PIXABAY_API_KEY:
        try:
            r = requests.get("https://pixabay.com/api/videos/", params={"key": PIXABAY_API_KEY, "q": query, "per_page": 6, "safesearch": "true"}, timeout=20)
            q_words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query)]
            for h in r.json().get("hits", []):
                tags = h.get("tags", "").lower()
                if q_words and tags and not any(w in tags for w in q_words):
                    continue
                vids = h.get("videos", {})
                u = (vids.get("large") or vids.get("medium") or {}).get("url")
                if u:
                    out_mp4.write_bytes(requests.get(u, timeout=60).content)
                    return True
        except Exception:
            pass
    return False


def render_thumbnail_16x9(subject_jpg: Optional[Path], fallback_clip: Path, hook_ml: str, out_jpg: Path, font_path: str) -> None:
    raw = WORK_DIR / "thumb_raw.jpg"
    if subject_jpg and subject_jpg.exists():
        raw.write_bytes(subject_jpg.read_bytes())
    else:
        subprocess.run(["ffmpeg", "-y", "-ss", "1.5", "-i", str(fallback_clip), "-vframes", "1", str(raw)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    src = ImageEnhance.Contrast(ImageEnhance.Color(Image.open(raw).convert("RGB")).enhance(1.42)).enhance(1.2)
    W, H = 1280, 720
    sc = max(W / src.width, H / src.height)
    res = src.resize((int(src.width * sc), int(src.height * sc)), Image.Resampling.LANCZOS)
    bg = res.crop(((res.width - W) // 2, (res.height - H) // 2, (res.width + W) // 2, (res.height + H) // 2)).convert("RGBA")

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for x in range(W):
        od.line([(x, 0), (x, H)], fill=(12, 18, 46, int(235 * max(0.25, 1.0 - x / (W * 0.88)))))
    canvas = Image.alpha_composite(bg, ov).convert("RGB")
    d = ImageDraw.Draw(canvas)
    lat_path = find_latin_font()

    d.rectangle([8, 8, W - 9, H - 9], outline=(0, 229, 255), width=12)
    d.rounded_rectangle([48, 42, 470, 118], radius=36, fill=(239, 68, 68), outline=(255, 255, 255), width=4)
    d.text((259, 80), "വാങ്ങുന്നതിന് മുമ്പ് കാണൂ!", font=ImageFont.truetype(font_path, 34), fill=(255, 255, 255), anchor="mm")

    f_ml, f_lat = ImageFont.truetype(font_path, 62), ImageFont.truetype(lat_path, 62)
    lines = wrap_mixed_px(d, hook_ml, f_ml, f_lat, 800)[:3]
    y = 220 if len(lines) == 3 else 270
    for ln in lines:
        draw_mixed_at(d, 54, y + 5, ln, f_ml, f_lat, (0, 0, 0))
        draw_mixed_at(d, 50, y, ln, f_ml, f_lat, (255, 235, 59))
        y += 88

    d.rounded_rectangle([48, 616, 460, 672], radius=24, fill=(0, 180, 216))
    d.text((254, 644), "ഗാഡ്ജറ്റ് പൾസ് മലയാളം", font=ImageFont.truetype(font_path, 30), fill=(255, 255, 255), anchor="mm")
    canvas.save(out_jpg, quality=95)


# ---------------------------------------------------------------------------
# Video Build & Publishing
# ---------------------------------------------------------------------------

def _srt_ts_to_sec(ts: str) -> float:
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _sec_to_ass_ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def srt_to_karaoke_ass(srt_path: Path, ass_path: Path) -> None:
    """Converts edge-tts SRT into 1080p bottom ASS Karaoke subtitles with yellow word sweep."""
    raw = srt_path.read_text(encoding="utf-8", errors="ignore").strip() if srt_path.exists() else ""
    cues: List[Tuple[float, float, str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", raw):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            s_t, e_t = [x.strip() for x in lines[1].split("-->")[:2]]
            cues.append((_srt_ts_to_sec(s_t), _srt_ts_to_sec(e_t), " ".join(lines[2:])))
    # Merge short word cues into readable 5-7 word phrases
    merged: List[Tuple[float, float, str]] = []
    buf_w: List[str] = []
    b_s, b_e = 0.0, 0.0
    for st, en, txt in cues:
        words = txt.split()
        if not buf_w:
            b_s = st
        if len(buf_w) + len(words) > 6:
            merged.append((b_s, b_e, " ".join(buf_w)))
            buf_w = list(words)
            b_s, b_e = st, en
        else:
            buf_w.extend(words)
            b_e = en
    if buf_w:
        merged.append((b_s, b_e, " ".join(buf_w)))

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Noto Sans Malayalam,50,&H0015CCFA,&H00FFFFFF,&H0022100A,&HD822100A,-1,0,0,0,100,100,0,0,3,12,0,2,120,120,52,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev_lines = []
    for st, en, txt in merged:
        dur_cs = max(20, int(round((en - st) * 100)))
        words = txt.split()
        if not words:
            continue
        per_cs = max(8, dur_cs // len(words))
        k_txt = " ".join(f"{{\\kf{per_cs}}}{w}" for w in words)
        ev_lines.append(f"Dialogue: 0,{_sec_to_ass_ts(st)},{_sec_to_ass_ts(en)},Karaoke,,0,0,0,,{k_txt}")
    ass_path.write_text(header + "\n".join(ev_lines) + "\n", encoding="utf-8")


COMMON_TECH_PRONUNCIATIONS: Dict[str, str] = {
    "iQOO": "ഐക്യൂ",
    "OnePlus": "വൺപ്ലസ്",
    "Realme": "റിയൽമി",
    "Redmi": "റെഡ്മി",
    "Xiaomi": "ഷവോമി",
    "Samsung": "സാംസങ്",
    "Motorola": "മോട്ടറോള",
    "Nothing": "നത്തിങ്",
    "POCO": "പോക്കോ",
    "Vivo": "വിവോ",
    "OPPO": "ഓപ്പോ",
    "iPhone": "ഐഫോൺ",
    "iPad": "ഐപാഡ്",
    "MacBook": "മാക്ബുക്ക്",
    "AirPods": "എയർപോഡ്സ്",
    "boAt": "ബോട്ട്",
    "CMF": "സി എം എഫ്",
    "JBL": "ജെ ബി എൽ",
    "Sony": "സോണി",
    "ASUS": "അസൂസ്",
    "Lenovo": "ലെനോവോ",
    "AMOLED": "അമോലെഡ്",
    "QLED": "ക്യുലെഡ്",
    "OLED": "ഒലെഡ്",
    "TWS": "ടി ഡബ്ല്യു എസ്",
    "ANC": "എ എൻ സി",
    "mAh": "എം എ എച്ച്",
    "5G": "ഫൈവ് ജി",
    "4K": "ഫോർ കെ",
    "USB-C": "യു എസ് ബി സി",
    "Wi-Fi": "വൈ ഫൈ",
    "HDMI": "എച്ച് ഡി എം ഐ",
}


def build_pronunciation_map(extra_pairs: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    mapping: Dict[str, str] = dict(COMMON_TECH_PRONUNCIATIONS)
    for item in extra_pairs or []:
        if "=>" in item:
            eng, ml = [p.strip() for p in item.split("=>", 1)]
            if eng and ml:
                mapping[eng] = ml
    return sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True)


def prepare_text_for_tts(text: str, pairs: List[Tuple[str, str]]) -> str:
    out = text
    for eng, ml in pairs:
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(eng)}(?![A-Za-z0-9])", flags=re.IGNORECASE)
        out = pattern.sub(ml, out)
    return out


def restore_english_in_srt(srt_path: Path, pairs: List[Tuple[str, str]]) -> None:
    if not srt_path.exists():
        return
    txt = srt_path.read_text(encoding="utf-8", errors="ignore")
    for eng, ml in sorted(pairs, key=lambda kv: len(kv[1]), reverse=True):
        txt = txt.replace(ml, eng)
    srt_path.write_text(txt, encoding="utf-8")


def tts_to_mp3(text: str, out_mp3: Path, out_srt: Optional[Path] = None) -> float:
    cmd = ["edge-tts", "--voice", TTS_VOICE, "--rate=-3%", "--pitch=-1Hz", "--text", text, "--write-media", str(out_mp3)]
    if out_srt is not None:
        cmd.extend(["--write-subtitles", str(out_srt)])
    subprocess.run(cmd, check=True)
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(out_mp3)], text=True).strip())


def build_long_video(plan: LongTechPlan) -> tuple[Path, Path, str]:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    font_path = find_malayalam_font()
    whoosh_wav, bgm_wav = WORK_DIR / "whoosh.wav", WORK_DIR / "bgm_60s.wav"
    synthesize_whoosh(whoosh_wav)
    synthesize_looping_bgm_60s(bgm_wav)

    lead_clips, lead_photo = [], WORK_DIR / "lead_thumb_photo.jpg"
    for idx, ch in enumerate(plan.chapters, start=1):
        c_mp4 = WORK_DIR / f"ch_{idx}_clip_0.mp4"
        if fetch_clip_or_photo(ch.search_queries[0], ch.official_page_urls, c_mp4, lead_photo if idx == 1 else None, product_name=ch.product_name):
            lead_clips.append(c_mp4)
    if not lead_clips:
        c_mp4 = WORK_DIR / "fallback.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=#0B162C:s=1920x1080:r=30:d=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(c_mp4)], check=True)
        lead_clips.append(c_mp4)

    segments, chapter_ts, elapsed = [], [], 0.0

    # 5-clip Intro Montage
    intro_mp3 = WORK_DIR / "intro.mp3"
    all_pairs = build_pronunciation_map([p for ch in plan.chapters for p in ch.pronunciation_pairs])
    intro_dur = tts_to_mp3(prepare_text_for_tts(plan.intro_narration_ml, all_pairs), intro_mp3)
    per_i = max(2.5, intro_dur / len(lead_clips))
    i_trims = []
    for k, lc in enumerate(lead_clips):
        it = WORK_DIR / f"intro_t_{k}.mp4"
        subprocess.run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(lc), "-t", f"{per_i:.2f}", "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", str(it)], check=True)
        i_trims.append(it)
    (WORK_DIR / "ic.txt").write_text("\n".join(f"file '{p.name}'" for p in i_trims), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(WORK_DIR / "ic.txt"), "-c", "copy", str(WORK_DIR / "iraw.mp4")], check=True)
    intro_seg = WORK_DIR / "seg_intro.mp4"
    subprocess.run(["ffmpeg", "-y", "-i", str(WORK_DIR / "iraw.mp4"), "-i", str(intro_mp3), "-map", "0:v:0", "-map", "1:a:0", "-t", f"{intro_dur:.2f}", "-c:v", "copy", "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k", "-shortest", str(intro_seg)], check=True)
    segments.append(intro_seg)
    elapsed += intro_dur

    for idx, ch in enumerate(plan.chapters, start=1):
        chapter_ts.append(f"{int(elapsed//60):02d}:{int(elapsed%60):02d} {ch.chapter_title_ml}")
        card_png, card_mp4 = WORK_DIR / f"card_{idx}.png", WORK_DIR / f"seg_card_{idx}.mp4"
        render_chapter_card(idx, ch, card_png, font_path)
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", str(card_png), "-i", str(whoosh_wav), "-filter_complex", "[1:a]apad=whole_dur=2.5,volume=0.35[aout]", "-map", "0:v:0", "-map", "[aout]", "-t", "2.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k", "-shortest", str(card_mp4)], check=True)
        segments.append(card_mp4)
        elapsed += 2.5

        ch_mp3 = WORK_DIR / f"ch_{idx}.mp3"
        ch_srt = WORK_DIR / f"ch_{idx}.srt"
        ch_ass = WORK_DIR / f"ch_{idx}.ass"
        ch_pairs = build_pronunciation_map(ch.pronunciation_pairs)
        ch_dur = tts_to_mp3(prepare_text_for_tts(ch.narration_ml, ch_pairs), ch_mp3, ch_srt)
        restore_english_in_srt(ch_srt, ch_pairs)
        srt_to_karaoke_ass(ch_srt, ch_ass)
        ch_clips = [WORK_DIR / f"ch_{idx}_clip_0.mp4"] if (WORK_DIR / f"ch_{idx}_clip_0.mp4").exists() else []
        for q_i, q in enumerate(ch.search_queries[1:3], start=1):
            cp = WORK_DIR / f"ch_{idx}_clip_{q_i}.mp4"
            if fetch_clip_or_photo(q, [], cp):
                ch_clips.append(cp)
        if not ch_clips:
            ch_clips = [lead_clips[0]]

        trims = []
        per_c = ch_dur / len(ch_clips)
        for c_i, cp in enumerate(ch_clips):
            tp = WORK_DIR / f"ch_{idx}_tr_{c_i}.mp4"
            subprocess.run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(cp), "-t", f"{per_c:.2f}", "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", str(tp)], check=True)
            trims.append(tp)
        (WORK_DIR / f"cc_{idx}.txt").write_text("\n".join(f"file '{p.name}'" for p in trims), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(WORK_DIR / f"cc_{idx}.txt"), "-c", "copy", str(WORK_DIR / f"craw_{idx}.mp4")], check=True)

        ov_png = WORK_DIR / f"ov_{idx}.png"
        render_callout_overlay(ch, ov_png, font_path)
        ch_seg = WORK_DIR / f"seg_ch_{idx}.mp4"
        subprocess.run(["ffmpeg", "-y", "-i", str(WORK_DIR / f"craw_{idx}.mp4"), "-i", str(ch_mp3), "-i", str(ov_png), "-filter_complex", f"[0:v][2:v]overlay=0:0,ass={ch_ass.as_posix()}[vout]", "-map", "[vout]", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k", "-shortest", str(ch_seg)], check=True)
        segments.append(ch_seg)
        elapsed += ch_dur

    outro_mp3 = WORK_DIR / "outro.mp3"
    outro_dur = tts_to_mp3(prepare_text_for_tts(plan.outro_narration_ml, all_pairs), outro_mp3)
    outro_seg = WORK_DIR / "seg_outro.mp4"
    subprocess.run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(lead_clips[0]), "-i", str(outro_mp3), "-t", f"{outro_dur:.2f}", "-map", "0:v:0", "-map", "1:a:0", "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k", "-shortest", str(outro_seg)], check=True)
    segments.append(outro_seg)

    (WORK_DIR / "fc.txt").write_text("\n".join(f"file '{p.name}'" for p in segments), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(WORK_DIR / "fc.txt"), "-c", "copy", str(WORK_DIR / "vo_all.mp4")], check=True)
    final_mp4 = WORK_DIR / "long_video.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(WORK_DIR / "vo_all.mp4"), "-i", str(bgm_wav),
            "-filter_complex", "[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume=1.0[vo];[1:a]aloop=loop=-1:size=2646000,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume=0.08[bgm];[vo][bgm]amix=inputs=2:duration=first:normalize=0[aout]",
            "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k", "-movflags", "+faststart", str(final_mp4),
        ],
        check=True,
    )
    thumb_jpg = WORK_DIR / "long_thumb.jpg"
    render_thumbnail_16x9(lead_photo if lead_photo.exists() else None, lead_clips[0], plan.thumbnail_hook_ml, thumb_jpg, font_path)
    return final_mp4, thumb_jpg, "Chapters:\n00:00 Intro\n" + "\n".join(chapter_ts)


def build_affiliate_desc(plan: LongTechPlan) -> str:
    amz_tag = os.getenv("AMAZON_AFFILIATE_TAG", "").strip()
    fk_id = os.getenv("FLIPKART_AFFILIATE_ID", "").strip()
    lines = ["\n🛒 Featured Product Links:"]
    added = False
    for ch in plan.chapters:
        if ch.available_in_india:
            added = True
            q = urllib.parse.quote_plus(ch.product_name)
            amz_u = f"https://www.amazon.in/s?k={q}" + (f"&tag={amz_tag}" if amz_tag else "")
            fk_u = f"https://www.flipkart.com/search?q={q}" + (f"&affid={fk_id}" if fk_id else "")
            lines.append(f"• {ch.product_name} ({ch.price_inr_label}): Amazon {amz_u} | Flipkart {fk_u}")
    return ("\n".join(lines) + "\n") if added else ""



def post_first_comment_safe(youtube, video_id: str, comment_text: str) -> None:
    if not comment_text.strip():
        return
    try:
        youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": comment_text.strip(),
                        }
                    },
                }
            },
        ).execute()
        print("[SUCCESS] Posted affiliate first comment on YouTube video!")
    except Exception as exc:
        print(f"[INFO] YouTube first comment skipped: {exc}")

def upload_long_to_youtube(video_path: Path, thumb_jpg: Path, plan: LongTechPlan, chapters_text: str, category: str) -> str:
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"].strip(),
        client_secret=os.environ["YT_CLIENT_SECRET"].strip(),
    )
    yt = build("youtube", "v3", credentials=creds)
    status_obj = {"privacyStatus": PRIVACY_STATUS, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False}
    if PRIVACY_STATUS == "private":
        ist_now = datetime.now(IST_TZ)
        target_ist = ist_now.replace(hour=21, minute=0, second=0, microsecond=0)  # 9:00 PM IST
        if ist_now >= target_ist - timedelta(minutes=10):
            target_ist = ist_now + timedelta(minutes=60)
        status_obj["publishAt"] = target_ist.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    full_desc = f"{plan.description}\n\n{chapters_text}{build_affiliate_desc(plan)}"
    req = yt.videos().insert(
        part="snippet,status",
        body={
            "snippet": {"title": plan.title[:100], "description": full_desc[:4900], "tags": plan.tags, "categoryId": CATEGORY_ID, "defaultLanguage": "ml", "defaultAudioLanguage": "ml"},
            "status": status_obj,
        },
        media_body=MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=5 * 1024 * 1024, resumable=True),
    )
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    print(f"[SUCCESS] Uploaded Long Video: https://www.youtube.com/watch?v={vid}")

    for _ in range(20):
        try:
            vinfo = yt.videos().list(part="processingDetails", id=vid).execute()
            if vinfo.get("items", [{}])[0].get("processingDetails", {}).get("processingStatus") == "succeeded":
                import time
                time.sleep(10)
                break
        except Exception:
            pass
        import time
        time.sleep(15)

    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb_jpg), mimetype="image/jpeg")).execute()
    except Exception as exc:
        print(f"[WARN] Thumbnail skipped: {exc}")

    try:
        pl_title = SERIES_CONFIG[category]["playlist_title"]
        pls = yt.playlists().list(part="snippet", mine=True, maxResults=50).execute()
        pl_id = next((i["id"] for i in pls.get("items", []) if i["snippet"]["title"].strip().lower() == pl_title.lower()), None)
        if not pl_id:
            pl_id = yt.playlists().insert(part="snippet,status", body={"snippet": {"title": pl_title, "description": pl_title}, "status": {"privacyStatus": "public"}}).execute()["id"]
        yt.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": pl_id, "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
    except Exception as exc:
        print(f"[INFO] Playlist skipped: {exc}")
    aff_block = build_affiliate_desc(plan).strip()
    if aff_block:
        post_first_comment_safe(yt, vid, aff_block)
    return vid


def upload_long_to_facebook(video_path: Path, title: str, description: str) -> None:
    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    page_id = os.getenv("FB_PAGE_ID", "").strip()
    if not (token and page_id):
        return
    try:
        pt = requests.get(f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}", params={"fields": "access_token", "access_token": token}, timeout=20)
        if pt.ok and pt.json().get("access_token"):
            token = pt.json()["access_token"]
    except Exception:
        pass

    base_url = f"https://graph-video.facebook.com/{GRAPH_API_VERSION}/{page_id}/videos"
    fsize = video_path.stat().st_size
    s_resp = requests.post(base_url, data={"upload_phase": "start", "file_size": str(fsize), "access_token": token}, timeout=60)
    if not s_resp.ok:
        print(f"[ERROR] Facebook Video start HTTP {s_resp.status_code}: {s_resp.text}")
        s_resp.raise_for_status()
    s_data = s_resp.json()
    if "upload_session_id" not in s_data:
        raise RuntimeError(f"Facebook Video start missing upload_session_id: {s_data}")
    session_id = s_data["upload_session_id"]
    start_off, end_off = int(s_data["start_offset"]), int(s_data["end_offset"])
    with open(video_path, "rb") as vf:
        while start_off < fsize:
            vf.seek(start_off)
            chunk = vf.read(end_off - start_off)
            tr = requests.post(base_url, data={"upload_phase": "transfer", "upload_session_id": session_id, "start_offset": str(start_off), "access_token": token}, files={"video_file_chunk": ("chunk.mp4", chunk, "application/octet-stream")}, timeout=300).json()
            start_off, end_off = int(tr["start_offset"]), int(tr["end_offset"])
            if start_off == end_off:
                break
    thumb_jpg = WORK_DIR / "long_thumb.jpg"
    fin_data = {"upload_phase": "finish", "upload_session_id": session_id, "title": title[:100], "description": description[:4900], "access_token": token}
    ist_now = datetime.now(IST_TZ)
    target_ist = ist_now.replace(hour=21, minute=0, second=0, microsecond=0)
    if ist_now <= target_ist - timedelta(minutes=12):
        fin_data["published"] = "false"
        fin_data["scheduled_publish_time"] = str(int(target_ist.timestamp()))
        print(f"[INFO] Scheduling Facebook Long Video for 9:00 PM IST ({target_ist.isoformat()})!")
    else:
        fin_data["published"] = "true"
    if thumb_jpg.exists():
        with open(thumb_jpg, "rb") as tf:
            requests.post(base_url, data=fin_data, files={"thumb": ("long_thumb.jpg", tf, "image/jpeg")}, timeout=120).raise_for_status()
    else:
        requests.post(base_url, data=fin_data, timeout=120).raise_for_status()
    print("[SUCCESS] Published Long Video to Facebook Page!")


def main() -> None:
    today_ist = datetime.now(IST_TZ).strftime("%Y-%m-%d")
    if "--publish-fb-only" in sys.argv:
        vid = WORK_DIR / "long_video.mp4"
        if vid.exists() and META_CACHE_FILE.exists():
            m = json.loads(META_CACHE_FILE.read_text(encoding="utf-8"))
            if m.get("date_ist") and m.get("date_ist") != today_ist:
                print(f"[SKIP] Cached FB Long Video is from {m.get('date_ist')} (expected {today_ist}); skipping stale video.")
                return
            upload_long_to_facebook(vid, m["title"], m["description"])
        return

    cat = get_today_category()
    topic_key, plan = generate_plan(cat)
    vid_path, thumb_path, chapters_txt = build_long_video(plan)
    META_CACHE_FILE.write_text(json.dumps({"date_ist": today_ist, "title": plan.title, "description": f"{plan.description}\n\n{chapters_txt}{build_affiliate_desc(plan)}"}, ensure_ascii=False), encoding="utf-8")
    yt_id = upload_long_to_youtube(vid_path, thumb_path, plan, chapters_txt, cat)
    save_history(topic_key, yt_id, plan.title, cat)


if __name__ == "__main__":
    main()