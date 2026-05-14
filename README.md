# Flora — Automated Content Generation

> Asset generation tools for [Flora: Flower of the Day](https://apps.apple.com/it/app/flora-fiore-del-giorno/id6759986494) — the iOS app that celebrates a new flower every day.

<img src="flora_pipeline/mdimg/image1.jpg" width="300"/>

This repository contains two tools that do the same job — generating production-ready Xcode assets from a Latin name — but with fundamentally different architectures.

---

## flora_agent — Agentic version

Gemini acts as an autonomous agent: it decides which tools to call, in what order, and how to recover from failures. The Python code provides tools; Gemini orchestrates them.

```bash
cd flora_agent
python agent.py "Rosa canina"
```

### How it works

At each turn, Gemini sees the full conversation history (all previous tool calls and their results) and autonomously decides the next step. There is no hardcoded step sequence.

| Tool | What it does |
|------|-------------|
| `fetch_wikipedia` | Fetches common name, habitat, etymology, cultural info |
| `fetch_care_info` | Scrapes PFAF for hardiness, soil and light; Gemini fallback |
| `fetch_photos` | Downloads up to 4 CC-licensed photos from Wikimedia; Gemini picks the best |
| `process_home_image` | Removes background via Gemini → transparent PNG 492×492 |
| `process_info_image` | Compresses photo to <1 MB JPEG |
| `generate_lock_image` | Generates monochromatic botanical icon via Gemini |
| `enhance_and_translate` | Writes poetic copy and translates into DE, FR, ES, IT, ZH, JA |
| `update_dataset` | Upserts the flower into `flowers.dataset/flowers.json` |

If a tool fails (e.g. background removal on the first photo), Gemini sees the error and decides to retry with the next candidate — no explicit retry logic in the code.

### Setup

```bash
pip install -r ../flora_pipeline/requirements.txt
```

Add a `.env` at the repo root:

```
GEMINI_API_KEY=your_key_here
```

---

## flora_pipeline — Pipeline version

A sequential Python script. Each step calls the next in a fixed order. Simpler, faster, and easier to debug.

```bash
cd flora_pipeline
python pipeline.py "Rosa canina"
```

Runs 7 steps end-to-end:

| Step | What it does |
|------|-------------|
| **1 — Wikipedia** | Fetches common name, description, habitat, etymology, cultural info |
| **2 — Care info** | Scrapes PFAF for hardiness, soil and light requirements |
| **3 — Photos** | Fetches up to 4 CC-licensed candidates from Wikimedia; Gemini picks the best |
| **4 — Image processing** | Background removal on `home.png` via Gemini; compression for `info.jpg` |
| **5 — Lock screen** | Generates a monochromatic botanical icon via Gemini (linocut style) |
| **6 — Content & translations** | Enhances English copy and translates into DE, FR, ES, IT, ZH, JA |
| **7 — Dataset** | Upserts the flower into `flowers.dataset/flowers.json` |

To process a batch:

```bash
python run_batch.py
```

### Setup

```bash
pip install -r requirements.txt
```

Add a `.env` at the repo root (or inside `flora_pipeline/`):

```
GEMINI_API_KEY=your_key_here
```

### Utilities

```bash
python clean.py   # wipe results.xcassets/ for a fresh run
```

---

## Output structure

Both tools produce the same output layout:

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

Get your API key from [Google AI Studio](https://aistudio.google.com/apikey).

---

## Pipeline vs Agent — which one to use

The pipeline makes roughly 5 Gemini calls per flower; the agent makes ~13, since each orchestration turn resends the full conversation history. For a repetitive, well-defined workflow the pipeline is cheaper and faster.

However, the agent evaluates every step on its own: it reads the result, decides whether it's good enough, and adapts — retrying a failed photo, skipping a broken step, or changing strategy without any hardcoded logic. `flora_agent` was built to explore that autonomy.
