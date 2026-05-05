"""Two integrated charts for slide 7's feature-families table.

- `slide7_repeat_players.png` — visualizes "Repeat players dominate the
  PTAB" via a Lorenz curve (left) + top-10 petitioner bars (right).
- `slide7_sector_variation.png` — visualizes "Cancellation rates vary
  sharply across sectors" via per-tech-center bars vs. the global base rate.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
JOINED = ROOT / "data" / "processed" / "joined_trials.parquet"
OUT = Path(__file__).parent

NAVY = "#0B2B4A"
ACCENT = "#C94A2D"
MUTED = "#6E6E6E"
LIGHT = "#D8D8D8"

TC_NAMES = {
    "1600": "Biotech / Organic Chem",
    "1700": "Chemical / Materials",
    "2100": "Computer Architecture",
    "2400": "Networks / Multiplex",
    "2600": "Communications",
    "2700": "Comm. (E-Commerce)",
    "2800": "Semiconductors / Optics",
    "2900": "Designs",
    "3600": "Transportation / Construction",
    "3700": "Mech. Eng. / Sports",
    "3900": "Central Reexam Unit",
}


def chart_repeat_players() -> None:
    df = pd.read_parquet(JOINED, columns=["petitioner_real_party"])
    counts = df["petitioner_real_party"].value_counts().sort_values(ascending=False)
    n_total = counts.sum()
    cum = counts.cumsum().to_numpy() / n_total
    rank_pct = np.arange(1, len(counts) + 1) / len(counts)

    # Gini via Lorenz
    lorenz_cum = np.concatenate([[0.0], counts.sort_values().cumsum().to_numpy() / n_total])
    pop_cum = np.linspace(0, 1, len(lorenz_cum))
    gini = 1 - 2 * np.trapezoid(lorenz_cum, pop_cum)

    # Top 10 petitioners
    top = counts.head(10).iloc[::-1]
    short = {
        "Samsung Electronics Co., Ltd. et al.": "Samsung (et al.)",
        "Samsung Electronics Co., Ltd.": "Samsung",
        "Comcast Cable Communications, LLC et al.": "Comcast (et al.)",
        "Amazon.com, Inc. et al.": "Amazon (et al.)",
    }
    top.index = [short.get(n, n.replace(" Inc.", "").replace(", LLC", "")) for n in top.index]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [1.05, 1]})

    # --- Left: Lorenz / concentration curve ---
    ax1.fill_between(rank_pct * 100, cum * 100, color=NAVY, alpha=0.15)
    ax1.plot(rank_pct * 100, cum * 100, color=NAVY, linewidth=2.2)
    ax1.plot([0, 100], [0, 100], color=MUTED, linestyle="--", linewidth=1, label="perfect equality")

    # Annotate: top-N% of filers cover Y% of trials
    for pct in [1, 5, 10]:
        k = max(int(pct / 100 * len(counts)), 1)
        share = counts.head(k).sum() / n_total
        ax1.scatter([pct], [share * 100], color=ACCENT, zorder=5, s=40)
        ax1.annotate(
            f"top {pct}% of filers\n→ {share*100:.0f}% of all IPRs",
            xy=(pct, share * 100),
            xytext=(pct + 5, share * 100 - 12),
            fontsize=8.5, color=ACCENT,
            arrowprops=dict(arrowstyle="-", color=ACCENT, lw=0.8),
        )

    ax1.set_xlabel("Petitioners ranked by # IPRs filed (cumulative %)", fontsize=10)
    ax1.set_ylabel("Cumulative share of all IPR trials (%)", fontsize=10)
    ax1.set_title(f"PTAB filings are extremely concentrated\n(Gini = {gini:.2f}, n = {len(counts):,} unique petitioners)",
                  fontsize=11, fontweight="bold", color=NAVY)
    ax1.set_xlim(0, 100); ax1.set_ylim(0, 100)
    ax1.grid(alpha=0.25)
    ax1.legend(loc="lower right", fontsize=8.5, frameon=False)

    # --- Right: top-10 petitioners ---
    bars = ax2.barh(range(len(top)), top.values, color=NAVY, alpha=0.9)
    bars[-1].set_color(ACCENT)  # highlight Apple
    ax2.set_yticks(range(len(top)))
    ax2.set_yticklabels(top.index, fontsize=9)
    for i, v in enumerate(top.values):
        ax2.text(v + 8, i, f"{v:,}", va="center", fontsize=8.5, color=NAVY)
    ax2.set_xlabel("# IPR petitions filed (2012–2024)", fontsize=10)
    ax2.set_title("Top 10 petitioners — and they keep coming back", fontsize=11, fontweight="bold", color=NAVY)
    ax2.set_xlim(0, top.max() * 1.15)
    ax2.grid(alpha=0.25, axis="x")
    ax2.spines["top"].set_visible(False); ax2.spines["right"].set_visible(False)

    fig.suptitle("Repeat players dominate the PTAB",
                 fontsize=14, fontweight="bold", color=NAVY, y=1.0)
    fig.tight_layout()
    out = OUT / "slide7_repeat_players.png"
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def chart_sector_variation() -> None:
    df = pd.read_parquet(JOINED, columns=["technology_center", "cancelled"])
    df = df[df["technology_center"].isin(TC_NAMES)]
    agg = df.groupby("technology_center")["cancelled"].agg(["mean", "count"]).reset_index()
    agg = agg[agg["count"] >= 50].sort_values("mean")
    agg["label"] = agg["technology_center"].map(lambda c: f"TC {c} — {TC_NAMES[c]}")

    base = df["cancelled"].mean()

    fig, ax = plt.subplots(figsize=(11, 5.5))
    colors = [ACCENT if m > base else NAVY for m in agg["mean"]]
    bars = ax.barh(range(len(agg)), agg["mean"] * 100, color=colors, alpha=0.9)
    ax.set_yticks(range(len(agg)))
    ax.set_yticklabels(agg["label"], fontsize=9.5)

    for i, (m, n) in enumerate(zip(agg["mean"], agg["count"], strict=True)):
        ax.text(m * 100 + 0.4, i, f"{m*100:.1f}%  (n={n:,})", va="center", fontsize=8.5, color=NAVY)

    ax.axvline(base * 100, color=MUTED, linestyle="--", linewidth=1.4)
    ax.text(base * 100 + 0.15, len(agg) - 0.3, f"global rate = {base*100:.1f}%",
            color=MUTED, fontsize=9, fontweight="bold")

    ax.set_xlabel("Cancellation rate (%)", fontsize=10)
    ax.set_xlim(0, agg["mean"].max() * 100 + 7)
    ax.set_title("Sector matters: cancellation rates by USPTO Technology Center",
                 fontsize=12, fontweight="bold", color=NAVY)

    safest = agg.iloc[0]; riskiest = agg.iloc[-1]
    spread = (riskiest["mean"] - safest["mean"]) * 100
    safe_name = TC_NAMES[safest["technology_center"]]; risk_name = TC_NAMES[riskiest["technology_center"]]
    ax.text(0.99, -0.14,
            f"Spread: {spread:.1f} pp from {safe_name} (TC {safest['technology_center']}, "
            f"{safest['mean']*100:.1f}%) to {risk_name} (TC {riskiest['technology_center']}, "
            f"{riskiest['mean']*100:.1f}%)",
            transform=ax.transAxes, ha="right", fontsize=9, color=MUTED, style="italic")

    ax.grid(alpha=0.25, axis="x")
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)

    fig.tight_layout()
    out = OUT / "slide7_sector_variation.png"
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


if __name__ == "__main__":
    chart_repeat_players()
    chart_sector_variation()
