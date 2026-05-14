#!/usr/bin/env python3
"""
Flora Agent — agentic version of flora-pipeline.

Gemini (via Google GenAI SDK function calling) autonomously orchestrates every step:
it decides which tools to call, in what order, and how to recover from failures.

Usage:
    python agent.py "Bellis perennis"

Environment variables required:
    GEMINI_API_KEY
"""

import base64
import io
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    # look in agent dir first, then parent (shared with pipeline)
    load_dotenv(Path(__file__).parent / ".env")
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from google import genai
from google.genai import types as gtypes


# ──────────────────────────────────────────────────────────────── helpers ──

def _make_slug(latin_name: str) -> str:
    return latin_name.lower().replace(" ", "-")


def _make_imageset(xcassets_root: Path, name: str) -> Path:
    d = xcassets_root / f"{name}.imageset"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_contents_json(imageset_dir: Path, filename: str) -> None:
    contents = {
        "images": [
            {"filename": filename, "idiom": "universal", "scale": "1x"},
            {"filename": filename, "idiom": "universal", "scale": "2x"},
            {"filename": filename, "idiom": "universal", "scale": "3x"},
        ],
        "info": {"author": "xcode", "version": 1},
    }
    (imageset_dir / "Contents.json").write_text(json.dumps(contents, indent=2), encoding="utf-8")


def _ensure_dataset(xcassets_root: Path) -> Path:
    ds = xcassets_root / "flowers.dataset"
    ds.mkdir(parents=True, exist_ok=True)
    cp = ds / "Contents.json"
    if not cp.exists():
        cp.write_text(json.dumps({
            "data": [{"filename": "flowers.json", "idiom": "universal"}],
            "info": {"author": "xcode", "version": 1},
        }, indent=2), encoding="utf-8")
    return ds


def _resize_fit_transparent(img, max_size: int):
    from PIL import Image
    img = img.convert("RGBA")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    canvas = Image.new("RGBA", (max_size, max_size), (0, 0, 0, 0))
    offset = ((max_size - img.width) // 2, (max_size - img.height) // 2)
    canvas.paste(img, offset, img)
    return canvas


def _extract_petal_color(img) -> str:
    from collections import Counter
    from PIL import Image
    img = img.convert("RGBA")
    w, h = img.size
    mx, my = w // 5, h // 5
    cropped = img.crop((mx, my, w - mx, h - my)).resize((80, 80), Image.LANCZOS)
    pixels = list(cropped.getdata())
    buckets = []
    for r, g, b, a in pixels:
        if a > 100:
            brightness = (r + g + b) / 3
            if 80 < brightness < 235:
                buckets.append(((r // 25) * 25, (g // 25) * 25, (b // 25) * 25))
    if not buckets:
        visible = [(r, g, b) for r, g, b, a in pixels if a > 100 and r + g + b > 80]
        if not visible:
            return "#F0EBD8"
        r = sum(p[0] for p in visible) // len(visible)
        g = sum(p[1] for p in visible) // len(visible)
        b = sum(p[2] for p in visible) // len(visible)
        return f"#{r:02X}{g:02X}{b:02X}"
    r, g, b = Counter(buckets).most_common(1)[0][0]
    return f"#{min(255, r+12):02X}{min(255, g+12):02X}{min(255, b+12):02X}"


# ──────────────────────────────────────────────────── tool implementations ──

def tool_fetch_wikipedia(latin_name: str) -> dict:
    """Fetch and parse Wikipedia data for the flower."""
    try:
        import wikipediaapi
        wiki = wikipediaapi.Wikipedia(language="en", user_agent="FloraApp/1.0 (flora@example.com)")
        page = wiki.page(latin_name)
        if not page.exists():
            page = wiki.page(latin_name.replace(" ", "_"))
        if not page.exists():
            return {"error": f"No Wikipedia page found for '{latin_name}'",
                    "name": latin_name.split()[-1].capitalize(), "latinName": latin_name,
                    "wikiDescription": "", "habitat": "", "etymology": "", "culturalInfo": "",
                    "wikipediaUrl": f"https://en.wikipedia.org/wiki/{latin_name.replace(' ', '_')}",
                    "_raw_summary": ""}

        summary = page.summary

        common_name = ""
        for pat in [
            r"commonly (?:known|called) as ([^,;\.]+)",
            r"common names? (?:include|is|are) ([^,;\.]+)",
            r"known as ([^,;\.]+)",
            r"also called ([^,;\.]+)",
        ]:
            m = re.search(pat, summary, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip().split(" or ")[0].strip()
                common_name = re.sub(r"^the\s+", "", candidate, flags=re.IGNORECASE)
                break
        if not common_name:
            title = page.title
            common_name = title if title.lower() not in latin_name.lower() else latin_name.split()[-1].capitalize()

        def find_section(keywords):
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
        cultural_info = find_section(["folk", "culture", "medicine", "use", "traditional", "history"])

        if not habitat:
            habitat = (summary.split("\n")[0] if summary else "")[:400]

        return {
            "name": common_name,
            "latinName": latin_name,
            "wikiDescription": (summary.split("\n")[0] if summary else "")[:700],
            "habitat": habitat,
            "etymology": etymology,
            "culturalInfo": cultural_info,
            "wikipediaUrl": f"https://en.wikipedia.org/wiki/{latin_name.replace(' ', '_')}",
            "_raw_summary": summary[:2000],
        }
    except Exception as e:
        return {
            "error": str(e),
            "name": latin_name.split()[-1].capitalize(), "latinName": latin_name,
            "wikiDescription": "", "habitat": "", "etymology": "", "culturalInfo": "",
            "wikipediaUrl": f"https://en.wikipedia.org/wiki/{latin_name.replace(' ', '_')}",
            "_raw_summary": "",
        }


def tool_fetch_care_info(latin_name: str) -> dict:
    """Scrape care info from PFAF, falling back to Gemini."""
    pfaf_err = None
    try:
        import httpx
        from bs4 import BeautifulSoup

        url = f"https://pfaf.org/user/Plant.aspx?LatinName={latin_name.replace(' ', '+')}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = httpx.get(url, headers=headers, timeout=25, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        if any(p in page_text for p in ["not found in the database", "No plant found", "search did not"]):
            raise ValueError(f"'{latin_name}' not in PFAF database")

        img_alts = {img.get("alt", "").strip() for img in soup.find_all("img") if img.get("alt")}

        HARDINESS = {"Fully Hardy": "Fully Hardy", "Frost Hardy": "Frost Hardy", "Half Hardy": "Half Hardy", "Tender": "Half Hardy"}
        MOISTURE  = {"Well drained soil": "Well Drained Soil", "Moist Soil": "Moist Soil", "Wet Soil": "Wet Soil", "Water Plants": "Water Plants"}
        LIGHT     = {"Full sun": ("sun.max.fill", "Full Sun"), "Semi-shade": ("cloud.sun.fill", "Part Shade"), "Full shade": ("moon.fill", "Full Shade")}

        care_info = []
        for alt, label in HARDINESS.items():
            if alt in img_alts:
                care_info.append({"icon": "snowflake", "label": label})
                break
        for alt, label in MOISTURE.items():
            if alt in img_alts:
                care_info.append({"icon": "drop.fill", "label": label})
        for alt, (icon, label) in LIGHT.items():
            if alt in img_alts:
                care_info.append({"icon": icon, "label": label})

        seen: set = set()
        care_info = [e for e in care_info if not (e["label"] in seen or seen.add(e["label"]))]

        if not care_info:
            raise ValueError("No care icons found in PFAF page")

        return {"care_info": care_info, "source": "pfaf"}

    except Exception as e:
        pfaf_err = str(e)

    # Gemini fallback
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
        prompt = f"""Return care info for "{latin_name}" as a JSON array.
Use ONLY these exact values:
Hardiness (one): {{"icon":"snowflake","label":"Fully Hardy"}} | "Frost Hardy" | "Half Hardy" | "Tender"
Moisture (one+): {{"icon":"drop.fill","label":"Well Drained Soil"}} | "Moist Soil" | "Wet Soil" | "Water Plants"
Light (one+):    {{"icon":"sun.max.fill","label":"Full Sun"}} | {{"icon":"cloud.sun.fill","label":"Part Shade"}} | {{"icon":"moon.fill","label":"Full Shade"}}
Return ONLY a JSON array."""
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite", contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return {"care_info": json.loads(resp.text.strip()), "source": "gemini_fallback"}
    except Exception as e2:
        return {
            "care_info": [
                {"icon": "snowflake", "label": "Fully Hardy"},
                {"icon": "drop.fill",  "label": "Well Drained Soil"},
                {"icon": "sun.max.fill", "label": "Full Sun"},
            ],
            "source": "default_fallback",
            "warning": f"PFAF: {pfaf_err}  |  Gemini: {e2}",
        }


def tool_fetch_photos(latin_name: str, temp_dir: str) -> dict:
    """Download candidate CC0/free photos from Wikimedia Commons into temp_dir.
    Returns paths sorted by quality (Gemini-judged best first)."""
    try:
        import httpx
        import urllib.request as _urllib_req

        temp_path = Path(temp_dir)
        temp_path.mkdir(parents=True, exist_ok=True)

        API     = "https://commons.wikimedia.org/w/api.php"
        HEADERS = {"User-Agent": "FloraApp/1.0 (flora@example.com)"}

        def _is_free(s: str) -> bool:
            s = s.lower()
            return any(kw in s for kw in ("cc0", "pd", "public domain", "pdm", "cc-zero", "cc by", "cc-by"))

        SKIP_WORDS   = re.compile(r"artwork|illustration|drawing|painting|herbarium|watercolor|lithograph|stamp|colnect|rcin|royal.collection|postage|flickr|\d{7,}", re.IGNORECASE)
        DETAIL_WORDS = re.compile(r"\bleaf\b|\bleaves\b|\bbranch\b|\bstem\b|\bdetail\b|\bmacro\b|\bclose.up\b|\bseed\b|\bfruit\b|\broot\b|\bbee\b|\bbees\b|\binsect\b|\bbutterfly\b|\bbug\b|\bpollen\b", re.IGNORECASE)
        FLOWER_WORDS = re.compile(r"\bflower\b|\bbloom\b|\bplant\b|\bblossom\b|\binflorescence\b", re.IGNORECASE)

        def _score(item):
            t = item.get("title", "")
            if DETAIL_WORDS.search(t): return -1
            if FLOWER_WORDS.search(t): return 2
            return 1

        def _search_titles(query, limit=30):
            r = httpx.get(API, headers=HEADERS, params={
                "action": "query", "list": "search", "srsearch": query,
                "srnamespace": "6", "srlimit": str(limit), "format": "json",
            }, timeout=20)
            r.raise_for_status()
            return [h["title"] for h in r.json().get("query", {}).get("search", [])]

        def _get_info(titles):
            if not titles: return []
            r = httpx.get(API, headers=HEADERS, params={
                "action": "query", "titles": "|".join(titles), "prop": "imageinfo",
                "iiprop": "url|extmetadata|mime|size|thumburl", "iiurlwidth": "1500", "format": "json",
            }, timeout=20)
            r.raise_for_status()
            results = []
            for page in r.json().get("query", {}).get("pages", {}).values():
                title = page.get("title", "")
                if SKIP_WORDS.search(title): continue
                for ii in page.get("imageinfo", []):
                    mime = ii.get("mime", "")
                    if not mime.startswith(("image/jpeg", "image/png")): continue
                    meta = ii.get("extmetadata", {})
                    license_name = meta.get("LicenseShortName", {}).get("value", "") or meta.get("License", {}).get("value", "")
                    if not _is_free(license_name): continue
                    author = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "") or meta.get("Credit", {}).get("value", "")).strip()[:80]
                    results.append({"title": title, "url": ii["url"], "thumb_url": ii.get("thumburl", ""),
                                    "width": ii.get("width", 0), "height": ii.get("height", 0),
                                    "author": author, "license": license_name})
            results.sort(key=lambda x: (_score(x), x["width"] * x["height"]), reverse=True)
            return results

        DL_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Referer": "https://commons.wikimedia.org/"}

        def _download(item):
            for url in filter(None, [item.get("thumb_url", ""), item["url"]]):
                try:
                    req = _urllib_req.Request(url, headers=DL_HEADERS)
                    with _urllib_req.urlopen(req, timeout=40) as resp:
                        return resp.read()
                except Exception:
                    continue
            return None

        genus      = latin_name.split()[0]
        candidates = []
        for q in [f"{latin_name} flower photo", f"{latin_name} flower", latin_name, f"{genus} flower"]:
            titles = [t for t in _search_titles(q, 40) if re.search(r"\.(jpe?g|png)$", t, re.I)]
            for item in _get_info(titles[:25]):
                if item not in candidates:
                    candidates.append(item)
            if len(candidates) >= 4:
                break

        if len(candidates) < 4:
            for cat in [latin_name, genus]:
                r = httpx.get(API, headers=HEADERS, params={
                    "action": "query", "list": "categorymembers", "cmtitle": f"Category:{cat}",
                    "cmnamespace": "6", "cmlimit": "40", "format": "json",
                }, timeout=20)
                r.raise_for_status()
                cat_titles = [t for t in [m["title"] for m in r.json().get("query", {}).get("categorymembers", [])] if re.search(r"\.(jpe?g|png)$", t, re.I)]
                for item in _get_info(cat_titles[:25]):
                    if item not in candidates:
                        candidates.append(item)
                if len(candidates) >= 4:
                    break

        if not candidates:
            return {"error": "No free photos found on Wikimedia Commons", "photos": []}

        saved = []
        for i, item in enumerate(candidates[:4]):
            data = _download(item)
            if not data:
                continue
            ext  = "jpg" if re.search(r"jpe?g", item["url"], re.I) else "png"
            path = temp_path / f"photo_{i}.{ext}"
            path.write_bytes(data)
            saved.append({"path": str(path), "title": item["title"], "author": item["author"],
                          "license": item["license"], "width": item["width"], "height": item["height"]})
            time.sleep(1.0)

        if not saved:
            return {"error": "Failed to download any photos", "photos": []}

        best_idx = 0
        if len(saved) > 1:
            try:
                from google import genai
                from google.genai import types as gtypes
                from PIL import Image as PILImage

                gclient = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
                parts = []
                for i, photo in enumerate(saved):
                    parts.append(f"Photo {i+1}:")
                    img = PILImage.open(photo["path"]).convert("RGB")
                    img.thumbnail((800, 800), PILImage.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, "JPEG", quality=75)
                    parts.append(gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
                parts.append(
                    f"Select the best photo for a flower home widget for {latin_name}.\n"
                    "Criteria: petals clearly visible, simple/blurred background, close-up of flower head.\n"
                    "Reject: any insect visible, seed heads, wide landscape shots, leaves only.\n"
                    f"Reply with ONLY a single digit (1 through {len(saved)})."
                )
                resp = gclient.models.generate_content(
                    model="gemini-2.5-flash-lite", contents=parts,
                    config=gtypes.GenerateContentConfig(response_mime_type="text/plain"),
                )
                digit = re.search(r"[1-4]", resp.text.strip())
                if digit:
                    best_idx = min(int(digit.group()) - 1, len(saved) - 1)
            except Exception as e:
                print(f"  [Warning] Gemini photo judge failed: {e}")

        ordered = [saved[best_idx]] + [p for i, p in enumerate(saved) if i != best_idx]
        return {
            "photos": ordered,
            "home_photo":  ordered[0]["path"],
            "info_photo":  ordered[1]["path"] if len(ordered) > 1 else ordered[0]["path"],
            "info_author": ordered[1]["author"] if len(ordered) > 1 else ordered[0]["author"],
        }

    except Exception as e:
        return {"error": str(e), "photos": []}


def tool_process_home_image(photo_path: str, slug: str, xcassets_root: str) -> dict:
    """Remove background via Gemini, save as home.png (492×492 transparent PNG).
    Returns the output path and dominant petal color hex."""
    try:
        import numpy as np
        from PIL import Image, ImageOps
        from google import genai
        from google.genai import types

        xcassets  = Path(xcassets_root)
        gclient   = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))

        raw_img = ImageOps.exif_transpose(Image.open(photo_path)).convert("RGB")
        buf = io.BytesIO()
        raw_img.thumbnail((1024, 1024), Image.LANCZOS)
        raw_img.save(buf, "JPEG", quality=88)

        resp = gclient.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[
                types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"),
                "Remove the background from this flower photo. "
                "Output the single main flower isolated on a solid pure white (#FFFFFF) background. "
                "CRITICAL RULES: "
                "1. Do NOT convert to grayscale — keep all flower colors exactly as they are. "
                "2. Background must be pure white (#FFFFFF), not grey, not transparent. "
                "3. Keep ALL petals complete and intact. "
                "4. Remove everything that is not the main flower head. "
                "Result: one flower in full color on a pure white background.",
            ],
            config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
        )

        generated_bytes = None
        for part in resp.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                raw = part.inline_data.data
                if isinstance(raw, str):
                    raw = base64.b64decode(raw)
                generated_bytes = raw
                break

        if not generated_bytes:
            return {"error": "Gemini returned no image data"}

        img  = Image.open(io.BytesIO(generated_bytes)).convert("RGBA")
        arr  = np.array(img, dtype=np.uint8)
        h_px, w_px = arr.shape[:2]

        # Detect and flood-fill background from borders
        border_px = np.concatenate([arr[0, :, :3], arr[-1, :, :3], arr[:, 0, :3], arr[:, -1, :3]]).astype(np.float32)
        bg_color  = np.median(border_px, axis=0)
        if bg_color.mean() < 150:
            return {"error": "Background detection failed — image may already be dark or unprocessed"}

        dist_from_bg = np.sqrt(((arr[:, :, :3].astype(np.float32) - bg_color) ** 2).sum(axis=2))
        near_bg  = dist_from_bg < 40
        reachable = np.zeros((h_px, w_px), dtype=bool)
        reachable[0, :]  = near_bg[0, :]
        reachable[-1, :] = near_bg[-1, :]
        reachable[:, 0]  = near_bg[:, 0]
        reachable[:, -1] = near_bg[:, -1]
        for _ in range(max(h_px, w_px)):
            expanded = np.roll(reachable, 1, 0) | np.roll(reachable, -1, 0) | np.roll(reachable, 1, 1) | np.roll(reachable, -1, 1)
            new = near_bg & expanded & ~reachable
            if not new.any():
                break
            reachable |= new
        arr[reachable, 3] = 0
        img = Image.fromarray(arr)

        alpha       = arr[:, :, 3]
        visible_pct = (alpha > 10).sum() / alpha.size
        if visible_pct < 0.10:
            return {"error": f"Too little flower visible ({visible_pct:.1%}) after bg removal — try a different photo"}

        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        img         = _resize_fit_transparent(img, 492)
        petal_color = _extract_petal_color(img)

        imageset = _make_imageset(xcassets, slug)
        fname    = "home.png"
        img.save(str(imageset / fname), "PNG")
        _write_contents_json(imageset, fname)

        return {"path": str(imageset / fname), "petal_color": petal_color, "visible_pct": round(visible_pct, 3)}

    except Exception as e:
        return {"error": str(e)}


def tool_process_info_image(photo_path: str, slug: str, xcassets_root: str) -> dict:
    """Compress photo to <1 MB JPEG and save as info.jpg. Returns the output path."""
    try:
        from PIL import Image, ImageOps

        xcassets = Path(xcassets_root)
        img      = ImageOps.exif_transpose(Image.open(photo_path)).convert("RGB")
        imageset = _make_imageset(xcassets, f"{slug}-info")
        fname    = "info.jpg"

        quality = 88
        while True:
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality, optimize=True)
            if buf.tell() < 1_000_000:
                break
            w, h    = img.size
            img     = img.resize((int(w * 0.9), int(h * 0.9)), Image.LANCZOS)
            quality = max(60, quality - 5)

        (imageset / fname).write_bytes(buf.getvalue())
        _write_contents_json(imageset, fname)
        return {"path": str(imageset / fname), "size_kb": buf.tell() // 1024}

    except Exception as e:
        return {"error": str(e)}


def tool_generate_lock_image(latin_name: str, common_name: str, reference_photo_path: str, slug: str, xcassets_root: str) -> dict:
    """Generate a monochromatic botanical lock screen icon via Gemini. Returns the output path."""
    try:
        import numpy as np
        from PIL import Image
        from google import genai
        from google.genai import types

        xcassets = Path(xcassets_root)
        gclient  = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))

        lock_prompt = (
            f"Flat botanical icon of a {common_name} ({latin_name}). "
            "Composition: one large flower head at top-center, single straight stem, 2-3 simple leaves below. "
            "CRITICAL: use EXACTLY ONE single solid color for the ENTIRE illustration — every petal, leaf, stem, everything. "
            "NO white, NO second color, NO outlines in a different color, NO highlights, NO gradients. "
            "Background is plain white. Bold linocut stamp style: flat filled shapes with cut-out negative space. "
            "The flower must be clearly recognizable and fill most of the frame."
        )

        contents = []
        if reference_photo_path and Path(reference_photo_path).exists():
            ref_bytes = Path(reference_photo_path).read_bytes()
            contents.append(types.Part.from_bytes(data=ref_bytes, mime_type="image/jpeg"))
        contents.append(lock_prompt)

        response = gclient.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=contents,
            config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
        )

        generated_bytes = None
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                raw = part.inline_data.data
                if isinstance(raw, str):
                    raw = base64.b64decode(raw)
                generated_bytes = raw
                break

        if not generated_bytes:
            return {"error": "Gemini returned no image data"}

        img_lock = Image.open(io.BytesIO(generated_bytes)).convert("RGBA")
        arr      = np.array(img_lock, dtype=np.uint8)

        # Pass 1: hard white threshold
        white = (arr[:, :, 0] > 240) & (arr[:, :, 1] > 240) & (arr[:, :, 2] > 240)
        arr[white, 3] = 0

        # Pass 2: propagate transparency to near-white fringe
        near_white = (arr[:, :, 0] > 220) & (arr[:, :, 1] > 220) & (arr[:, :, 2] > 220)
        for _ in range(6):
            transparent = arr[:, :, 3] == 0
            adj   = np.roll(transparent, 1, 0) | np.roll(transparent, -1, 0) | np.roll(transparent, 1, 1) | np.roll(transparent, -1, 1)
            spill = near_white & adj
            if not spill.any():
                break
            arr[spill, 3] = 0
            near_white[spill] = False

        # Pass 3: keep only dominant color, discard outliers
        visible = arr[:, :, 3] > 0
        if visible.any():
            pixels   = arr[visible][:, :3].astype(np.float32)
            dominant = np.median(pixels, axis=0)
            dist     = np.sqrt(((arr[:, :, :3].astype(np.float32) - dominant) ** 2).sum(axis=2))
            arr[visible & (dist > 80), 3] = 0

        img_lock = Image.fromarray(arr)
        bbox = img_lock.getbbox()
        if bbox:
            img_lock = img_lock.crop(bbox)
        img_lock = _resize_fit_transparent(img_lock, 200)

        imageset = _make_imageset(xcassets, f"{slug}-lock")
        fname    = "lock.png"
        img_lock.save(str(imageset / fname), "PNG")
        _write_contents_json(imageset, fname)
        return {"path": str(imageset / fname)}

    except Exception as e:
        return {"error": str(e)}


def tool_enhance_and_translate(wiki_data: dict) -> dict:
    """Use Gemini to write poetic descriptions, a fun fact, and translate into 6 languages."""
    try:
        from google import genai
        from google.genai import types

        gclient     = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
        latin_name  = wiki_data.get("latinName", "")
        name        = wiki_data.get("name", "")

        prompt = f"""You are a botanical content writer for 'Flora: Flower of the Day' iOS app.
Return ONLY valid JSON — no markdown fences, no prose outside the JSON.

Wikipedia data about '{name}' ({latin_name}):
SUMMARY: {wiki_data.get('_raw_summary', '')[:2000]}
Habitat: {wiki_data.get('habitat', '')[:600]}
Etymology: {wiki_data.get('etymology', '')[:500]}
Cultural uses: {wiki_data.get('culturalInfo', '')[:500]}

TASKS:
1. Write a one-sentence DESCRIPTION — poetic/emotional (~20-30 words).
2. Write a one-sentence FUN FACT — curious or surprising (focus on etymology or unusual behaviour).
3. Write clean HABITAT — 1-2 clear sentences.
4. Write clean ETYMOLOGY — 1-2 sentences. If unknown, write "Origin of the name is uncertain."
5. Write clean CULTURAL INFO — 1-2 sentences about folk or cultural uses.
6. Confirm the best common English NAME.

Then TRANSLATE all six fields AND wikiDescription into: de, fr, es, it, zh, ja.
wikiDescription to translate: {wiki_data.get('wikiDescription', '')[:500]}

Return ONLY this JSON (no nulls):
{{
  "english": {{"name":"","description":"","funFact":"","habitat":"","etymology":"","culturalInfo":""}},
  "translations": {{
    "de": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}},
    "fr": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}},
    "es": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}},
    "it": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}},
    "zh": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}},
    "ja": {{"name":"","description":"","funFact":"","wikiDescription":"","habitat":"","etymology":"","culturalInfo":""}}
  }}
}}"""

        last_exc = None
        for attempt in range(4):
            try:
                response = gclient.models.generate_content(
                    model="gemini-2.5-flash-lite", contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                break
            except Exception as e:
                last_exc = e
                if attempt < 3:
                    wait = 15 * (attempt + 1)
                    print(f"  [Retry {attempt+1}] {e} — retrying in {wait}s…")
                    time.sleep(wait)
        else:
            raise last_exc

        raw    = re.sub(r"^```(?:json)?\s*", "", response.text.strip())
        raw    = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        return {"english": parsed.get("english", {}), "translations": parsed.get("translations", {})}

    except Exception as e:
        return {
            "error": str(e),
            "english": {
                "name": wiki_data.get("name", ""),
                "description": "A beautiful flower cherished by nature lovers around the world.",
                "funFact": "This flower has fascinated botanists for centuries.",
                "habitat": wiki_data.get("habitat", ""),
                "etymology": wiki_data.get("etymology", ""),
                "culturalInfo": wiki_data.get("culturalInfo", ""),
            },
            "translations": {},
        }


BATCH_START_DATE = date(2026, 5, 3)


def tool_update_dataset(
    latin_name: str,
    slug: str,
    wiki_data: dict,
    care_info: list,
    petal_color: str,
    info_author: str,
    english: dict,
    translations: dict,
    xcassets_root: str,
) -> dict:
    """Upsert the flower entry into flowers.dataset/flowers.json. Call this last."""
    xcassets   = Path(xcassets_root)
    final_name = english.get("name") or wiki_data.get("name") or latin_name.split()[-1].capitalize()

    flower = {
        "name": final_name, "latinName": latin_name,
        "description": english.get("description", ""),
        "funFact": english.get("funFact", ""),
        "petalColorHex": petal_color,
        "imageName": slug, "lockImageName": f"{slug}-lock", "infoImageName": f"{slug}-info",
        "infoImageAuthor": info_author, "careInfo": care_info,
        "year": 0, "month": 0, "day": 0,
        "wikiDescription": wiki_data.get("wikiDescription", ""),
        "habitat": english.get("habitat") or wiki_data.get("habitat", ""),
        "etymology": english.get("etymology") or wiki_data.get("etymology", ""),
        "culturalInfo": english.get("culturalInfo") or wiki_data.get("culturalInfo", ""),
        "wikipediaUrl": wiki_data.get("wikipediaUrl", ""),
        "translations": translations,
    }

    dataset_dir  = _ensure_dataset(xcassets)
    flowers_path = dataset_dir / "flowers.json"
    existing     = []
    if flowers_path.exists():
        try:
            existing = json.loads(flowers_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    prev = next((f for f in existing if f.get("latinName") == latin_name), None)
    if prev:
        flower_date = date(prev["year"], prev["month"], prev["day"])
    else:
        others      = [f for f in existing if f.get("latinName") != latin_name]
        flower_date = BATCH_START_DATE + timedelta(days=len(others))

    flower["year"]  = flower_date.year
    flower["month"] = flower_date.month
    flower["day"]   = flower_date.day

    updated = [f for f in existing if f.get("latinName") != latin_name]
    updated.append(flower)
    flowers_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"success": True, "total_flowers": len(updated), "date_assigned": str(flower_date), "name": final_name}


# ────────────────────────────────────────────────────────── tool schemas ──

TOOLS = gtypes.Tool(function_declarations=[
    gtypes.FunctionDeclaration(
        name="fetch_wikipedia",
        description="Fetch Wikipedia data for a flower: common name, description, habitat, etymology, cultural info, Wikipedia URL.",
        parameters={"type": "object", "properties": {"latin_name": {"type": "string", "description": "Scientific name, e.g. 'Bellis perennis'"}}, "required": ["latin_name"]},
    ),
    gtypes.FunctionDeclaration(
        name="fetch_care_info",
        description="Get care info (hardiness, soil moisture, light) from PFAF with Gemini fallback.",
        parameters={"type": "object", "properties": {"latin_name": {"type": "string"}}, "required": ["latin_name"]},
    ),
    gtypes.FunctionDeclaration(
        name="fetch_photos",
        description="Download up to 4 CC0/free candidate photos from Wikimedia Commons into temp_dir. Returns paths sorted by quality (Gemini-judged best first).",
        parameters={"type": "object", "properties": {
            "latin_name": {"type": "string"},
            "temp_dir":   {"type": "string", "description": "Directory to save downloaded photos"},
        }, "required": ["latin_name", "temp_dir"]},
    ),
    gtypes.FunctionDeclaration(
        name="process_home_image",
        description="Remove background from a photo using Gemini, save as home.png (492x492 transparent PNG). Returns path and petal_color hex. If it returns an error about visibility, retry with the next photo from fetch_photos.",
        parameters={"type": "object", "properties": {
            "photo_path":    {"type": "string", "description": "Absolute path to source photo"},
            "slug":          {"type": "string"},
            "xcassets_root": {"type": "string"},
        }, "required": ["photo_path", "slug", "xcassets_root"]},
    ),
    gtypes.FunctionDeclaration(
        name="process_info_image",
        description="Compress a photo to <1 MB JPEG, save as info.jpg for the info screen. Returns path.",
        parameters={"type": "object", "properties": {
            "photo_path":    {"type": "string"},
            "slug":          {"type": "string"},
            "xcassets_root": {"type": "string"},
        }, "required": ["photo_path", "slug", "xcassets_root"]},
    ),
    gtypes.FunctionDeclaration(
        name="generate_lock_image",
        description="Generate a monochromatic botanical icon for the lock screen via Gemini image generation. Returns path.",
        parameters={"type": "object", "properties": {
            "latin_name":           {"type": "string"},
            "common_name":          {"type": "string"},
            "reference_photo_path": {"type": "string", "description": "Path to a reference photo (pass empty string if none)"},
            "slug":                 {"type": "string"},
            "xcassets_root":        {"type": "string"},
        }, "required": ["latin_name", "common_name", "reference_photo_path", "slug", "xcassets_root"]},
    ),
    gtypes.FunctionDeclaration(
        name="enhance_and_translate",
        description="Use Gemini to write a poetic description, fun fact, clean text fields, and translations into 6 languages (de, fr, es, it, zh, ja). Pass the full wiki_data dict.",
        parameters={"type": "object", "properties": {
            "wiki_data": {"type": "object", "description": "The dict returned by fetch_wikipedia"},
        }, "required": ["wiki_data"]},
    ),
    gtypes.FunctionDeclaration(
        name="update_dataset",
        description="Upsert the completed flower entry into flowers.dataset/flowers.json. Call this last, only after images are processed.",
        parameters={"type": "object", "properties": {
            "latin_name":    {"type": "string"},
            "slug":          {"type": "string"},
            "wiki_data":     {"type": "object"},
            "care_info":     {"type": "array", "items": {"type": "object"}, "description": "List of care objects from fetch_care_info"},
            "petal_color":   {"type": "string", "description": "Hex color from process_home_image, e.g. '#FF5733'. Use '#F0EBD8' if image processing failed."},
            "info_author":   {"type": "string"},
            "english":       {"type": "object", "description": "Enhanced English fields from enhance_and_translate"},
            "translations":  {"type": "object", "description": "Translations dict from enhance_and_translate"},
            "xcassets_root": {"type": "string"},
        }, "required": ["latin_name", "slug", "wiki_data", "care_info", "petal_color", "info_author", "english", "translations", "xcassets_root"]},
    ),
])

TOOL_MAP = {
    "fetch_wikipedia":       lambda **kw: tool_fetch_wikipedia(**kw),
    "fetch_care_info":       lambda **kw: tool_fetch_care_info(**kw),
    "fetch_photos":          lambda **kw: tool_fetch_photos(**kw),
    "process_home_image":    lambda **kw: tool_process_home_image(**kw),
    "process_info_image":    lambda **kw: tool_process_info_image(**kw),
    "generate_lock_image":   lambda **kw: tool_generate_lock_image(**kw),
    "enhance_and_translate": lambda **kw: tool_enhance_and_translate(**kw),
    "update_dataset":        lambda **kw: tool_update_dataset(**kw),
}


# ──────────────────────────────────────────────────────────── agent loop ──

SYSTEM_PROMPT = """\
You are an autonomous agent that processes flowers for the 'Flora: Flower of the Day' iOS app.
Given a flower's scientific name you must produce all assets using the available tools.

Typical workflow:
  1. fetch_wikipedia        → get common name and text data
  2. fetch_care_info        → get watering / light / hardiness info
  3. fetch_photos           → download candidate photos to temp_dir
  4. process_home_image     → remove background, extract petal color
                              (if it returns an error about visibility, retry with the next photo in the list)
  5. process_info_image     → compress info photo
  6. generate_lock_image    → create monochromatic icon
  7. enhance_and_translate  → write poetic copy and translations
  8. update_dataset         → save everything to flowers.json

Rules:
- If process_home_image fails on one photo, try the next one from fetch_photos results.
- If a step fails and cannot be retried, use sensible defaults and keep going.
- Call update_dataset only after images are successfully processed.
- Be adaptive: if something unexpected happens, decide the best course of action.
- When fully done, print a brief summary of what was produced.\
"""


def run_agent(latin_name: str) -> None:
    xcassets_root = Path(__file__).parent / "results.xcassets"
    xcassets_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(__file__).parent / "_temp" / _make_slug(latin_name)
    temp_dir.mkdir(parents=True, exist_ok=True)

    slug = _make_slug(latin_name)

    print("┌─────────────────────────────────────────┐")
    print(f"  Flora Agent — {latin_name}")
    print(f"  Slug: {slug}")
    print(f"  Output: {xcassets_root}/")
    print("└─────────────────────────────────────────┘\n")

    gclient = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))

    initial_message = (
        f"Process the flower '{latin_name}' for the Flora iOS app.\n"
        f"slug:          {slug}\n"
        f"xcassets_root: {xcassets_root}\n"
        f"temp_dir:      {temp_dir}\n"
    )

    contents: list = [
        gtypes.Content(role="user", parts=[gtypes.Part(text=initial_message)])
    ]

    # Accumulates full tool results — used for the fallback update_dataset call
    # if Gemini's context overflows before the agent reaches that step.
    state: dict = {}

    turn = 0
    while True:
        turn += 1
        print(f"\n── Agent turn {turn} ──────────────────────────────────")

        response = gclient.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=gtypes.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=[TOOLS],
            ),
        )

        candidate = response.candidates[0]

        if not candidate.content or not candidate.content.parts:
            print(f"[Warning] Empty response (finish_reason: {candidate.finish_reason}). Stopping.")
            break

        contents.append(candidate.content)

        for part in candidate.content.parts:
            if part.text:
                print(f"[Agent] {part.text}")

        fn_calls = [p for p in candidate.content.parts if p.function_call]
        if not fn_calls:
            print("\n✓ Agent finished.")
            break

        fn_responses = []
        for part in fn_calls:
            fc = part.function_call
            args = dict(fc.args)
            print(f"  → {fc.name}({list(args.keys())})")

            tool_fn = TOOL_MAP.get(fc.name)
            if not tool_fn:
                result = {"error": f"Unknown tool: {fc.name}"}
            else:
                try:
                    result = tool_fn(**args)
                except Exception as e:
                    result = {"error": f"Tool execution error: {e}"}

            # Persist full result for the fallback (before any trimming)
            if isinstance(result, dict) and "error" not in result:
                state[fc.name] = result

            if isinstance(result, dict) and "error" in result:
                print(f"    ✗ {result['error']}")
            else:
                summary = {k: v for k, v in result.items() if k not in ("translations", "_raw_summary", "photos")} if isinstance(result, dict) else result
                print(f"    ✓ {summary}")

            # Trim large payloads before feeding back into Gemini's context.
            # The full data is already in `state`; the agent only needs a short confirmation.
            trimmed = result
            if isinstance(result, dict) and "translations" in result:
                langs = list(result["translations"].keys())
                trimmed = {**{k: v for k, v in result.items() if k != "translations"},
                           "translations_note": f"generated for {langs}"}
            if isinstance(result, dict) and "_raw_summary" in result:
                trimmed = {k: v for k, v in trimmed.items() if k != "_raw_summary"}

            fn_responses.append(
                gtypes.Part.from_function_response(
                    name=fc.name,
                    response={"result": json.dumps(trimmed, default=str)},
                )
            )

        contents.append(gtypes.Content(role="user", parts=fn_responses))

    # ── Fallback: if the agent stopped before calling update_dataset, do it now ──
    if "update_dataset" not in state:
        wiki    = state.get("fetch_wikipedia", {})
        care    = state.get("fetch_care_info", {}).get("care_info", [])
        photos  = state.get("fetch_photos", {})
        home    = state.get("process_home_image", {})
        enhance = state.get("enhance_and_translate", {})

        if wiki and home.get("path"):
            print("\n[Fallback] Agent did not call update_dataset — running it directly.")
            fb_result = tool_update_dataset(
                latin_name=latin_name,
                slug=slug,
                wiki_data=wiki,
                care_info=care,
                petal_color=home.get("petal_color", "#F0EBD8"),
                info_author=photos.get("info_author", ""),
                english=enhance.get("english", {}),
                translations=enhance.get("translations", {}),
                xcassets_root=str(xcassets_root),
            )
            print(f"    ✓ {fb_result}")

    print(f"\nOutput files in {xcassets_root}/")
    for f in sorted(xcassets_root.rglob("*")):
        if f.is_file():
            size_kb = f.stat().st_size // 1024
            rel     = f.relative_to(xcassets_root)
            tag     = f"  ({size_kb} KB)" if size_kb > 0 else ""
            print(f"  {rel}{tag}")


def main() -> None:
    if len(sys.argv) != 2:
        print('Usage: python agent.py "Latin Name"')
        print('Example: python agent.py "Bellis perennis"')
        sys.exit(1)
    run_agent(sys.argv[1].strip())


if __name__ == "__main__":
    main()
