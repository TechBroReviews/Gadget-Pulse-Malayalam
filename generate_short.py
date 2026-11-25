#!/usr/bin/env python3
"""
Gadget Pulse Malayalam — Daily Automated Tech, Gadgets & Smart Home Shorts/Reels Generator
Features:
  - Google Search Grounded Discovery: Finds today's latest Indian smartphone, earbuds,
    laptop, or smart home gadget launch (or useful tech tip) with real India INR prices.
  - Hybrid Visuals: Scrapes official product/launch images (og:image / product photos)
    for specific devices and turns them into smooth Ken Burns zoom clips, falling back
    to tag-filtered Pixabay clips/photos + 2D Animated Spec Cards.
  - Mixed Malayalam + English Font Engine: Zero tofu boxes, zero text overflow.
  - Platform-Specific Affiliate CTA Outro:
      * If product is available in India -> YouTube gets "Link in description & comment"
        outro + Amazon/Flipkart search links; IG & FB Reels get "Comment LINK for DM" outro.
      * Otherwise -> standard Gadget Pulse Malayalam follow/subscribe outro.
"""

import asyncio
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
from typing import List, Optional, Tuple

import edge_tts
import requests
from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GEMINI_MODEL_CANDIDATES = [
    os.getenv("GEMINI_MODEL", "").strip(),
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

EVERGREEN_TECH_TOPICS = [
    "Best Wireless Earbuds Under 2000 Rupees in India Right Now",
    "How Robotic Vacuum Cleaners Map Your Home Using LiDAR",
    "Inverter AC vs Non-Inverter AC: Which Saves More Electricity in Kerala?",
    "Top Load vs Front Load Washing Machine: Real Water & Power Difference",
    "OLED vs QLED Smart TV: Which Display Is Better for Bright Living Rooms?",
    "How Fast Charging Works and Whether Overnight Charging Hurts Your Phone",
]

OUTRO_STANDARD_ML = (
    "ഇത്തരം പുതിയ ഗാഡ്ജറ്റ് വിശേഷങ്ങൾക്കും ടെക് അറിവുകൾക്കുമായി "
    "ഗാഡ്ജറ്റ് പൾസ് മലയാളം ഫോളോ ചെയ്യാനും സബ്സ്ക്രൈബ് ചെയ്യാനും മറക്കല്ലേ."
)
OUTRO_YT_LINK_ML = (
    "ഈ പ്രോഡക്റ്റിന്റെ ബെസ്റ്റ് ബൈ ലിങ്ക് താഴെ ഡിസ്ക്രിപ്ഷനിലും കമന്റിലും നൽകിയിട്ടുണ്ട്. "
    "കൂടുതൽ ടെക് അപ്ഡേറ്റുകൾക്കായി ഗാഡ്ജറ്റ് പൾസ് മലയാളം സബ്സ്ക്രൈബ് ചെയ്യൂ."
)
OUTRO_META_LINK_ML = (
    "ഈ പ്രോഡക്റ്റിന്റെ ബെസ്റ്റ് ബൈ ലിങ്ക് താഴെ ക്യാപ്ഷനിലും കമന്റിലും നൽകിയിട്ടുണ്ട്. "
    "കൂടുതൽ ടെക് അപ്ഡേറ്റുകൾക്കായി ഗാഡ്ജറ്റ് പൾസ് മലയാളം ഫോളോ ചെയ്യാൻ മറക്കല്ലേ."
)

WATERMARK_TEXT = "Gadget Pulse Malayalam"
HISTORY_FILE = Path("used_topics.json")
WORK_DIR = Path("build_short")
META_CACHE_FILE = WORK_DIR / "meta_payload.json"
VOICE_NAME = os.getenv("TTS_VOICE", "ml-IN-MidhunNeural")
PRIVACY_STATUS = os.getenv("YT_PRIVACY_STATUS", "private")
SKIP_UPLOAD = os.getenv("SKIP_UPLOAD", "false").lower() == "true"
CATEGORY_ID = os.getenv("YT_CATEGORY_ID", "28")
GRAPH_API_VERSION = "v21.0"
IST_TZ = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Structured Output Schema
# ---------------------------------------------------------------------------
class TechShortPlan(BaseModel):
    topic_key: str = Field(description="Concise unique English identifier for this device/topic (for deduplication).")
    product_name: str = Field(description="Exact product name if specific device (e.g. 'Nothing Phone 3a'), else general topic.")
    is_specific_product: bool = Field(description="True if covering a specific phone, earbuds, laptop, or appliance; False for general tips.")
    available_in_india: bool = Field(description="True if currently available to buy on Amazon.in / Flipkart, False if upcoming/rumored or general tip.")
    price_inr_label: str = Field(description="Short Indian price label (e.g. '₹24,999' or '₹1,999' or 'Tech Guide').")
    spec_highlights_ml: List[str] = Field(
        description="Exactly 3 crisp spec/feature lines (3-6 words each, Malayalam or Malayalam+English) for the 2D Spec Card."
    )
    animation_scene: str = Field(
        description="Best 2D animation type from: 'spec_card', 'battery_heat', 'sound_waves', 'comparison_bars', 'light_optics'."
    )
    title: str = Field(description="Catchy Malayalam + English YouTube Shorts title under 70 chars with #Shorts #Malayalam.")
    hook_headline_ml: str = Field(description="4-to-7 word Malayalam headline for the top video bar.")
    thumbnail_question_ml: str = Field(description="Punchy 3-to-5 word Malayalam curiosity hook for the thumbnail.")
    description: str = Field(description="Engaging Malayalam + English YouTube description + 5-8 hashtags.")
    reels_caption_ml: str = Field(
        description="Instagram & Facebook Reels caption in Malayalam + English (do NOT ask users to comment LINK for DM) + 10-12 hashtags including #GadgetPulseMalayalam #MalayalamTech #KeralaTech."
    )
    tags: List[str] = Field(description="6 to 10 relevant Malayalam and English SEO keywords without #.")
    narration_malayalam: str = Field(
        description="110 to 135 words of natural spoken Kerala Malayalam script (excluding outro). Short sentences, commas for pauses, no markdown or stage directions."
    )
    product_page_urls: List[str] = Field(
        description="1-3 official brand or tech news article URLs from search results for this exact device (empty list if general topic)."
    )
    search_queries: List[str] = Field(
        description="Exactly 4 literal 2-to-3 word ENGLISH visual search terms (no abbreviations like TWS/ANC/OLED; e.g. 'wireless earbuds charging', 'smartphone camera lens')."
    )


# ---------------------------------------------------------------------------
# Helpers: History, YouTube Auth, Mixed-Script Fonts
# ---------------------------------------------------------------------------
def load_used_topics() -> List[str]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def save_used_topic(topic_id: str, used: List[str]) -> None:
    used.append(topic_id)
    HISTORY_FILE.write_text(json.dumps(used[-500:], indent=2), encoding="utf-8")


def get_youtube_service():
    for var in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
        if not os.getenv(var, "").strip():
            raise RuntimeError(f"Missing GitHub Secret '{var}'.")
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"].strip(),
        client_secret=os.environ["YT_CLIENT_SECRET"].strip(),
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ],
    )
    return build("youtube", "v3", credentials=creds)


def find_malayalam_font() -> str:
    for path in (
        "/usr/share/fonts/truetype/noto/NotoSansMalayalam-Bold.ttf",
        "/usr/share/fonts/truetype/malayalam/Manjari-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansMalayalam-Regular.ttf",
        "/usr/share/fonts/truetype/smc/Manjari-Bold.ttf",
    ):
        if Path(path).exists():
            return path
    return "DejaVuSans-Bold.ttf"


def find_latin_font() -> str:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        if Path(path).exists():
            return path
    return "DejaVuSans-Bold.ttf"


def _split_script_runs(text: str) -> List[Tuple[str, bool]]:
    return [(part, bool(re.fullmatch(r"[A-Za-z]+", part))) for part in re.findall(r"[A-Za-z]+|[^A-Za-z]+", text)]


def measure_mixed_width(draw: ImageDraw.ImageDraw, text: str, ml_f: ImageFont.FreeTypeFont, lat_f: ImageFont.FreeTypeFont) -> float:
    return sum(draw.textlength(seg, font=lat_f if is_lat else ml_f) for seg, is_lat in _split_script_runs(text))


def wrap_mixed_text_px(
    draw: ImageDraw.ImageDraw, text: str, ml_f: ImageFont.FreeTypeFont, lat_f: ImageFont.FreeTypeFont, max_w: int
) -> List[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if measure_mixed_width(draw, test, ml_f, lat_f) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


def draw_mixed_centered_lines(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    center_y: int,
    lines: List[str],
    ml_f: ImageFont.FreeTypeFont,
    lat_f: ImageFont.FreeTypeFont,
    fill: Tuple[int, ...],
    line_spacing: int = 14,
) -> None:
    line_h = ml_f.size + line_spacing
    total_h = len(lines) * line_h - line_spacing
    cur_y = center_y - total_h // 2 + ml_f.size // 2
    for ln in lines:
        w = measure_mixed_width(draw, ln, ml_f, lat_f)
        cur_x = center_x - w / 2.0
        for seg, is_lat in _split_script_runs(ln):
            fnt = lat_f if is_lat else ml_f
            draw.text((cur_x, cur_y), seg, font=fnt, fill=fill, anchor="lm")
            cur_x += draw.textlength(seg, font=fnt)
        cur_y += line_h


# ---------------------------------------------------------------------------
# Step 1: Google Search Grounded Indian Tech Launch Discovery + Script
# ---------------------------------------------------------------------------
def generate_tech_short_plan(used_topics: List[str]) -> TechShortPlan:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing GitHub Secret 'GEMINI_API_KEY'.")
    client = genai.Client(api_key=api_key)
    today_ist = datetime.now(IST_TZ).strftime("%B %d, %Y")
    recent_avoid = ", ".join(used_topics[-30:]) if used_topics else "None"

    # Pass 1: Search live Indian tech launches & gadget news
    research_prompt = f"""
Today is {today_ist} in India.
Search for the most interesting NEW gadget launch, upcoming smartphone/earbuds/laptop announcement, OR useful smart home appliance (robotic vacuum, smart TV, fridge, washing machine) trending in India right now.
Avoid repeating these recently covered topics: {recent_avoid}

Find:
1. Exact product name, launch status (available now in India vs upcoming), and verified India price in INR (₹).
2. 3 key verified specs/features (no guessed numbers!).
3. 1 to 3 official brand or tech news article URLs from your search results so we can fetch official product images.
"""
    research_notes = ""
    for model_name in [m for m in GEMINI_MODEL_CANDIDATES if m]:
        try:
            r1 = client.models.generate_content(
                model=model_name,
                contents=research_prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.4,
                ),
            )
            research_notes = r1.text or ""
            if research_notes.strip():
                break
        except Exception as exc:
            print(f"[WARN] Search pass failed on {model_name}: {exc}")

    if not research_notes.strip():
        # Live Indian tech news RSS fallback if Gemini Search tool hits 429
        try:
            rss = requests.get(
                "https://news.google.com/rss/search?q=India+launch+smartphone+OR+earbuds+OR+laptop+price&hl=en-IN&gl=IN&ceid=IN:en",
                timeout=15,
            )
            if rss.ok:
                root = ET.fromstring(rss.content)
                for item in root.findall(".//item")[:10]:
                    t_el = item.find("title")
                    l_el = item.find("link")
                    t_txt = (t_el.text or "").strip() if t_el is not None else ""
                    l_txt = (l_el.text or "").strip() if l_el is not None else ""
                    if t_txt and t_txt not in used_topics:
                        research_notes = f"Live Indian Tech News: {t_txt}\nReference URL: {l_txt}"
                        break
        except Exception as exc:
            print(f"[WARN] Tech RSS fallback failed: {exc}")

    if not research_notes.strip():
        fallback_topic = next((t for t in EVERGREEN_TECH_TOPICS if t not in used_topics), EVERGREEN_TECH_TOPICS[0])
        research_notes = f"Topic: {fallback_topic}. General buying & tech guide for Indian buyers."

    # Pass 2: Convert verified research into structured Malayalam script plan
    script_prompt = f"""
You are the host of "Gadget Pulse Malayalam", a crisp, trustworthy Kerala Malayalam tech & gadgets channel covering phones, earbuds, laptops, and smart home appliances (robotic vacuums, TVs, fridges, washing machines).

VERIFIED RESEARCH NOTES:
{research_notes}

RULES:
1. Only state verified prices and specs from the notes above—never invent numbers.
2. Write 'narration_malayalam' in warm, natural spoken Kerala Malayalam (110–135 words, NO outro line—we append the platform-specific outro automatically).
3. Use short sentences with commas for natural breathing pauses.
4. For 'search_queries', give 4 literal 2-to-3 word English visual phrases (never abbreviations like TWS/ANC/OLED).
5. Set 'available_in_india' to True ONLY if the product is currently on sale in India (not rumored/upcoming/general tip).
"""
    last_err = None
    for attempt in range(3):
        for model_name in [m for m in GEMINI_MODEL_CANDIDATES if m]:
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=script_prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=TechShortPlan,
                        temperature=0.75,
                    ),
                )
                return TechShortPlan.model_validate_json(resp.text)
            except Exception as exc:
                print(f"[WARN] Script generation failed on {model_name} (try {attempt + 1}/3): {exc}")
                last_err = exc
                if "503" in str(exc) or "UNAVAILABLE" in str(exc):
                    time.sleep(6)
        if attempt < 2:
            wait_s = 12 * (attempt + 1)
            print(f"[INFO] Waiting {wait_s}s before retrying Gemini models...")
            time.sleep(wait_s)
    raise RuntimeError(f"All Gemini models failed: {last_err}")


# ---------------------------------------------------------------------------
# Step 2: Official Product Photo Scraper + Tag-Filtered Stock Clips
# ---------------------------------------------------------------------------
def search_exact_product_images(product_name: str, out_dir: Path, max_images: int = 3) -> List[Path]:
    """Searches public web images for the exact device name (e.g. 'Nothing Phone 3a') when launch URLs fail."""
    saved: List[Path] = []
    if not product_name.strip():
        return saved
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    q = urllib.parse.quote_plus(f"{product_name} official product image")
    try:
        r = requests.get(f"https://www.bing.com/images/search?q={q}&qft=+filterui:imagesize-large&form=HDRSC2", headers=headers, timeout=15)
        if r.ok:
            # Bing embeds direct image URLs as murl&quot;:&quot;https://...&quot; or "murl":"https://..."
            urls = re.findall(r'murl(?:&quot;|")\s*:\s*(?:&quot;|")(https?://[^"&]+)', r.text)
            for idx, img_url in enumerate(urls[:12]):
                if len(saved) >= max_images:
                    break
                if any(b in img_url.lower() for b in ("logo", "favicon", "avatar", "icon", ".svg", "placeholder")):
                    continue
                try:
                    ir = requests.get(img_url, headers=headers, timeout=15)
                    if ir.ok and len(ir.content) > 18000:
                        out_img = out_dir / f"exact_prod_{len(saved)}.jpg"
                        out_img.write_bytes(ir.content)
                        with Image.open(out_img) as im:
                            if im.width >= 450 and im.height >= 300:
                                im.convert("RGB").save(out_img, quality=92)
                                saved.append(out_img)
                                print(f"[INFO] Fetched exact product photo via web search: {img_url[:80]}")
                except Exception:
                    continue
    except Exception as exc:
        print(f"[WARN] Exact product image search failed for '{product_name}': {exc}")
    return saved


def scrape_official_product_images(urls: List[str], out_dir: Path, max_images: int = 3) -> List[Path]:
    """Extracts og:image / twitter:image / large product photos from official launch pages."""
    saved: List[Path] = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for idx, url in enumerate(urls[:4]):
        if len(saved) >= max_images:
            break
        try:
            resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if not resp.ok:
                continue
            html = resp.text[:150000]
            candidates = re.findall(
                r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']',
                html,
                flags=re.IGNORECASE,
            )
            if not candidates:
                candidates = re.findall(
                    r'content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']',
                    html,
                    flags=re.IGNORECASE,
                )
            for img_url in candidates[:2]:
                abs_url = urllib.parse.urljoin(resp.url, img_url)
                if any(bad in abs_url.lower() for bad in ("logo", "favicon", "avatar", "icon", ".svg")):
                    continue
                img_resp = requests.get(abs_url, headers=headers, timeout=20)
                if img_resp.ok and len(img_resp.content) > 15000:
                    out_img = out_dir / f"official_prod_{len(saved)}.jpg"
                    out_img.write_bytes(img_resp.content)
                    # Verify valid readable image & decent width
                    with Image.open(out_img) as im:
                        if im.width >= 400 and im.height >= 250:
                            im.convert("RGB").save(out_img, quality=92)
                            saved.append(out_img)
                            print(f"[INFO] Fetched official product image: {abs_url[:80]}")
                            break
        except Exception as exc:
            print(f"[WARN] Official image scrape skipped ({url[:60]}): {exc}")
    return saved


def image_to_ken_burns_clip(img_path: Path, out_mp4: Path, duration_sec: float = 3.5, style_idx: int = 0) -> None:
    """Renders dynamic 1080x1920 30fps product photo animations: fly-in, bounce-zoom, float, and swipe-out."""
    fps = 30
    total_frames = max(60, int(round(duration_sec * fps)))
    W, H = 1080, 1920
    cx, cy = 540, 840  # Center stage (safely between header and subtitles)

    # Pre-render dark tech grid background
    bg = Image.new("RGB", (W, H), (9, 16, 34))
    bg_d = ImageDraw.Draw(bg)
    for gx in range(0, W, 90):
        bg_d.line([(gx, 0), (gx, H)], fill=(16, 28, 56), width=2)
    for gy in range(0, H, 90):
        bg_d.line([(0, gy), (W, gy)], fill=(16, 28, 56), width=2)

    prod = Image.open(img_path).convert("RGB")
    prod = ImageEnhance.Color(prod).enhance(1.18)
    prod = ImageEnhance.Contrast(prod).enhance(1.10)
    # Fit product nicely into a 860x860 card base
    base_scale = min(860 / prod.width, 860 / prod.height)
    bw, bh = max(200, int(prod.width * base_scale)), max(200, int(prod.height * base_scale))
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

            # Pulsing neon tech ring behind the product
            ring_r = 350 + int(20 * math.sin(t * 4.0))
            d.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r], outline=accent, width=3)

            # Entry (0..0.20) -> Dynamic Showcase (0.20..0.82) -> Fast Swipe-Out (0.82..1.0)
            dx, dy, scale_f = 0, 0, 1.0
            if p < 0.20:
                ep = 1.0 - (1.0 - p / 0.20) ** 3
                if mode == 0:  # Fly in from right
                    dx = int((1.0 - ep) * 950)
                    scale_f = 0.85 + 0.15 * ep
                elif mode == 1:  # Pop up from bottom with overshoot
                    dy = int((1.0 - ep) * 700)
                    scale_f = 0.65 + 0.35 * ep
                elif mode == 2:  # Fly in from left
                    dx = int(-(1.0 - ep) * 950)
                    scale_f = 0.85 + 0.15 * ep
                else:  # Zoom-punch in from center
                    scale_f = 0.40 + 0.60 * ep
            elif p > 0.82:
                sp = ((p - 0.82) / 0.18) ** 2
                if mode in (0, 3):  # Fast swipe out left
                    dx = int(-sp * 1150)
                    scale_f = 1.06 - 0.12 * sp
                else:  # Fast swipe out right
                    dx = int(sp * 1150)
                    scale_f = 1.06 - 0.12 * sp
            else:
                mid_p = (p - 0.20) / 0.62
                if mode in (0, 2):  # Smooth zoom-in + gentle float
                    scale_f = 1.0 + 0.10 * mid_p
                    dy = int(16 * math.sin(t * 3.2))
                elif mode == 1:  # Pulse zoom
                    scale_f = 1.0 + 0.08 * math.sin(mid_p * math.pi)
                    dx = int(18 * math.sin(t * 2.5))
                else:  # Slow close-up zoom + pan
                    scale_f = 1.04 + 0.10 * mid_p
                    dx = int(-24 + 48 * mid_p)

            nw = max(120, int(bw * scale_f))
            nh = max(120, int(bh * scale_f))
            p_resized = prod_base.resize((nw, nh), Image.Resampling.BILINEAR)

            px = cx + dx - nw // 2
            py = cy + dy - nh // 2
            # Card backing + neon border around product photo
            pad = 14
            d.rounded_rectangle(
                [px - pad, py - pad, px + nw + pad, py + nh + pad],
                radius=28,
                fill=(18, 30, 58),
                outline=accent,
                width=5,
            )
            frame.paste(p_resized, (px, py))
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("Dynamic product photo animation failed")


def _query_matches_tags(query: str, tags_str: str) -> bool:
    if not tags_str:
        return True
    q_words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query)]
    return True if not q_words else any(w in tags_str.lower() for w in q_words)


def gather_tech_visual_clips(plan: TechShortPlan, out_dir: Path) -> Tuple[List[Path], Optional[Path]]:
    """Prioritizes official product photos when is_specific_product=True, then Pixabay tech clips."""
    clips: List[Path] = []
    lead_photo: Optional[Path] = None

    if plan.is_specific_product:
        prod_imgs: List[Path] = []
        if plan.product_page_urls:
            prod_imgs.extend(scrape_official_product_images(plan.product_page_urls, out_dir, max_images=3))
        if len(prod_imgs) < 6:
            needed = 6 - len(prod_imgs)
            prod_imgs.extend(search_exact_product_images(plan.product_name, out_dir, max_images=needed))
        if prod_imgs:
            lead_photo = prod_imgs[0]
            for idx, p_img in enumerate(prod_imgs):
                c_mp4 = out_dir / f"prod_clip_{idx}.mp4"
                image_to_ken_burns_clip(p_img, c_mp4, duration_sec=3.8, style_idx=idx)
                clips.append(c_mp4)

    pixabay_key = os.getenv("PIXABAY_API_KEY", "").strip()
    # Only pull generic Pixabay stock if we don't already have >=2 exact product photos
    if pixabay_key and len(clips) < (3 if plan.is_specific_product else 5):
        for idx, query in enumerate(plan.search_queries):
            if len(clips) >= 4:
                break
            try:
                resp = requests.get(
                    f"https://pixabay.com/api/videos/?key={pixabay_key}&q={urllib.parse.quote(query)}&per_page=8&safesearch=true",
                    timeout=20,
                )
                for h in resp.json().get("hits", []):
                    if not _query_matches_tags(query, h.get("tags", "")):
                        continue
                    vids = h.get("videos", {})
                    url = vids.get("medium", {}).get("url") or vids.get("small", {}).get("url")
                    if url:
                        c_mp4 = out_dir / f"stock_{idx}.mp4"
                        c_mp4.write_bytes(requests.get(url, timeout=60).content)
                        clips.append(c_mp4)
                        break
            except Exception as exc:
                print(f"[WARN] Pixabay clip failed for '{query}': {exc}")

    if not clips:
        for idx, col in enumerate(["#0B162C", "#112240", "#0A192F", "#13294B"]):
            c_mp4 = out_dir / f"grad_{idx}.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={col}:s=1080x1920:r=30:d=12", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(c_mp4)],
                check=True,
            )
            clips.append(c_mp4)

    return clips, lead_photo


# ---------------------------------------------------------------------------
# Step 3: Audio (Voiceover + Procedural BGM & SFX)
# ---------------------------------------------------------------------------
def get_media_duration(media_path: Path) -> float:
    return float(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
            text=True,
        ).strip()
    )


async def _edge_tts_async(text: str, out_mp3: Path, out_srt: Optional[Path]) -> None:
    comm = edge_tts.Communicate(text, VOICE_NAME, rate="-3%", pitch="-1Hz")
    submaker = edge_tts.SubMaker() if out_srt else None
    with open(out_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif submaker and chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                submaker.feed(chunk)
    if out_srt and submaker:
        out_srt.write_text(submaker.get_srt(), encoding="utf-8")


def synth_voiceover(text: str, out_mp3: Path, out_srt: Optional[Path] = None) -> float:
    asyncio.run(_edge_tts_async(text, out_mp3, out_srt))
    return get_media_duration(out_mp3)


def synthesize_ambient_bgm(out_wav: Path, duration_sec: float, sample_rate: int = 44100) -> None:
    total_samples = int(duration_sec * sample_rate)
    chords = [(261.63, 329.63, 392.0, 493.88), (220.0, 261.63, 329.63, 392.0), (174.61, 220.0, 261.63, 329.63), (196.0, 246.94, 293.66, 329.63)]
    chord_len = 4.0
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for start in range(0, total_samples, 4096):
            end = min(total_samples, start + 4096)
            buf = bytearray()
            for i in range(start, end):
                t = i / sample_rate
                freqs = chords[int(t // chord_len) % len(chords)]
                env = math.sin(math.pi * min(1.0, (t % chord_len) / chord_len)) ** 0.45
                val = sum(math.sin(2.0 * math.pi * f * t) * (0.28 if idx == 0 else 0.17) for idx, f in enumerate(freqs))
                pcm = int(max(-1.0, min(1.0, val * env * 0.32)) * 32767)
                buf.extend(struct.pack("<hh", pcm, pcm))
            wf.writeframes(bytes(buf))


def synthesize_whoosh(out_wav: Path, duration_sec: float = 0.4, sample_rate: int = 44100) -> None:
    rng = random.Random(42)
    prev = 0.0
    total = int(duration_sec * sample_rate)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        buf = bytearray()
        for i in range(total):
            t = i / total
            env = math.sin(math.pi * t) ** 1.5
            prev += (0.08 + 0.25 * math.sin(math.pi * t)) * (rng.uniform(-1.0, 1.0) - prev)
            pcm = int(max(-1.0, min(1.0, prev * env * 0.5)) * 32767)
            buf.extend(struct.pack("<hh", pcm, pcm))
        wf.writeframes(bytes(buf))


# ---------------------------------------------------------------------------
# Step 4: 2D Spec Card Animation, Overlays, Thumbnail & Dual-Outro Assembly
# ---------------------------------------------------------------------------
def parse_srt_time(ts: str) -> float:
    h, m, s_ms = ts.strip().split(":")
    s, ms = s_ms.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_coalesced_srt(srt_path: Path, max_words: int = 8) -> List[Tuple[float, float, str]]:
    if not srt_path.exists():
        return []
    raw = srt_path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    cues: List[Tuple[float, float, str]] = []
    for block in re.split(r"\r?\n\r?\n", raw):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            s_t, e_t = lines[1].split("-->")
            cues.append((parse_srt_time(s_t), parse_srt_time(e_t), " ".join(lines[2:])))
    if len(cues) <= 18:
        return cues
    merged, buf_w, b_s, b_e = [], [], cues[0][0], cues[0][1]
    for st, en, txt in cues:
        w = txt.split()
        if len(buf_w) + len(w) > max_words or (en - b_s) > 4.2:
            if buf_w:
                merged.append((b_s, b_e, " ".join(buf_w)))
            buf_w, b_s, b_e = w, st, en
        else:
            buf_w.extend(w)
            b_e = en
    if buf_w:
        merged.append((b_s, b_e, " ".join(buf_w)))
    return merged



def render_karaoke_subtitle_mov(
    cues: List[Tuple[float, float, str]],
    total_duration: float,
    out_mov: Path,
    font_path: str,
) -> None:
    """Renders SpendWise-style Karaoke word-by-word yellow pill highlighted subtitles on transparent .mov."""
    W, H, fps = 1080, 1920, 30
    total_frames = max(1, int(round(total_duration * fps)))
    lat_path = find_latin_font()
    ml_f = ImageFont.truetype(font_path, 40)
    lat_f = ImageFont.truetype(lat_path, 40)
    dummy_d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    cue_layouts = []
    for st, en, txt in cues:
        lines = wrap_mixed_text_px(dummy_d, txt, ml_f, lat_f, 860)[:3]
        words_flat = [w for ln in lines for w in ln.split()]
        cue_layouts.append((st, en, lines, max(1, len(words_flat))))

    cmd = [
        "ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{W}x{H}", "-pix_fmt", "rgba", "-r", str(fps), "-i", "-",
        "-an", "-c:v", "qtrle", str(out_mov),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    try:
        cue_idx = 0
        for f in range(total_frames):
            t = f / fps
            frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(frame)
            while cue_idx < len(cue_layouts) and t > cue_layouts[cue_idx][1]:
                cue_idx += 1
            if cue_idx < len(cue_layouts):
                st, en, lines, n_words = cue_layouts[cue_idx]
                if st <= t <= en:
                    dur = max(0.2, en - st)
                    active_word_idx = min(n_words - 1, int(((t - st) / dur) * n_words))
                    max_lw = max(measure_mixed_width(d, ln, ml_f, lat_f) for ln in lines)
                    line_h = 56
                    box_w = min(980, int(max_lw) + 76)
                    box_h = len(lines) * line_h + 36
                    x0 = (W - box_w) // 2
                    y0 = 1520 - box_h // 2
                    d.rounded_rectangle(
                        [x0, y0, x0 + box_w, y0 + box_h],
                        radius=26,
                        fill=(10, 16, 34, 228),
                        outline=(0, 229, 255, 220),
                        width=3,
                    )
                    cur_y = y0 + 42
                    w_counter = 0
                    space_w = d.textlength(" ", font=ml_f)
                    for ln in lines:
                        lw = measure_mixed_width(d, ln, ml_f, lat_f)
                        cur_x = (W - lw) / 2.0
                        for w in ln.split():
                            ww = measure_mixed_width(d, w, ml_f, lat_f)
                            if w_counter == active_word_idx:
                                d.rounded_rectangle(
                                    [cur_x - 8, cur_y - 24, cur_x + ww + 8, cur_y + 24],
                                    radius=12,
                                    fill=(250, 204, 21, 255),
                                )
                                txt_col = (10, 16, 34, 255)
                            else:
                                txt_col = (255, 255, 255, 255)
                            for seg, is_lat in _split_script_runs(w):
                                fnt = lat_f if is_lat else ml_f
                                d.text((cur_x, cur_y), seg, font=fnt, fill=txt_col, anchor="lm")
                                cur_x += d.textlength(seg, font=fnt)
                            cur_x += space_w
                            w_counter += 1
                        cur_y += line_h
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("Karaoke subtitle render failed")


def render_2d_spec_card_clip(plan: TechShortPlan, duration_sec: float, out_mp4: Path, font_path: str) -> None:
    """Renders a sleek 1080x1920 2D animated Tech Spec Card in the center stage (y=390..1280)."""
    fps = 30
    total_frames = max(60, int(round(duration_sec * fps)))
    W, H = 1080, 1920
    lat_path = find_latin_font()
    bg = Image.new("RGB", (W, H), (9, 16, 34))
    bg_d = ImageDraw.Draw(bg)
    for gx in range(0, W, 90):
        bg_d.line([(gx, 0), (gx, H)], fill=(17, 30, 58), width=2)
    for gy in range(0, H, 90):
        bg_d.line([(0, gy), (W, gy)], fill=(17, 30, 58), width=2)

    f_title_ml = ImageFont.truetype(font_path, 42)
    f_title_lat = ImageFont.truetype(lat_path, 42)
    f_price_ml = ImageFont.truetype(font_path, 52)
    f_price_lat = ImageFont.truetype(lat_path, 52)
    f_spec_ml = ImageFont.truetype(font_path, 38)
    f_spec_lat = ImageFont.truetype(lat_path, 38)

    title_lines = wrap_mixed_text_px(bg_d, plan.product_name, f_title_ml, f_title_lat, 820)[:1]
    price_lines = wrap_mixed_text_px(bg_d, plan.price_inr_label, f_price_ml, f_price_lat, 760)[:1]
    specs = [wrap_mixed_text_px(bg_d, s, f_spec_ml, f_spec_lat, 780)[:1][0] for s in plan.spec_highlights_ml[:3]]

    cmd = [
        "ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{W}x{H}", "-pix_fmt", "rgb24", "-r", str(fps), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(out_mp4),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    try:
        for f in range(total_frames):
            progress = f / max(1, total_frames - 1)
            frame = bg.copy()
            d = ImageDraw.Draw(frame)

            d.rounded_rectangle([70, 395, 1010, 1275], radius=34, fill=(14, 24, 48), outline=(0, 229, 255), width=4)
            draw_mixed_centered_lines(d, 540, 465, title_lines, f_title_ml, f_title_lat, (255, 255, 255))

            # Glowing price pill
            d.rounded_rectangle([210, 520, 870, 615], radius=26, fill=(250, 204, 21))
            draw_mixed_centered_lines(d, 540, 567, price_lines, f_price_ml, f_price_lat, (10, 16, 34))

            # Animated spec rows sliding in one by one
            for s_idx, sp in enumerate(specs):
                appear_at = s_idx * 0.22
                if progress >= appear_at:
                    ry0 = 665 + s_idx * 165
                    d.rounded_rectangle([120, ry0, 960, ry0 + 130], radius=22, fill=(22, 38, 72), outline=(56, 189, 248), width=3)
                    draw_mixed_centered_lines(d, 540, ry0 + 65, [sp], f_spec_ml, f_spec_lat, (240, 249, 255))

            d.rounded_rectangle([130, 1228, 950, 1246], radius=9, fill=(30, 46, 80))
            d.rounded_rectangle([130, 1228, 130 + max(18, int(820 * progress)), 1246], radius=9, fill=(0, 229, 255))
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("2D Spec Card render failed")


def render_thumbnail_9x16(lead_photo: Optional[Path], lead_clip: Path, question_ml: str, price_label: str, out_jpg: Path, font_path: str) -> None:
    src_jpg = WORK_DIR / "thumb_raw.jpg"
    if lead_photo and lead_photo.exists():
        src_jpg.write_bytes(lead_photo.read_bytes())
    else:
        subprocess.run(["ffmpeg", "-y", "-ss", "1.0", "-i", str(lead_clip), "-frames:v", "1", "-q:v", "2", str(src_jpg)], check=True)

    base = Image.open(src_jpg).convert("RGBA")
    base = ImageEnhance.Color(base).enhance(1.38)
    base = ImageEnhance.Contrast(base).enhance(1.18)

    W, H = 1080, 1920
    scale = max(W / base.width, H / base.height)
    res = base.resize((int(base.width * scale), int(base.height * scale)), Image.Resampling.LANCZOS)
    canvas = res.crop(((res.width - W) // 2, (res.height - H) // 2, (res.width + W) // 2, (res.height + H) // 2))

    ov = Image.new("RGBA", (W, H), (10, 16, 34, 85))
    ov_d = ImageDraw.Draw(ov)
    ov_d.rectangle([0, int(H * 0.48), W, H], fill=(10, 16, 34, 200))
    canvas = Image.alpha_composite(canvas, ov).convert("RGB")
    d = ImageDraw.Draw(canvas)

    d.rectangle([9, 9, W - 10, H - 10], outline=(255, 214, 0), width=16)
    lat_path = find_latin_font()
    f_badge_ml, f_badge_lat = ImageFont.truetype(font_path, 42), ImageFont.truetype(lat_path, 42)
    d.rounded_rectangle([170, 210, 910, 305], radius=44, fill=(239, 68, 68), outline=(255, 255, 255), width=4)
    draw_mixed_centered_lines(d, 540, 257, [price_label], f_badge_ml, f_badge_lat, (255, 255, 255))

    f_q_ml, f_q_lat = ImageFont.truetype(font_path, 66), ImageFont.truetype(lat_path, 66)
    q_lines = wrap_mixed_text_px(d, question_ml, f_q_ml, f_q_lat, 880)[:3]
    draw_mixed_centered_lines(d, 544, int(H * 0.68) + 4, q_lines, f_q_ml, f_q_lat, (0, 0, 0), line_spacing=20)
    draw_mixed_centered_lines(d, 540, int(H * 0.68), q_lines, f_q_ml, f_q_lat, (255, 235, 59), line_spacing=20)

    f_br_ml, f_br_lat = ImageFont.truetype(font_path, 34), ImageFont.truetype(lat_path, 34)
    draw_mixed_centered_lines(d, 540, int(H * 0.90), ["ഗാഡ്ജറ്റ് പൾസ് മലയാളം"], f_br_ml, f_br_lat, (0, 229, 255))
    canvas.save(out_jpg, quality=92)


def render_overlay_box(text: str, out_png: Path, font_path: str, is_header: bool) -> None:
    W, H = (1080, 260) if is_header else (1080, 360)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lat_path = find_latin_font()
    sz = 42 if is_header else 40
    ml_f, lat_f = ImageFont.truetype(font_path, sz), ImageFont.truetype(lat_path, sz)
    lines = wrap_mixed_text_px(d, text, ml_f, lat_f, 860)[: 2 if is_header else 3]

    if is_header:
        d.rounded_rectangle([60, 20, 1020, 240], radius=28, fill=(19, 34, 56, 228), outline=(0, 229, 255, 230), width=3)
        draw_mixed_centered_lines(d, 540, 130, lines, ml_f, lat_f, (255, 255, 255, 255), line_spacing=14)
    else:
        max_lw = max(measure_mixed_width(d, ln, ml_f, lat_f) for ln in lines)
        box_w = min(980, int(max_lw) + 72)
        box_h = min(320, len(lines) * (sz + 16) + 46)
        x0, y0 = (1080 - box_w) // 2, (360 - box_h) // 2
        d.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=24, fill=(12, 18, 34, 220), outline=(0, 229, 255, 190), width=2)
        draw_mixed_centered_lines(d, 540, 180, lines, ml_f, lat_f, (255, 235, 59, 255), line_spacing=16)
    img.save(out_png)


def assemble_dual_outro_shorts(
    plan: TechShortPlan,
    clips: List[Path],
    main_mp3: Path,
    main_srt: Path,
    yt_outro_mp3: Path,
    meta_outro_mp3: Path,
    final_yt_mp4: Path,
    final_meta_mp4: Path,
) -> None:
    font_path = find_malayalam_font()
    main_dur = get_media_duration(main_mp3)

    # Render 2D Spec Card & interleave with clips
    spec_card_mp4 = WORK_DIR / "spec_card.mp4"
    seg_dur = max(3.5, (main_dur + 0.5) / (len(clips) + 1))
    render_2d_spec_card_clip(plan, seg_dur, spec_card_mp4, font_path)

    norm_clips: List[Path] = []
    for idx, c in enumerate(clips):
        n_mp4 = WORK_DIR / f"norm_{idx}.mp4"
        if c.name.startswith("prod_clip_"):
            subprocess.run(
                ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(c), "-t", f"{seg_dur:.2f}", "-vf", "setsar=1,fps=30,format=yuv420p", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", str(n_mp4)],
                check=True,
            )
            norm_clips.append(n_mp4)
            if idx == 0:
                norm_clips.append(spec_card_mp4)
            continue
        vf = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=135:240:force_original_aspect_ratio=increase,crop=135:240,boxblur=4:1,scale=1080:1920,eq=brightness=-0.15[bg_blur];"
            "[fg]scale=1080:1240:force_original_aspect_ratio=decrease[fg_fit];"
            "[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2-40,setsar=1,fps=30,format=yuv420p[out]"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(c), "-t", f"{seg_dur:.2f}", "-filter_complex", vf, "-map", "[out]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", str(n_mp4)],
            check=True,
        )
        norm_clips.append(n_mp4)
        if idx == 0:
            norm_clips.append(spec_card_mp4)

    concat_txt = WORK_DIR / "main_concat.txt"
    concat_txt.write_text("\n".join(f"file '{c.resolve()}'" for c in norm_clips), encoding="utf-8")
    merged_bg = WORK_DIR / "merged_bg.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c", "copy", str(merged_bg)], check=True)

    bgm_wav = WORK_DIR / "bgm.wav"
    whoosh_wav = WORK_DIR / "whoosh.wav"
    synthesize_ambient_bgm(bgm_wav, main_dur + 12.0)
    synthesize_whoosh(whoosh_wav)

    mixed_main_wav = WORK_DIR / "mixed_main.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(main_mp3), "-i", str(bgm_wav), "-i", str(whoosh_wav),
            "-filter_complex",
            "[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume=1.0[vo];"
            "[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume=0.11[bgm];"
            f"[2:a]adelay={int(seg_dur*1000)}|{int(seg_dur*1000)},volume=0.22[w1];"
            "[vo][bgm][w1]amix=inputs=3:duration=first:normalize=0[aout]",
            "-map", "[aout]", "-ac", "2", "-ar", "44100", str(mixed_main_wav),
        ],
        check=True,
    )

    header_png = WORK_DIR / "header.png"
    render_overlay_box(plan.hook_headline_ml, header_png, font_path, is_header=True)
    cues = parse_coalesced_srt(main_srt)

    karaoke_mov = WORK_DIR / "karaoke_sub.mov"
    main_dur = get_media_duration(mixed_main_wav)
    render_karaoke_subtitle_mov(cues, main_dur, karaoke_mov, font_path)
    cmd_inputs = ["-i", str(merged_bg), "-i", str(mixed_main_wav), "-i", str(header_png), "-i", str(karaoke_mov)]
    filters = [
        "[0:v][2:v]overlay=0:90[vhead]",
        "[vhead][3:v]overlay=0:0[vsub]",
        f"[vsub]drawtext=text='{WATERMARK_TEXT}':fontsize=32:fontcolor=white@0.5:shadowcolor=black@0.25:shadowx=2:shadowy=2:x=48:y=40[vout]",
    ]
    main_body_mp4 = WORK_DIR / "main_body.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", *cmd_inputs, "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k", "-shortest", str(main_body_mp4),
        ],
        check=True,
    )

    def _append_outro(outro_mp3: Path, outro_bg_clip: Path, target_mp4: Path, label: str) -> None:
        odur = get_media_duration(outro_mp3)
        outro_seg = WORK_DIR / f"outro_{label}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(outro_bg_clip), "-i", str(outro_mp3), "-i", str(header_png),
                "-t", f"{odur:.2f}",
                "-filter_complex",
                "[0:v]split=2[bg][fg];[bg]scale=135:240:force_original_aspect_ratio=increase,crop=135:240,boxblur=4:1,scale=1080:1920,eq=brightness=-0.15[bg_b];"
                "[fg]scale=1080:1240:force_original_aspect_ratio=decrease[fg_f];[bg_b][fg_f]overlay=(W-w)/2:(H-h)/2-40[base];"
                "[base][2:v]overlay=0:90,setsar=1,fps=30,format=yuv420p[vout]",
                "-map", "[vout]", "-map", "1:a:0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k", "-shortest", str(outro_seg),
            ],
            check=True,
        )
        clist = WORK_DIR / f"concat_{label}.txt"
        clist.write_text(f"file '{main_body_mp4.resolve()}'\nfile '{outro_seg.resolve()}'", encoding="utf-8")
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(clist),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k", "-movflags", "+faststart", str(target_mp4),
            ],
            check=True,
        )

    _append_outro(yt_outro_mp3, clips[-1], final_yt_mp4, "yt")
    if yt_outro_mp3 == meta_outro_mp3:
        final_meta_mp4.write_bytes(final_yt_mp4.read_bytes())
    else:
        _append_outro(meta_outro_mp3, clips[-1], final_meta_mp4, "meta")


# ---------------------------------------------------------------------------
# Step 5: Affiliate Links + Publishing (YouTube, Instagram Reels, Facebook Reels)
# ---------------------------------------------------------------------------
def build_affiliate_links_block(plan: TechShortPlan) -> str:
    if not (plan.is_specific_product and plan.available_in_india):
        return ""
    q = urllib.parse.quote_plus(plan.product_name)
    amz_tag = os.getenv("AMAZON_AFFILIATE_TAG", "").strip()
    fk_id = os.getenv("FLIPKART_AFFILIATE_ID", "").strip()
    lines = ["\n🛒 Best Buy Links:"]
    amz_url = f"https://www.amazon.in/s?k={q}" + (f"&tag={amz_tag}" if amz_tag else "")
    lines.append(f"• Amazon India: {amz_url}")
    if fk_id:
        lines.append(f"• Flipkart: https://www.flipkart.com/search?q={q}&affid={fk_id}")
    else:
        lines.append(f"• Flipkart: https://www.flipkart.com/search?q={q}")
    return "\n".join(lines) + "\n"



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

def upload_to_youtube(video_path: Path, thumb_jpg: Path, plan: TechShortPlan) -> str:
    youtube = get_youtube_service()
    status_obj = {"privacyStatus": PRIVACY_STATUS, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False}
    if PRIVACY_STATUS == "private":
        ist_now = datetime.now(IST_TZ)
        target_ist = ist_now.replace(hour=20, minute=0, second=0, microsecond=0)  # 8:00 PM IST
        if ist_now >= target_ist - timedelta(minutes=10):
            target_ist = ist_now + timedelta(minutes=60)
        status_obj["publishAt"] = target_ist.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    full_desc = f"{plan.description}{build_affiliate_links_block(plan)}"
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=1024 * 1024 * 5, resumable=True)
    req = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": plan.title[:100],
                "description": full_desc[:4900],
                "tags": plan.tags,
                "categoryId": CATEGORY_ID,
                "defaultLanguage": "ml",
                "defaultAudioLanguage": "ml",
            },
            "status": status_obj,
        },
        media_body=media,
    )
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    video_id = resp["id"]
    print(f"[SUCCESS] Uploaded YouTube Short: https://youtube.com/shorts/{video_id}")

    for _ in range(16):
        try:
            vinfo = youtube.videos().list(part="processingDetails", id=video_id).execute()
            pstat = vinfo.get("items", [{}])[0].get("processingDetails", {}).get("processingStatus", "")
            if pstat == "succeeded":
                time.sleep(10)
                break
        except Exception:
            pass
        time.sleep(15)

    try:
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb_jpg), mimetype="image/jpeg")).execute()
        print("[SUCCESS] Uploaded custom 9:16 thumbnail to YouTube Shorts!")
    except Exception as exc:
        print(f"[WARN] Thumbnail API skipped: {exc}")

    try:
        pls = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
        pl_id = next((i["id"] for i in pls.get("items", []) if i["snippet"]["title"].strip().lower() == "gadget pulse malayalam - shorts"), None)
        if not pl_id:
            pl_id = youtube.playlists().insert(
                part="snippet,status",
                body={"snippet": {"title": "Gadget Pulse Malayalam - Shorts", "description": "Daily Tech & Gadget Shorts"}, "status": {"privacyStatus": "public"}},
            ).execute()["id"]
        youtube.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": pl_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
    except Exception as exc:
        print(f"[INFO] Playlist skipped: {exc}")

    aff_block = build_affiliate_links_block(plan).strip()
    if aff_block:
        post_first_comment_safe(youtube, video_id, f"{plan.product_name} ({plan.price_inr_label})\n{aff_block}")

    return video_id


def upload_to_instagram_reels(caption: str, comment_text: str = "") -> None:
    access_token = os.getenv("META_ACCESS_TOKEN", "").strip()
    ig_user_id = os.getenv("IG_USER_ID", "").strip()
    video_url = os.getenv("IG_PUBLIC_VIDEO_URL", "").strip()
    thumb_url = os.getenv("IG_PUBLIC_THUMB_URL", "").strip()
    if not (access_token and ig_user_id and video_url):
        print("[SKIP] Instagram Reels skipped (missing token, IG_USER_ID, or IG_PUBLIC_VIDEO_URL).")
        return

    payload = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true", "access_token": access_token}
    if thumb_url:
        payload["cover_url"] = thumb_url
    init_resp = requests.post(f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media", data=payload, timeout=30)
    if not init_resp.ok:
        print(f"[ERROR] Instagram init HTTP {init_resp.status_code}: {init_resp.text}")
        init_resp.raise_for_status()
    cid = init_resp.json()["id"]

    for _ in range(20):
        time.sleep(10)
        st = requests.get(f"https://graph.facebook.com/{GRAPH_API_VERSION}/{cid}", params={"fields": "status_code", "access_token": access_token}, timeout=20).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container error: {st}")

    pub = requests.post(f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media_publish", data={"creation_id": cid, "access_token": access_token}, timeout=30)
    pub.raise_for_status()
    ig_media_id = pub.json().get("id")
    print(f"[SUCCESS] Published Instagram Reel! ID: {ig_media_id}")
    if ig_media_id and comment_text.strip():
        try:
            c_resp = requests.post(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_media_id}/comments",
                data={"message": comment_text.strip(), "access_token": access_token},
                timeout=20,
            )
            if c_resp.ok:
                print("[SUCCESS] Posted affiliate comment on Instagram Reel!")
            else:
                print(f"[INFO] Instagram comment skipped (HTTP {c_resp.status_code}): {c_resp.text[:160]}")
        except Exception as exc:
            print(f"[INFO] Instagram comment skipped: {exc}")


def upload_to_facebook_reels(video_path: Path, title: str, description: str, comment_text: str = "") -> None:
    access_token = os.getenv("META_ACCESS_TOKEN", "").strip()
    fb_page_id = os.getenv("FB_PAGE_ID", "").strip()
    if not (access_token and fb_page_id):
        print("[SKIP] Facebook Reels skipped.")
        return
    try:
        pt = requests.get(f"https://graph.facebook.com/{GRAPH_API_VERSION}/{fb_page_id}", params={"fields": "access_token", "access_token": access_token}, timeout=20)
        if pt.ok and pt.json().get("access_token"):
            access_token = pt.json()["access_token"]
    except Exception:
        pass

    vbytes = video_path.read_bytes()
    start_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{fb_page_id}/video_reels"
    s_resp = requests.post(start_url, data={"upload_phase": "start", "access_token": access_token}, timeout=30)
    if not s_resp.ok:
        print(f"[ERROR] Facebook Reels start HTTP {s_resp.status_code}: {s_resp.text}")
        s_resp.raise_for_status()
    s_data = s_resp.json()
    if "video_id" not in s_data:
        raise RuntimeError(f"Facebook Reels start missing video_id: {s_data}")
    vid = s_data["video_id"]
    up_url = s_data.get("upload_url", f"https://rupload.facebook.com/video-upload/{GRAPH_API_VERSION}/{vid}")
    requests.post(
        up_url,
        headers={"Authorization": f"OAuth {access_token}", "offset": "0", "file_size": str(len(vbytes)), "Content-Length": str(len(vbytes)), "Content-Type": "application/octet-stream"},
        data=vbytes,
        timeout=180,
    ).raise_for_status()
    ist_now = datetime.now(IST_TZ)
    target_ist = ist_now.replace(hour=21, minute=7, second=0, microsecond=0)
    fin_payload = {"upload_phase": "finish", "video_id": vid, "description": description, "title": title[:100], "access_token": access_token}
    if ist_now <= target_ist - timedelta(minutes=15):
        fin_payload["video_state"] = "SCHEDULED"
        fin_payload["scheduled_publish_time"] = str(int(target_ist.timestamp()))
        print(f"[INFO] Scheduling Gadget Pulse FB Reel for 9:07 PM IST ({target_ist.isoformat()})!")
    else:
        fin_payload["video_state"] = "PUBLISHED"
    fin_r = requests.post(start_url, data=fin_payload, timeout=30)
    if not fin_r.ok and fin_payload.get("video_state") == "SCHEDULED":
        fin_payload.pop("scheduled_publish_time", None)
        fin_payload["video_state"] = "PUBLISHED"
        fin_r = requests.post(start_url, data=fin_payload, timeout=30)
    fin_r.raise_for_status()
    print(f"[SUCCESS] Published Facebook Reel! Video ID: {vid}")
    thumb_jpg = WORK_DIR / "thumbnail.jpg"
    if thumb_jpg.exists():
        with open(thumb_jpg, "rb") as tf:
            requests.post(f"https://graph.facebook.com/{GRAPH_API_VERSION}/{vid}/thumbnails", data={"is_preferred": "true", "access_token": access_token}, files={"source": ("thumbnail.jpg", tf, "image/jpeg")}, timeout=30)
    if comment_text.strip():
        try:
            fc = requests.post(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{vid}/comments",
                data={"message": comment_text.strip(), "access_token": access_token},
                timeout=20,
            )
            if fc.ok:
                print("[SUCCESS] Posted affiliate comment on Facebook Reel!")
            else:
                print(f"[INFO] Facebook comment skipped (HTTP {fc.status_code}): {fc.text[:160]}")
        except Exception as exc:
            print(f"[INFO] Facebook comment skipped: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    if "--publish-ig-only" in sys.argv:
        if not META_CACHE_FILE.exists():
            print("[SKIP] No cached Meta payload.")
            return
        meta = json.loads(META_CACHE_FILE.read_text(encoding="utf-8"))
        upload_to_instagram_reels(meta["reels_caption"], meta.get("comment_text", ""))
        return

    if "--publish-fb-only" in sys.argv:
        vid = WORK_DIR / "final_short_meta.mp4"
        if not (vid.exists() and META_CACHE_FILE.exists()):
            print("[SKIP] No cached FB Reel.")
            return
        meta = json.loads(META_CACHE_FILE.read_text(encoding="utf-8"))
        upload_to_facebook_reels(vid, meta["title"], meta["fb_caption"], meta.get("comment_text", ""))
        return

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    used_topics = load_used_topics()

    print("[1/6] Discovering today's Indian tech/gadget topic with Google Search...")
    plan = generate_tech_short_plan(used_topics)
    print(f"      Product/Topic: {plan.product_name} ({plan.price_inr_label}) | In India: {plan.available_in_india}")

    print("[2/6] Generating voiceover + dual platform outros...")
    main_mp3, main_srt = WORK_DIR / "main.mp3", WORK_DIR / "main.srt"
    synth_voiceover(plan.narration_malayalam, main_mp3, main_srt)
    yt_outro_mp3, meta_outro_mp3 = WORK_DIR / "yt_outro.mp3", WORK_DIR / "meta_outro.mp3"
    if plan.is_specific_product and plan.available_in_india:
        synth_voiceover(OUTRO_YT_LINK_ML, yt_outro_mp3)
        synth_voiceover(OUTRO_META_LINK_ML, meta_outro_mp3)
    else:
        synth_voiceover(OUTRO_STANDARD_ML, yt_outro_mp3)
        meta_outro_mp3 = yt_outro_mp3

    print("[3/6] Fetching official product photos & tech clips...")
    clips, lead_photo = gather_tech_visual_clips(plan, WORK_DIR)

    print("[4/6] Rendering 9:16 thumbnail + dual-outro 2D animated videos...")
    font_path = find_malayalam_font()
    thumb_jpg = WORK_DIR / "thumbnail.jpg"
    render_thumbnail_9x16(lead_photo, clips[0], plan.thumbnail_question_ml, plan.price_inr_label, thumb_jpg, font_path)
    final_yt_mp4 = WORK_DIR / "final_short_yt.mp4"
    final_meta_mp4 = WORK_DIR / "final_short_meta.mp4"
    assemble_dual_outro_shorts(plan, clips, main_mp3, main_srt, yt_outro_mp3, meta_outro_mp3, final_yt_mp4, final_meta_mp4)

    aff_block = build_affiliate_links_block(plan)
    print(f"[INFO] Affiliate tags loaded -> Amazon: {bool(os.getenv('AMAZON_AFFILIATE_TAG', '').strip())}, Flipkart: {bool(os.getenv('FLIPKART_AFFILIATE_ID', '').strip())}")
    META_CACHE_FILE.write_text(
        json.dumps(
            {
                "title": plan.title,
                "reels_caption": f"{plan.title}\n\n{plan.reels_caption_ml}{aff_block}",
                "fb_caption": f"{plan.title}\n\n{plan.reels_caption_ml}{aff_block}",
                "comment_text": f"{plan.product_name} ({plan.price_inr_label})\n{aff_block.strip()}" if aff_block.strip() else "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if SKIP_UPLOAD:
        return

    print("[5/6] Uploading to YouTube Shorts (8:00 PM IST publishAt)...")
    upload_to_youtube(final_yt_mp4, thumb_jpg, plan)
    save_used_topic(plan.topic_key, used_topics)
    print("[6/6] Done!")


if __name__ == "__main__":
    main()