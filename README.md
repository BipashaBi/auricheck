# AuriCheck — Spurious Gold Detection (Working Prototype)

Non-destructive verification of pledged gold for gold-loan appraisal.
Fuses **density (Archimedes) + ultrasonic velocity + eddy-current
conductivity + magnet response + photo analysis** into one
PASS / REVIEW / FAIL verdict, and names the likely adulterant by matching
against known impostor signatures (tungsten, lead, brass, under-karated alloy).

## Setup

```bash
pip install numpy pillow
```

## Run the demo (4 built-in cases)

```bash
python auricheck.py demo
```

Cases: genuine 22K bangle → **PASS**, tungsten-core bangle (density nearly
passes!) → **FAIL** (caught by ultrasound + conductivity), gold-plated brass
chain → **FAIL**, 18K sold as 22K → **FAIL**.

## Score a real item

```bash
python auricheck.py analyze --karat 22 --air 45.20 --water 42.66 \
    --velocity 3290 --conductivity 12.4 --magnet none --vision 18 \
    --note "Customer bangle, pledge #1042"
```

Density is computed as `air / (air − water)` — only a 0.01 g scale, a beaker
and a sling are needed. Any signal can be omitted; the fusion re-weights,
and with fewer than two physical signals the best possible verdict is REVIEW
(never a silent PASS).

## Inspect a pledge photo (Layer 3)

```bash
python auricheck.py inspect photo.jpg --out annotated.png
```

Sobel edge analysis flags long straight seam/fill lines (`seam_linearity`),
plus colour-uniformity across the item (plating wear shows as mismatched
patches). Prints an anomaly score 0–100 to feed into `analyze --vision`, and
saves an annotated image with detected edges highlighted in red.

Generate the included synthetic samples: `sample_genuine.jpg` scores ~24,
`sample_suspect.jpg` (horizontal fill seam + plug) scores 100.

## Verdict logic

- Each signal → deviation 0–100 vs. the declared-karat reference band
- Weighted fusion: density 0.30, ultrasound 0.25, conductivity 0.25, magnet 0.10, vision 0.10
- **FAIL** if risk ≥ 50 **or any physical signal ≥ 65** (a hard physical
  impossibility is decisive regardless of the average)
- **REVIEW** if risk ≥ 20, any physical signal ≥ 30, or < 2 physical signals
- **PASS** otherwise

Reference bands are literature values; they are meant to be re-calibrated per
item category (hollow bangles, stone-set pieces, chains) from the branch's own
genuine-pledge history during pilot.
