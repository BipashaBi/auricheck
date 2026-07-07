#!/usr/bin/env python3
"""
AuriCheck — Non-destructive spurious gold detection for gold-loan appraisal.

Working prototype: fuses density (Archimedes), ultrasonic velocity,
eddy-current conductivity, magnet response and photo analysis into a
single PASS / REVIEW / FAIL verdict.

Usage:
    python auricheck.py demo                      # run 4 built-in test cases
    python auricheck.py analyze --karat 22 --air 45.20 --water 42.66 \
        --velocity 3300 --conductivity 12.5 --magnet none
    python auricheck.py inspect photo.jpg         # seam/edge + wear analysis

Dependencies: numpy, Pillow  (pip install numpy pillow)
"""

import argparse
import json
import os
import math
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

# ----------------------------------------------------------------------------
# Reference physics (literature values; tolerance bands to be re-calibrated
# on the branch's own genuine-pledge history during pilot)
# ----------------------------------------------------------------------------

REFERENCE = {
    # karat: (density g/cc band, ultrasonic velocity m/s band, conductivity MS/m band)
    24: {"density": (18.9, 19.4), "velocity": (3150, 3350), "conductivity": (40.0, 46.0)},
    22: {"density": (17.4, 18.1), "velocity": (3150, 3450), "conductivity": (9.0, 16.0)},
    18: {"density": (14.7, 16.2), "velocity": (3200, 3600), "conductivity": (7.0, 13.0)},
}

# Known impostor signatures, used to name the *likely* adulterant
IMPOSTORS = {
    "tungsten core":  {"density": 19.25, "velocity": 5180, "conductivity": 18.2},
    "lead core":      {"density": 11.34, "velocity": 2160, "conductivity": 4.8},
    "copper core":    {"density": 8.96,  "velocity": 4700, "conductivity": 58.0},
    "brass (plated)": {"density": 8.50,  "velocity": 4430, "conductivity": 15.9},
    "under-karated alloy (≈18K)": {"density": 15.5, "velocity": 3400, "conductivity": 10.0},
}

WEIGHTS = {"density": 0.30, "velocity": 0.25, "conductivity": 0.25,
           "magnet": 0.10, "vision": 0.10}


@dataclass
class SignalResult:
    name: str
    value: str
    expected: str
    deviation: float   # 0 = perfect, 100 = wildly off
    note: str


def band_deviation(value: float, band: tuple, hard_limit_frac: float = 0.25) -> float:
    """0 inside the band; grows to 100 as the value moves hard_limit_frac
    (fraction of band centre) outside it."""
    lo, hi = band
    if lo <= value <= hi:
        return 0.0
    centre = (lo + hi) / 2
    dist = (lo - value) if value < lo else (value - hi)
    return min(100.0, 100.0 * dist / (hard_limit_frac * centre))


def analyze(karat: int, weight_air: float, weight_water: float | None,
            velocity: float | None, conductivity: float | None,
            magnet: str | None, vision_score: float | None,
            item_note: str = "") -> dict:
    ref = REFERENCE[karat]
    signals: list[SignalResult] = []
    measured = {}

    # --- Layer 1: density -----------------------------------------------
    if weight_water is not None:
        displaced = weight_air - weight_water
        if displaced <= 0:
            raise ValueError("weight in water must be less than weight in air")
        density = weight_air / displaced  # water = 1.000 g/cc
        measured["density"] = density
        signals.append(SignalResult(
            "Density (Archimedes)", f"{density:.2f} g/cc",
            f"{ref['density'][0]}–{ref['density'][1]} g/cc",
            band_deviation(density, ref["density"], hard_limit_frac=0.12),
            "weight_air / (weight_air − weight_water)"))

    # --- Layer 2a: ultrasonic velocity ------------------------------------
    if velocity is not None:
        measured["velocity"] = velocity
        signals.append(SignalResult(
            "Ultrasonic velocity", f"{velocity:.0f} m/s",
            f"{ref['velocity'][0]}–{ref['velocity'][1]} m/s",
            band_deviation(velocity, ref["velocity"]),
            "gold ≈ 3240 m/s, tungsten ≈ 5180 m/s"))

    # --- Layer 2b: eddy-current conductivity ------------------------------
    if conductivity is not None:
        measured["conductivity"] = conductivity
        signals.append(SignalResult(
            "Eddy-current conductivity", f"{conductivity:.1f} MS/m",
            f"{ref['conductivity'][0]}–{ref['conductivity'][1]} MS/m",
            band_deviation(conductivity, ref["conductivity"]),
            "reads through plating"))

    # --- Layer 2c: magnet slide -------------------------------------------
    if magnet is not None:
        dev = {"none": 0.0, "weak": 60.0, "strong": 100.0}[magnet]
        signals.append(SignalResult(
            "Magnet response", magnet, "none (gold is diamagnetic)", dev,
            "any pull ⇒ ferrous content"))

    # --- Layer 3: vision anomaly score ------------------------------------
    if vision_score is not None:
        signals.append(SignalResult(
            "Photo anomaly score", f"{vision_score:.0f}/100", "< 35",
            max(0.0, min(100.0, (vision_score - 35) * (100 / 65))) if vision_score > 35 else 0.0,
            "seams, wear mismatch, hallmark"))

    # --- Fusion -------------------------------------------------------------
    key_of = {"Density (Archimedes)": "density", "Ultrasonic velocity": "velocity",
              "Eddy-current conductivity": "conductivity", "Magnet response": "magnet",
              "Photo anomaly score": "vision"}
    total_w = sum(WEIGHTS[key_of[s.name]] for s in signals)
    risk = sum(WEIGHTS[key_of[s.name]] * s.deviation for s in signals) / total_w

    phys = [s.deviation for s in signals
            if key_of[s.name] in ("density", "velocity", "conductivity")]
    max_phys = max(phys) if phys else 0.0
    max_any = max((s.deviation for s in signals), default=0.0)
    if risk >= 50 or max_phys >= 65:
        verdict = "FAIL"          # a hard physical impossibility is decisive
    elif risk >= 20 or max_any >= 30 or len(phys) < 2:
        verdict = "REVIEW"        # any flagged signal, or too few signals, blocks PASS
    else:
        verdict = "PASS"

    # --- Likely adulterant (nearest impostor signature) ----------------------
    likely = None
    if verdict != "PASS" and len(measured) >= 2 and (risk >= 20 or max_phys >= 30):
        def dist(sig):
            terms = [((measured[k] - sig[k]) / sig[k]) ** 2
                     for k in measured if k in sig]
            return math.sqrt(sum(terms) / len(terms)) if terms else 9e9
        name, d = min(((n, dist(s)) for n, s in IMPOSTORS.items()), key=lambda t: t[1])
        if d < 0.20:
            likely = name

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "item_note": item_note,
        "declared_karat": karat,
        "signals": [asdict(s) for s in signals],
        "risk_score": round(risk, 1),
        "verdict": verdict,
        "likely_adulterant": likely,
    }


# ----------------------------------------------------------------------------
# Layer 3 — photo inspection (numpy + Pillow only)
# ----------------------------------------------------------------------------

def inspect_image(path: str, out_path: str | None = None) -> dict:
    import numpy as np
    from PIL import Image

    img = Image.open(path).convert("RGB")
    img.thumbnail((900, 900))
    arr = np.asarray(img, dtype=np.float32)
    gray = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)

    # Sobel edges
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
    ky = kx.T
    def conv(a, k):
        from numpy.lib.stride_tricks import sliding_window_view
        w = sliding_window_view(np.pad(a, 1, mode="edge"), (3, 3))
        return np.einsum("ijkl,kl->ij", w, k)
    mag = np.hypot(conv(gray, kx), conv(gray, ky))
    strong = mag > np.percentile(mag, 97)

    # Seam suspicion: long, thin, straight runs of strong edges
    edge_density = float(strong.mean())
    row_runs = strong.sum(axis=1) / strong.shape[1]
    col_runs = strong.sum(axis=0) / strong.shape[0]
    linearity = float(max(row_runs.max(), col_runs.max()))  # 1.0 = full-width seam line

    # Wear / colour uniformity: hue variance across 4x4 blocks
    h, w = gray.shape
    blocks = []
    for i in range(4):
        for j in range(4):
            b = arr[i*h//4:(i+1)*h//4, j*w//4:(j+1)*w//4]
            if b.size:
                blocks.append(b.reshape(-1, 3).mean(axis=0))
    blocks = np.array(blocks)
    colour_spread = float(np.linalg.norm(blocks.std(axis=0)))  # 0 = uniform

    anomaly = min(100.0, 55 * linearity / 0.5
                        + 25 * min(1.0, edge_density / 0.05)
                        + 20 * min(1.0, colour_spread / 40))

    if out_path:
        overlay = arr.copy()
        overlay[strong] = [220, 30, 30]
        Image.fromarray(overlay.astype(np.uint8)).save(out_path)

    return {"file": path, "edge_density": round(edge_density, 4),
            "seam_linearity": round(linearity, 3),
            "colour_spread": round(colour_spread, 1),
            "anomaly_score": round(anomaly, 1),
            "annotated": out_path}


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------

os.system("")  # enables ANSI colours on Windows consoles

BADGE = {"PASS": "\033[42;30m PASS \033[0m", "REVIEW": "\033[43;30m REVIEW \033[0m",
         "FAIL": "\033[41;97m FAIL \033[0m"}

def print_report(r: dict):
    print("=" * 66)
    print(f"AuriCheck report  ·  {r['timestamp']}  ·  declared {r['declared_karat']}K")
    if r["item_note"]:
        print(f"Item: {r['item_note']}")
    print("-" * 66)
    for s in r["signals"]:
        flag = "OK  " if s["deviation"] < 15 else ("WARN" if s["deviation"] < 50 else "FAIL")
        print(f"  [{flag}] {s['name']:<28} {s['value']:<14} expect {s['expected']}")
    print("-" * 66)
    print(f"  Risk score : {r['risk_score']}/100")
    print(f"  Verdict    : {BADGE.get(r['verdict'], r['verdict'])}")
    if r["likely_adulterant"]:
        print(f"  Signature match: measurements resemble a {r['likely_adulterant'].upper()}")
    print("=" * 66 + "\n")


# ----------------------------------------------------------------------------
# Demo cases
# ----------------------------------------------------------------------------

def run_demo():
    cases = [
        dict(item_note="Genuine 22K bangle, 45.20 g",
             karat=22, weight_air=45.20, weight_water=42.66,      # ρ ≈ 17.80
             velocity=3290, conductivity=12.4, magnet="none", vision_score=18),
        dict(item_note="Tungsten-core 'bangle' (density passes!)",
             karat=22, weight_air=45.10, weight_water=42.74,      # ρ ≈ 19.11 — close!
             velocity=4880, conductivity=17.6, magnet="none", vision_score=52),
        dict(item_note="Gold-plated brass chain",
             karat=22, weight_air=22.40, weight_water=19.85,      # ρ ≈ 8.78
             velocity=None, conductivity=15.2, magnet="weak", vision_score=61),
        dict(item_note="Under-karated (18K sold as 22K) ring",
             karat=22, weight_air=8.10, weight_water=7.58,        # ρ ≈ 15.58
             velocity=3400, conductivity=8.4, magnet="none", vision_score=22),
    ]
    for c in cases:
        print_report(analyze(**c))


def main():
    p = argparse.ArgumentParser(description="AuriCheck spurious-gold detector")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo", help="run built-in test cases")

    a = sub.add_parser("analyze", help="score one item from measurements")
    a.add_argument("--karat", type=int, choices=sorted(REFERENCE), required=True)
    a.add_argument("--air", type=float, required=True, help="weight in air (g)")
    a.add_argument("--water", type=float, help="weight suspended in water (g)")
    a.add_argument("--velocity", type=float, help="ultrasonic velocity (m/s)")
    a.add_argument("--conductivity", type=float, help="eddy-current reading (MS/m)")
    a.add_argument("--magnet", choices=["none", "weak", "strong"])
    a.add_argument("--vision", type=float, help="photo anomaly score 0-100")
    a.add_argument("--note", default="", help="item description")
    a.add_argument("--json", action="store_true", help="print JSON instead")

    i = sub.add_parser("inspect", help="analyze a jewellery photo")
    i.add_argument("image")
    i.add_argument("--out", default="annotated.png")

    args = p.parse_args()
    if args.cmd == "demo":
        run_demo()
    elif args.cmd == "analyze":
        r = analyze(args.karat, args.air, args.water, args.velocity,
                    args.conductivity, args.magnet, args.vision, args.note)
        print(json.dumps(r, indent=2) if args.json else "", end="")
        if not args.json:
            print_report(r)
    elif args.cmd == "inspect":
        res = inspect_image(args.image, args.out)
        print(json.dumps(res, indent=2))
        print(f"\nPhoto anomaly score: {res['anomaly_score']}/100 "
              f"(feed into analyze via --vision)")


if __name__ == "__main__":
    main()
