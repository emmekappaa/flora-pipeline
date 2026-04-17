#!/usr/bin/env python3
"""
Flora Pipeline — automates adding a new flower to Flora: Flower of the Day iOS app.

Usage:
    python pipeline.py "Bellis perennis"

Outputs inside  results.xcassets/  (next to this script), mirroring the real
Xcode asset catalogue structure:

    results.xcassets/
        <slug>.imageset/
            home.png            ← main/widget PNG, transparent bg, max 492×492
            Contents.json
        <slug>-info.imageset/
            info.jpg            ← info screen JPG, under 1 MB
            Contents.json
        <slug>-lock.imageset/
            lock.png            ← lock screen PNG, transparent bg, max 200×200
            Contents.json
        flowers.dataset/
            flowers.json        ← shared array; new flower is upserted by latinName
            Contents.json

Environment variables required:
    GEMINI_API_KEY
"""

import io
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

# Load .env from the same directory as this script
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # dotenv not installed — rely on shell env vars


# ─────────────────────────────────────────────────────────────────── helpers ──

def make_slug(latin_name: str) -> str:
    return latin_name.lower().replace(" ", "-")


def make_imageset(xcassets_root: Path, name: str) -> Path:
    d = xcassets_root / f"{name}.imageset"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_contents_json(imageset_dir: Path, filename: str) -> None:
    """Write a valid Xcode Contents.json referencing the image for all scales."""
    contents = {
        "images": [
            {"filename": filename, "idiom": "universal", "scale": "1x"},
            {"filename": filename, "idiom": "universal", "scale": "2x"},
            {"filename": filename, "idiom": "universal", "scale": "3x"},
        ],
        "info": {"author": "xcode", "version": 1},
    }
    (imageset_dir / "Contents.json").write_text(
        json.dumps(contents, indent=2), encoding="utf-8"
    )


def ensure_dataset(xcassets_root: Path) -> Path:
    """Create flowers.dataset folder + Contents.json if absent; return its path."""
    ds = xcassets_root / "flowers.dataset"
    ds.mkdir(parents=True, exist_ok=True)
    contents_path = ds / "Contents.json"
    if not contents_path.exists():
        contents = {
            "data": [{"filename": "flowers.json", "idiom": "universal"}],
            "info": {"author": "xcode", "version": 1},
        }
        contents_path.write_text(
            json.dumps(contents, indent=2), encoding="utf-8"
        )
    return ds


def _resize_fit_transparent(img, max_size: int):
    """Resize image to fit within max_size×max_size, pad with transparency."""
    from PIL import Image

    img = img.convert("RGBA")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    canvas = Image.new("RGBA", (max_size, max_size), (0, 0, 0, 0))
    offset = ((max_size - img.width) // 2, (max_size - img.height) // 2)
    canvas.paste(img, offset, img)
    return canvas


def extract_petal_color(img) -> str:
    """Sample the dominant light colour from the flower centre (after bg removal)."""
    from collections import Counter

    from PIL import Image

    img = img.convert("RGBA")
    w, h = img.size
    mx, my = w // 5, h // 5
    cropped = img.crop((mx, my, w - mx, h - my)).resize((80, 80), Image.LANCZOS)
    pixels = list(cropped.getdata())

    # keep visible, non-dark, non-pure-white pixels; quantise to reduce noise
    buckets = []
    for r, g, b, a in pixels:
        if a > 100:
            brightness = (r + g + b) / 3
            if 80 < brightness < 235:
                buckets.append(((r // 25) * 25, (g // 25) * 25, (b // 25) * 25))

    if not buckets:
        # fall back to simple average of all opaque pixels
        visible = [(r, g, b) for r, g, b, a in pixels if a > 100 and r + g + b > 80]
        if not visible:
            return "#F0EBD8"
        r = sum(p[0] for p in visible) // len(visible)
        g = sum(p[1] for p in visible) // len(visible)
        b = sum(p[2] for p in visible) // len(visible)
        return f"#{r:02X}{g:02X}{b:02X}"

    r, g, b = Counter(buckets).most_common(1)[0][0]
    r = min(255, r + 12)
    g = min(255, g + 12)
    b = min(255, b + 12)
    return f"#{r:02X}{g:02X}{b:02X}"


# ─────────────────────────────────────────────────────────────────── Step 1 ──
# Wikipedia data

def step1_wikipedia(latin_name: str) -> dict:
    print("\n[Step 1] Fetching Wikipedia data…")
    try:
        import wikipediaapi

        wiki = wikipediaapi.Wikipedia(
            language="en",
            user_agent="FloraApp/1.0 (flora@example.com)",
        )
        page = wiki.page(latin_name)
        if not page.exists():
            page = wiki.page(latin_name.replace(" ", "_"))
        if not page.exists():
            raise ValueError(f"No Wikipedia page found for '{latin_name}'")

        summary = page.summary
        text = page.text

        # ── common name ──────────────────────────────────────────────────────
        common_name = ""
        patterns = [
            r"commonly (?:known|called) as ([^,;\.]+)",
            r"common names? (?:include|is|are) ([^,;\.]+)",
            r"known as ([^,;\.]+)",
            r"also called ([^,;\.]+)",
        ]
        for pat in patterns:
            m = re.search(pat, summary, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip().split(" or ")[0].strip()
                common_name = re.sub(r"^the\s+", "", candidate, flags=re.IGNORECASE)
                break

        if not common_name:
            title = page.title
            if title.lower() not in latin_name.lower():
                common_name = title
            else:
                common_name = latin_name.split()[-1].capitalize()

        # ── sections ─────────────────────────────────────────────────────────
        def find_section(keywords: list) -> str:
            for section in page.sections:
                for kw in keywords:
                    if kw.lower() in section.title.lower():
                        t = section.text.strip()
                        if t:
                            return t[:700]
                for sub in section.sections:
                    for kw in keywords:
                        if kw.lower() in sub.title.lower():
                            t = sub.text.strip()
                            if t:
                                return t[:700]
            return ""

        habitat = find_section(["habitat", "distribution", "ecology", "range"])
        etymology = find_section(["etymology", "nomenclature"])
        cultural_info = find_section(
            ["folk", "culture", "medicine", "use", "traditional", "history", "ethnob"]
        )

        if not habitat:
            habitat = (summary.split("\n")[0] if summary else "")[:400]

        wiki_desc = (summary.split("\n")[0] if summary else "")[:700]

        wiki_url = (
            f"https://en.wikipedia.org/wiki/{latin_name.replace(' ', '_')}"
        )

        print(f"  Common name: {common_name}")
        return {
            "name": common_name,
            "wikiDescription": wiki_desc,
            "habitat": habitat,
            "etymology": etymology,
            "culturalInfo": cultural_info,
            "wikipediaUrl": wiki_url,
            "_raw_summary": summary,
            "_raw_text": text[:5000],
        }

    except Exception as exc:
        print(f"  [Warning] Wikipedia step failed: {exc}")
        return {
            "name": latin_name.split()[-1].capitalize(),
            "wikiDescription": "",
            "habitat": "",
            "etymology": "",
            "culturalInfo": "",
            "wikipediaUrl": (
                f"https://en.wikipedia.org/wiki/{latin_name.replace(' ', '_')}"
            ),
            "_raw_summary": "",
            "_raw_text": "",
        }


# ─────────────────────────────────────────────────────────────────── Step 2 ──
# Care info from PFAF

def step2_pfaf(latin_name: str) -> list:
    print("\n[Step 2] Scraping PFAF care info…")
    try:
        import httpx
        from bs4 import BeautifulSoup

        url = (
            "https://pfaf.org/user/Plant.aspx"
            f"?LatinName={latin_name.replace(' ', '+')}"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = httpx.get(url, headers=headers, timeout=25, follow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        care_info = []

        # ── hardiness ─────────────────────────────────────────────────────
        hardiness_label = "Fully Hardy"
        zone_match = re.search(
            r"(?:usda\s+)?hardiness[:\s]+(\d+)\s*[-–to]+\s*(\d+)",
            page_text,
            re.IGNORECASE,
        )
        if zone_match:
            min_zone = int(zone_match.group(1))
            if min_zone <= 6:
                hardiness_label = "Fully Hardy"
            elif min_zone <= 9:
                hardiness_label = "Half Hardy"
            else:
                hardiness_label = "Frost Tender"
        else:
            pt_lower = page_text.lower()
            if "frost tender" in pt_lower or "tender" in pt_lower:
                hardiness_label = "Frost Tender"
            elif "half hardy" in pt_lower:
                hardiness_label = "Half Hardy"
            else:
                hardiness_label = "Fully Hardy"

        care_info.append({"icon": "snowflake", "label": hardiness_label})

        # ── soil moisture ──────────────────────────────────────────────────
        moisture_label = "Well Drained"
        pt_lower = page_text.lower()
        if re.search(r"\bmoist\b(?!\s*well)", pt_lower):
            moisture_label = "Moist Soil"
        elif re.search(r"\bwell.drained\b|\bwell\s+drained\b", pt_lower):
            moisture_label = "Well Drained"
        elif re.search(r"\bdry\b", pt_lower):
            moisture_label = "Dry Soil"

        care_info.append({"icon": "drop.fill", "label": moisture_label})

        # ── light requirements ─────────────────────────────────────────────
        shade_entries = []
        if re.search(r"\bfull\s+sun\b|\bno\s+shade\b", pt_lower):
            shade_entries.append({"icon": "sun.max.fill", "label": "Full Sun"})
        if re.search(
            r"\bsemi.shade\b|\bpart(?:ial)?\s+shade\b|\blight\s+shade\b|\bdappled\b",
            pt_lower,
        ):
            shade_entries.append({"icon": "cloud.sun.fill", "label": "Part Shade"})
        if re.search(r"\bfull\s+shade\b|\bdeep\s+shade\b", pt_lower):
            shade_entries.append({"icon": "moon.fill", "label": "Full Shade"})

        if not shade_entries:
            shade_entries = [{"icon": "sun.max.fill", "label": "Full Sun"}]

        care_info.extend(shade_entries)

        print(f"  Care info: {care_info}")
        return care_info

    except Exception as exc:
        print(f"  [Warning] PFAF scraping failed: {exc}")
        return [
            {"icon": "snowflake", "label": "Fully Hardy"},
            {"icon": "drop.fill", "label": "Well Drained"},
            {"icon": "sun.max.fill", "label": "Full Sun"},
        ]


# ─────────────────────────────────────────────────────────────────── Step 3 ──
# Images from Wikimedia Commons (CC0 / Public Domain only)

def step3_wikimedia(latin_name: str) -> tuple:
    """Returns (img1_bytes, img2_bytes, author_string)."""
    print("\n[Step 3] Fetching CC0/PD photos from Wikimedia Commons…")
    try:
        import httpx

        API = "https://commons.wikimedia.org/w/api.php"
        HEADERS = {"User-Agent": "FloraApp/1.0 (flora@example.com)"}

        # Accept CC0/PD and CC BY / CC BY-SA (commercial use allowed with attribution)
        def _is_free(license_str: str) -> bool:
            s = license_str.lower()
            return any(kw in s for kw in (
                "cc0", "pd", "public domain", "pdm", "cc-zero",
                "cc by", "cc-by",
            ))

        def _search_titles(query: str, limit: int = 30) -> list[str]:
            r = httpx.get(API, headers=HEADERS, params={
                "action": "query", "list": "search",
                "srsearch": query, "srnamespace": "6",
                "srlimit": str(limit), "format": "json",
            }, timeout=20)
            r.raise_for_status()
            return [h["title"] for h in r.json().get("query", {}).get("search", [])]

        # Keywords that suggest non-photographic content or problematic sources
        SKIP_WORDS = re.compile(
            r"artwork|art.project|illustration|drawing|painting|herbarium"
            r"|watercolor|lithograph|engraving|sketch|museum|naturalis"
            r"|greenhouse|favourite.flowers|garden.and.greenhouse"
            r"|stamp|colnect|rcin|royal.collection|postage"
            r"|flickr|\d{7,}",   # Flickr photo IDs / institutional IDs in filenames
            re.IGNORECASE,
        )

        def _get_info(titles: list[str]) -> list[dict]:
            """Fetch imageinfo + license metadata for a batch of file titles."""
            if not titles:
                return []
            r = httpx.get(API, headers=HEADERS, params={
                "action": "query", "titles": "|".join(titles),
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|mime|size|thumburl",
                "iiurlwidth": "1200",
                "format": "json",
            }, timeout=20)
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {}).values()
            results = []
            for page in pages:
                title = page.get("title", "")
                if SKIP_WORDS.search(title):
                    continue
                for ii in page.get("imageinfo", []):
                    mime = ii.get("mime", "")
                    if not mime.startswith("image/jpeg") and not mime.startswith("image/png"):
                        continue
                    meta = ii.get("extmetadata", {})
                    license_name = (
                        meta.get("LicenseShortName", {}).get("value", "") or
                        meta.get("License", {}).get("value", "")
                    )
                    if not _is_free(license_name):
                        continue
                    author = re.sub(r"<[^>]+>", "", (
                        meta.get("Artist", {}).get("value", "") or
                        meta.get("Credit", {}).get("value", "")
                    )).strip()[:80]
                    results.append({
                        "url": ii["url"],
                        "thumb_url": ii.get("thumburl", ""),
                        "width": ii.get("width", 0),
                        "height": ii.get("height", 0),
                        "author": author,
                        "license": license_name,
                    })
            results.sort(key=lambda x: x["width"] * x["height"], reverse=True)
            return results

        import urllib.request as _urllib_req

        _DL_UA = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

        def _download(item: dict) -> bytes | None:
            urls = [u for u in [item["url"], item.get("thumb_url", "")] if u]
            # De-duplicate while preserving order
            seen = set()
            urls = [u for u in urls if not (u in seen or seen.add(u))]
            for url in urls:
                try:
                    req = _urllib_req.Request(
                        url,
                        headers={
                            "User-Agent": _DL_UA,
                            "Referer": "https://commons.wikimedia.org/",
                            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.9",
                        },
                    )
                    with _urllib_req.urlopen(req, timeout=40) as resp:
                        return resp.read()
                except Exception as e:
                    print(f"  [Warning] Download failed ({url[:80]}): {e}")
            return None

        def _category_titles(category: str, limit: int = 30) -> list[str]:
            """List image files in a Wikimedia Commons category."""
            r = httpx.get(API, headers=HEADERS, params={
                "action": "query", "list": "categorymembers",
                "cmtitle": f"Category:{category}",
                "cmnamespace": "6",  # File namespace only
                "cmlimit": str(limit), "format": "json",
            }, timeout=20)
            r.raise_for_status()
            return [m["title"] for m in r.json().get("query", {}).get("categorymembers", [])]

        # Try progressively broader queries until we have ≥2 photos
        genus = latin_name.split()[0]
        queries = [
            f"{latin_name} flower photo",
            f"{latin_name} flower",
            f"{latin_name}",
            f"{genus} flower",
        ]
        candidates = []
        for q in queries:
            titles = _search_titles(q, limit=40)
            titles = [t for t in titles if re.search(r"\.(jpe?g|png)$", t, re.I)]
            batch = _get_info(titles[:25])
            for item in batch:
                if item not in candidates:
                    candidates.append(item)
            if len(candidates) >= 2:
                break

        # Fallback: browse Commons category directly
        if len(candidates) < 2:
            for cat in [latin_name, genus]:
                cat_titles = _category_titles(cat, limit=40)
                cat_titles = [t for t in cat_titles if re.search(r"\.(jpe?g|png)$", t, re.I)]
                batch = _get_info(cat_titles[:25])
                for item in batch:
                    if item not in candidates:
                        candidates.append(item)
                if len(candidates) >= 2:
                    break

        if not candidates:
            print("  [Warning] No free photos found on Wikimedia Commons")
            return None, None, ""

        # Try candidates in order until we get 2 successful downloads
        downloaded = []
        for item in candidates:
            data = _download(item)
            if data:
                downloaded.append((data, item))
                if len(downloaded) == 2:
                    break

        img1, img2, author = None, None, ""
        if len(downloaded) >= 1:
            img1, meta1 = downloaded[0]
            author = meta1["author"]
            print(f"  Image 1: {len(img1) // 1024} KB  license: {meta1['license']}  author: {author[:40]}")
        if len(downloaded) >= 2:
            img2, meta2 = downloaded[1]
            print(f"  Image 2: {len(img2) // 1024} KB  license: {meta2['license']}")
        elif len(downloaded) == 1:
            print("  [Warning] Only one downloadable photo found — using it for both images")
            img2 = img1

        return img1, img2, author

    except Exception as exc:
        print(f"  [Warning] Wikimedia step failed: {exc}")
        return None, None, ""


# ─────────────────────────────────────────────────────────────────── Step 4 ──
# Process real photos: home.png (bg removed) + info.jpg (compressed)

def step4_process_images(
    img1_bytes, img2_bytes, xcassets_root: Path, slug: str
) -> dict:
    print("\n[Step 4] Processing images…")
    result = {
        "img1_bytes": img1_bytes,   # passed through to step 5 as Gemini reference
        "img1_path": None,
        "img2_path": None,
        "petal_color": "#F0EBD8",
    }

    try:
        from PIL import Image
        import numpy as np
        from rembg import remove, new_session

        # ── Image 1: home widget — remove bg, fit 492×492 PNG ─────────────
        if img1_bytes:
            print("  Image 1: removing background…")
            try:
                session = new_session("isnet-general-use")
                bg_removed = remove(img1_bytes, session=session)
                img1 = Image.open(io.BytesIO(bg_removed)).convert("RGBA")

                # Sanity check: if rembg wiped most of the flower, fall back
                alpha = np.array(img1)[:, :, 3]
                visible_pct = (alpha > 10).sum() / alpha.size
                print(f"  BG removal: {visible_pct:.1%} pixels visible")
                if visible_pct < 0.10:
                    print(
                        f"  [Warning] BG removal left only {visible_pct:.1%} visible "
                        "— using original image"
                    )
                    img1 = Image.open(io.BytesIO(img1_bytes)).convert("RGBA")

                # Crop to bounding box of visible pixels so the flower fills the frame
                bbox = img1.getbbox()
                if bbox:
                    img1 = img1.crop(bbox)

                img1 = _resize_fit_transparent(img1, 492)
                result["petal_color"] = extract_petal_color(img1)

                imageset = make_imageset(xcassets_root, slug)
                fname = "home.png"
                img1.save(str(imageset / fname), "PNG")
                write_contents_json(imageset, fname)
                result["img1_path"] = imageset / fname
                print(f"  Saved: {imageset / fname}  petal colour: {result['petal_color']}")
            except Exception as e:
                print(f"  [Warning] Image 1 processing failed: {e}")

        # ── Image 2: info screen — keep bg, compress to <1 MB JPG ─────────
        if img2_bytes:
            print("  Image 2: compressing for info screen…")
            try:
                img2 = Image.open(io.BytesIO(img2_bytes)).convert("RGB")
                imageset = make_imageset(xcassets_root, f"{slug}-info")
                fname = "info.jpg"

                quality = 88
                while True:
                    buf = io.BytesIO()
                    img2.save(buf, "JPEG", quality=quality, optimize=True)
                    if buf.tell() < 1_000_000:
                        break
                    w, h = img2.size
                    img2 = img2.resize(
                        (int(w * 0.9), int(h * 0.9)), Image.LANCZOS
                    )
                    quality = max(60, quality - 5)

                (imageset / fname).write_bytes(buf.getvalue())
                write_contents_json(imageset, fname)
                result["img2_path"] = imageset / fname
                print(f"  Saved: {imageset / fname}  ({buf.tell() // 1024} KB)")
            except Exception as e:
                print(f"  [Warning] Image 2 processing failed: {e}")

    except Exception as exc:
        print(f"  [Warning] Image processing step failed: {exc}")

    return result


# ─────────────────────────────────────────────────────────────────── Step 5 ──
# Generate lock.png via Gemini (monochromatic botanical icon)

def step5_gemini_lock(
    common_name: str,
    latin_name: str,
    img1_bytes,
    xcassets_root: Path,
    slug: str,
) -> bool:
    print("\n[Step 5] Generating lock screen image via Gemini…")
    try:
        from google import genai
        from google.genai import types
        from PIL import Image

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")

        client = genai.Client(api_key=api_key)

        lock_prompt = (
            f"Flat botanical icon of a {common_name} ({latin_name}). "
            "Composition: one large flower head prominently at the top-center, "
            "a single straight stem, and 2–3 simple leaves below. "
            "The entire illustration — flower, petals, stem, leaves — is filled "
            "with one single solid color. White background. "
            "No gradients, no shading, no outlines, no second color anywhere. "
            "Bold graphic style like a linocut stamp or app icon. "
            "The flower must be clearly recognizable and fill most of the frame."
        )

        # Use iNaturalist photo as visual reference
        if img1_bytes:
            ref_part = types.Part.from_bytes(data=img1_bytes, mime_type="image/jpeg")
            contents = [ref_part, lock_prompt]
        else:
            contents = [lock_prompt]

        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"]
            ),
        )

        generated_bytes = None
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                raw = part.inline_data.data
                if isinstance(raw, str):
                    import base64
                    raw = base64.b64decode(raw)
                generated_bytes = raw
                break

        if not generated_bytes:
            raise ValueError("Gemini returned no image data")

        # For flat Gemini icons (solid color + white bg) use threshold removal,
        # which gives much cleaner transparency than rembg.
        img_lock = Image.open(io.BytesIO(generated_bytes)).convert("RGBA")
        import numpy as np
        arr = np.array(img_lock)
        # Mark near-white pixels as fully transparent
        white_mask = (arr[:, :, 0] > 240) & (arr[:, :, 1] > 240) & (arr[:, :, 2] > 240)
        arr[white_mask, 3] = 0
        img_lock = Image.fromarray(arr)

        # Crop to visible content, then fit in 200×200
        bbox = img_lock.getbbox()
        if bbox:
            img_lock = img_lock.crop(bbox)
        img_lock = _resize_fit_transparent(img_lock, 200)

        imageset = make_imageset(xcassets_root, f"{slug}-lock")
        fname = "lock.png"
        img_lock.save(str(imageset / fname), "PNG")
        write_contents_json(imageset, fname)
        print(f"  Saved: {imageset / fname}")
        return True

    except Exception as exc:
        print(f"  [Warning] Gemini lock screen step failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────── Step 6 ──
# Enhance English fields + translations via Gemini

def step6_enhance_and_translate(wiki_data: dict) -> tuple:
    """Returns (enhanced_english: dict, translations: dict)."""
    print("\n[Step 6] Enhancing content & generating translations via Gemini…")
    try:
        from google import genai
        from google.genai import types

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")

        client = genai.Client(api_key=api_key)

        latin_name = wiki_data.get("latinName", "")
        name = wiki_data.get("name", "")
        wiki_desc = wiki_data.get("wikiDescription", "")
        habitat = wiki_data.get("habitat", "")
        etymology = wiki_data.get("etymology", "")
        cultural = wiki_data.get("culturalInfo", "")
        raw_summary = wiki_data.get("_raw_summary", "")

        prompt = f"""You are a botanical content writer and multilingual translator for \
'Flora: Flower of the Day', a beautiful iOS app. \
You write evocative, poetic content and produce accurate, natural translations. \
Return ONLY valid JSON — no markdown fences, no prose outside the JSON.

Here is Wikipedia data about '{name}' ({latin_name}):

SUMMARY (first ~2 000 chars):
{raw_summary[:2000]}

WIKIPEDIA SECTIONS:
Habitat: {habitat[:600]}
Etymology: {etymology[:500]}
Cultural uses: {cultural[:500]}

TASKS
1. Write a one-sentence DESCRIPTION — poetic/emotional (NOT botanical). ~20–30 words.
   Example: "A cheerful symbol of innocence and new beginnings that has delighted meadow-walkers for centuries."
2. Write a one-sentence FUN FACT — curious or surprising. Focus on etymology or unusual behaviour.
3. Produce clean HABITAT — 1–2 clear sentences. If the section was empty, derive from the summary.
4. Produce clean ETYMOLOGY — 1–2 clear sentences. If empty, write "Origin of the name is uncertain."
5. Produce clean CULTURAL INFO — 1–2 clear sentences about folk or cultural uses. If empty, write something from the summary.
6. Confirm the best common English NAME for this flower.

Then TRANSLATE all six fields PLUS wikiDescription into:
German (de), French (fr), Spanish (es), Italian (it), Chinese Simplified (zh), Japanese (ja).

wikiDescription to translate:
{wiki_desc[:500]}

Return ONLY this JSON (fill every string value, no nulls):
{{
  "english": {{
    "name": "",
    "description": "",
    "funFact": "",
    "habitat": "",
    "etymology": "",
    "culturalInfo": ""
  }},
  "translations": {{
    "de": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}},
    "fr": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}},
    "es": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}},
    "it": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}},
    "zh": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}},
    "ja": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}}
  }}
}}"""

        import time
        last_exc = None
        response = None
        for attempt in range(4):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    ),
                )
                break
            except Exception as e:
                last_exc = e
                if attempt < 3:
                    wait = 15 * (attempt + 1)
                    print(f"  [Retry {attempt + 1}/3] {e} — retrying in {wait}s…")
                    time.sleep(wait)
        if response is None:
            raise last_exc

        raw = response.text.strip()
        # Strip any accidental markdown fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        parsed = json.loads(raw)
        english = parsed.get("english", {})
        translations = parsed.get("translations", {})

        print(f"  Enhanced name   : {english.get('name', name)}")
        print(f"  Languages ready : {list(translations.keys())}")
        return english, translations

    except Exception as exc:
        print(f"  [Warning] Gemini step failed: {exc}")
        fallback_en = {
            "name": wiki_data.get("name", ""),
            "description": "A beautiful flower cherished by nature lovers around the world.",
            "funFact": "This flower has fascinated botanists and poets for centuries.",
            "habitat": wiki_data.get("habitat", ""),
            "etymology": wiki_data.get("etymology", ""),
            "culturalInfo": wiki_data.get("culturalInfo", ""),
        }
        return fallback_en, {}


# ─────────────────────────────────────────────────────────────────── Step 7 ──
# Upsert flower entry into flowers.dataset/flowers.json

def step7_update_dataset(
    latin_name: str,
    slug: str,
    wiki_data: dict,
    care_info: list,
    processed: dict,
    english: dict,
    translations: dict,
    observer: str,
    xcassets_root: Path,
) -> dict:
    print("\n[Step 7] Updating flowers.dataset/flowers.json…")

    today = date.today()
    final_name = (
        english.get("name")
        or wiki_data.get("name")
        or latin_name.split()[-1].capitalize()
    )

    flower = {
        "name": final_name,
        "latinName": latin_name,
        "description": english.get("description", ""),
        "funFact": english.get("funFact", ""),
        "petalColorHex": processed.get("petal_color", "#F0EBD8"),
        "imageName": slug,
        "lockImageName": f"{slug}-lock",
        "infoImageName": f"{slug}-info",
        "infoImageAuthor": observer,
        "careInfo": care_info,
        "year": today.year,
        "month": today.month,
        "day": today.day,
        "wikiDescription": wiki_data.get("wikiDescription", ""),
        "habitat": english.get("habitat") or wiki_data.get("habitat", ""),
        "etymology": english.get("etymology") or wiki_data.get("etymology", ""),
        "culturalInfo": (
            english.get("culturalInfo") or wiki_data.get("culturalInfo", "")
        ),
        "wikipediaUrl": wiki_data.get("wikipediaUrl", ""),
        "translations": translations,
    }

    dataset_dir = ensure_dataset(xcassets_root)
    flowers_path = dataset_dir / "flowers.json"

    # Load existing array if present
    if flowers_path.exists():
        try:
            existing = json.loads(flowers_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    else:
        existing = []

    # Upsert: replace existing entry with same latinName, or append
    updated = [f for f in existing if f.get("latinName") != latin_name]
    updated.append(flower)

    flowers_path.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  Saved: {flowers_path}  ({len(updated)} flower(s) total)")
    return flower


# ─────────────────────────────────────────────────────────────────────── main ──

def main() -> None:
    if len(sys.argv) != 2:
        print('Usage: python pipeline.py "Latin Name"')
        print('Example: python pipeline.py "Bellis perennis"')
        sys.exit(1)

    latin_name = sys.argv[1].strip()
    slug = make_slug(latin_name)

    # Output always goes into results.xcassets/ next to this script
    xcassets_root = Path(__file__).parent / "results.xcassets"
    xcassets_root.mkdir(parents=True, exist_ok=True)

    print("┌─────────────────────────────────────────┐")
    print(f"  Flora Pipeline — {latin_name}")
    print(f"  Slug: {slug}")
    print(f"  Output: {xcassets_root}/")
    print("└─────────────────────────────────────────┘")

    # ── Step 1: Wikipedia ─────────────────────────────────────────────────
    wiki_data = step1_wikipedia(latin_name)
    wiki_data["latinName"] = latin_name

    # ── Step 2: PFAF care info ────────────────────────────────────────────
    care_info = step2_pfaf(latin_name)

    # ── Step 3: Wikimedia Commons CC0/PD photos ──────────────────────────
    img1_bytes, img2_bytes, observer = step3_wikimedia(latin_name)

    # ── Step 4: Process images ────────────────────────────────────────────
    processed = step4_process_images(img1_bytes, img2_bytes, xcassets_root, slug)

    # ── Step 5: Gemini — lock.png only ──────────────────────────────────
    step5_gemini_lock(
        wiki_data.get("name") or latin_name,
        latin_name,
        processed.get("img1_bytes"),
        xcassets_root,
        slug,
    )

    # ── Step 6: Claude — enhance + translate ─────────────────────────────
    english, translations = step6_enhance_and_translate(wiki_data)

    # ── Step 7: Upsert into flowers.dataset/flowers.json ─────────────────
    step7_update_dataset(
        latin_name=latin_name,
        slug=slug,
        wiki_data=wiki_data,
        care_info=care_info,
        processed=processed,
        english=english,
        translations=translations,
        observer=observer,
        xcassets_root=xcassets_root,
    )

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n✓ Pipeline complete — output in: {xcassets_root}/")
    files = sorted(xcassets_root.rglob("*"))
    for f in files:
        if f.is_file():
            size_kb = f.stat().st_size // 1024
            rel = f.relative_to(xcassets_root)
            tag = f"  ({size_kb} KB)" if size_kb > 0 else ""
            print(f"    {rel}{tag}")


if __name__ == "__main__":
    main()
