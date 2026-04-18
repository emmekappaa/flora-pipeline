# Flora Pipeline

> Automated content generation pipeline for [Flora: Flower of the Day](https://apps.apple.com/it/app/flora-fiore-del-giorno/id6759986494) — the iOS app that celebrates a new flower every day.

<img src="mdimg/image1.jpg" width="300"/>

Adding a new flower to Flora used to mean manually sourcing photos, writing descriptions, translating content into 6 languages, and wiring up every asset by hand. This pipeline automates the entire process: give it a Latin name, get back production-ready Xcode assets in under a minute.

---

## How it works

```bash
python pipeline.py "Rosa canina"
```

The pipeline runs 7 steps end-to-end:

| Step | What it does |
|------|-------------|
| **1 — Wikipedia** | Fetches the common name, description, habitat, etymology, and cultural info |
| **2 — Care info** | Scrapes PFAF for hardiness, soil, and light requirements |
| **3 — Photos** | Fetches up to 4 CC-licensed candidates from Wikimedia Commons; Gemini picks the best one for the home widget |
| **4 — Image processing** | Background removal on `home.png` (rembg), compression for `info.jpg` |
| **5 — Lock screen** | Generates a monochromatic botanical icon via Gemini (linocut style) |
| **6 — Content & translations** | Enhances English copy and translates everything into DE, FR, ES, IT, ZH, JA via Gemini |
| **7 — Dataset** | Upserts the flower into `flowers.dataset/flowers.json`, ready for Xcode |

## Output

All assets land in `results.xcassets/`, mirroring the real Xcode asset catalogue structure:

```
results.xcassets/
  <slug>.imageset/
      home.png              ← transparent-bg photo, 492×492
  <slug>-info.imageset/
      info.jpg              ← compressed photo, <1 MB
  <slug>-lock.imageset/
      lock.png              ← Gemini-generated icon, 200×200
  flowers.dataset/
      flowers.json          ← shared array, upserted by latinName
```

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in the same folder:

```
GEMINI_API_KEY=your_key_here
```

Get your key from [Google AI Studio](https://aistudio.google.com/apikey) — free tier is enough.

## Utilities

```bash
python clean.py   # wipe results.xcassets/ for a fresh run
```
