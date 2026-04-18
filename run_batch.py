#!/usr/bin/env python3
"""
Batch runner for Flora Pipeline.
Runs pipeline.py for each flower in the list, one at a time.
Skips flowers that already have a folder in results.xcassets/.
"""

import subprocess
import sys
from pathlib import Path

PIPELINE = Path(__file__).parent / "pipeline.py"
RESULTS  = Path(__file__).parent / "results.xcassets"

FLOWERS = [
    # (date, latin_name)
    ("2026-05-03", "Iris germanica"),
    ("2026-05-04", "Paeonia lactiflora"),
    ("2026-05-05", "Papaver orientale"),           # was Digitalis purpurea (duplicate)
    ("2026-05-06", "Aquilegia vulgaris"),
    ("2026-05-07", "Borago officinalis"),           # was Lupinus polyphyllus (duplicate)
    ("2026-05-08", "Convolvulus tricolor"),         # was Centaurea cyanus (duplicate)
    ("2026-05-09", "Nigella damascena"),
    ("2026-05-10", "Gaillardia x grandiflora"),     # was Echinacea purpurea (duplicate)
    ("2026-05-11", "Rudbeckia hirta"),
    ("2026-05-12", "Callistephus chinensis"),        # was Cosmos bipinnatus (duplicate)
    ("2026-05-13", "Zinnia elegans"),
    ("2026-05-14", "Tagetes erecta"),
    ("2026-05-15", "Lathyrus odoratus"),
    ("2026-05-16", "Salvia splendens"),             # was Antirrhinum majus (duplicate)
    ("2026-05-17", "Scabiosa atropurpurea"),
    ("2026-05-18", "Verbena bonariensis"),
    ("2026-05-19", "Gazania rigens"),
    ("2026-05-20", "Portulaca grandiflora"),
    ("2026-05-21", "Matthiola incana"),
    ("2026-05-22", "Alstroemeria aurea"),
    ("2026-05-23", "Heliconia rostrata"),           # was Strelitzia reginae (duplicate)
    ("2026-05-24", "Plumeria rubra"),
    ("2026-05-25", "Hibiscus syriacus"),            # was Hibiscus rosa-sinensis (duplicate)
    ("2026-05-26", "Nerium oleander"),
    ("2026-05-27", "Gardenia jasminoides"),
    ("2026-05-28", "Trachelospermum jasminoides"),  # was Jasminum officinale (duplicate)
    ("2026-05-29", "Clematis montana"),             # was Wisteria sinensis (duplicate)
    ("2026-05-30", "Lonicera periclymenum"),
    ("2026-05-31", "Tropaeolum majus"),
    ("2026-06-01", "Impatiens walleriana"),
    ("2026-06-02", "Fuchsia magellanica"),
    ("2026-06-03", "Pelargonium x hortorum"),
    ("2026-06-04", "Begonia x semperflorens"),
    ("2026-06-05", "Xerochrysum bracteatum"),
    ("2026-06-06", "Limonium sinuatum"),
    ("2026-06-07", "Echinops ritro"),
    ("2026-06-08", "Monarda didyma"),
    ("2026-06-09", "Agastache foeniculum"),
    ("2026-06-10", "Astilbe chinensis"),
    ("2026-06-11", "Kniphofia uvaria"),
    ("2026-06-12", "Crocosmia x crocosmiiflora"),
    ("2026-06-13", "Allium giganteum"),
    ("2026-06-14", "Eryngium planum"),
    ("2026-06-15", "Achillea millefolium"),
    ("2026-06-16", "Lychnis coronaria"),
    ("2026-06-17", "Geranium pratense"),
    ("2026-06-18", "Platycodon grandiflorus"),      # was Campanula persicifolia (duplicate)
    ("2026-06-19", "Phlox paniculata"),
    ("2026-06-20", "Helenium autumnale"),
    ("2026-06-21", "Nymphaea alba"),
    ("2026-06-22", "Hypericum calycinum"),
    ("2026-06-23", "Heuchera sanguinea"),
    ("2026-06-24", "Thalictrum aquilegiifolium"),
    ("2026-06-25", "Anchusa azurea"),
    ("2026-06-26", "Catharanthus roseus"),
    ("2026-06-27", "Bougainvillea glabra"),
    ("2026-06-28", "Protea cynaroides"),
    ("2026-06-29", "Hemerocallis fulva"),
    ("2026-06-30", "Salvia nemorosa"),
    ("2026-07-01", "Penstemon digitalis"),
]


def slug(latin: str) -> str:
    return latin.lower().replace(" ", "-").replace("×", "x")


def already_done(latin: str) -> bool:
    s = slug(latin)
    return (RESULTS / f"{s}.imageset" / "home.png").exists()


def main() -> None:
    total   = len(FLOWERS)
    skipped = 0
    ok      = 0
    failed  = []

    for i, (dt, latin) in enumerate(FLOWERS, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{total}]  {dt}  {latin}")
        print(f"{'='*60}")

        if already_done(latin):
            print(f"  → already done, skipping")
            skipped += 1
            continue

        result = subprocess.run(
            [sys.executable, str(PIPELINE), latin],
            cwd=str(PIPELINE.parent),
        )

        if result.returncode == 0:
            ok += 1
        else:
            print(f"  ✗ FAILED (exit code {result.returncode})")
            failed.append((dt, latin))

    print(f"\n{'='*60}")
    print(f"Done:    {ok}/{total - skipped} processed")
    print(f"Skipped: {skipped} (already had home.png)")
    if failed:
        print(f"Failed ({len(failed)}):")
        for dt, latin in failed:
            print(f"  {dt}  {latin}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
