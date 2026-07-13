"""Visualization charts for pref-04 harness results.

Usage:
    python -m harness.plot                      # all runs, shows plots
    python -m harness.plot --run-id pref-04
    python -m harness.plot --run-id pref-04 --save   # saves PNGs to harness/plots/
    python -m harness.plot --run-id pref-04 --window 10  # rolling window size
"""

import argparse
import json
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
import numpy as np

DB_PATH = Path(__file__).parent.parent / "harness.db"

PALETTE = {
    "local2_better": "#34d399",
    "native_better": "#f87171",
    "tie":           "#6b7280",
    "pending":       "#374151",
}

sns.set_theme(style="darkgrid", palette="muted")
plt.rcParams.update({
    "figure.facecolor":  "#0f1117",
    "axes.facecolor":    "#181c26",
    "axes.edgecolor":    "#2a2f45",
    "axes.labelcolor":   "#c8cfe8",
    "xtick.color":       "#5a6080",
    "ytick.color":       "#5a6080",
    "text.color":        "#c8cfe8",
    "grid.color":        "#2a2f45",
    "grid.linewidth":    0.5,
    "legend.facecolor":  "#181c26",
    "legend.edgecolor":  "#2a2f45",
})


def _load(run_id: str | None) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    where = "WHERE i.run_id = ?" if run_id else ""
    params = (run_id,) if run_id else ()
    rows = con.execute(f"""
        SELECT
          ROW_NUMBER() OVER (ORDER BY i.timestamp) AS row_num,
          j.verdict,
          i.arm_a_tool_calls,
          i.arm_a_capsules
        FROM items i
        LEFT JOIN judgments j ON j.item_id = i.item_id AND j.run_id = i.run_id
        {where}
        ORDER BY i.timestamp
    """, params).fetchall()

    result = []
    for r in rows:
        tool_calls = json.loads(r["arm_a_tool_calls"] or "[]")
        capsules   = json.loads(r["arm_a_capsules"]   or "[]")
        persona = ""
        for tc in tool_calls:
            if tc.get("tool") == "persona":
                persona = tc.get("args", {}).get("mode", "")
        result.append({
            "row":     r["row_num"],
            "verdict": r["verdict"],
            "persona": persona or "none",
            "caps":    len(capsules),
            "l2":      1 if r["verdict"] == "local2_better" else 0,
            "nat":     1 if r["verdict"] == "native_better" else 0,
            "tie":     1 if r["verdict"] == "tie" else 0,
        })
    return result


def plot_rolling_rates(rows: list[dict], window: int, ax: plt.Axes) -> None:
    """Line chart: rolling tie/L2/NAT rates over items."""
    xs = [r["row"] for r in rows]
    ties = [r["tie"] for r in rows]
    l2s  = [r["l2"]  for r in rows]
    nats = [r["nat"] for r in rows]

    def rolling(vals):
        out = []
        for i in range(len(vals)):
            lo = max(0, i - window + 1)
            chunk = vals[lo:i+1]
            out.append(sum(chunk) / len(chunk) * 100)
        return out

    ax.plot(xs, rolling(ties), color=PALETTE["tie"],           lw=2,   label="TIE",     alpha=0.9)
    ax.plot(xs, rolling(l2s),  color=PALETTE["local2_better"], lw=2,   label="L2 WIN",  alpha=0.9)
    ax.plot(xs, rolling(nats), color=PALETTE["native_better"], lw=2,   label="NAT WIN", alpha=0.9)

    ax.set_title(f"Rolling verdict rates  (window={window})", color="#818cf8")
    ax.set_xlabel("Item #")
    ax.set_ylabel("Rate (%)")
    ax.yaxis.set_major_formatter(mticker.PercentFormatter())
    ax.set_ylim(0, 105)
    ax.legend()


def plot_band_bars(rows: list[dict], band_size: int, ax: plt.Axes) -> None:
    """Stacked bar chart: verdict counts per band."""
    bands: dict[int, dict] = {}
    for r in rows:
        b = ((r["row"] - 1) // band_size) + 1
        s = bands.setdefault(b, {"l2": 0, "nat": 0, "tie": 0, "n": 0})
        s["n"] += 1
        if r["verdict"] == "local2_better":   s["l2"] += 1
        elif r["verdict"] == "native_better": s["nat"] += 1
        elif r["verdict"] == "tie":           s["tie"] += 1

    labels = [f"{(b-1)*band_size+1}–{min(b*band_size, rows[-1]['row'])}" for b in sorted(bands)]
    l2s  = [bands[b]["l2"]  for b in sorted(bands)]
    nats = [bands[b]["nat"] for b in sorted(bands)]
    ties = [bands[b]["tie"] for b in sorted(bands)]
    x = np.arange(len(labels))

    ax.bar(x, ties, label="TIE",     color=PALETTE["tie"],           alpha=0.85)
    ax.bar(x, l2s,  label="L2 WIN",  color=PALETTE["local2_better"], alpha=0.85, bottom=ties)
    ax.bar(x, nats, label="NAT WIN", color=PALETTE["native_better"], alpha=0.85,
           bottom=[t + l for t, l in zip(ties, l2s)])

    ax.set_title(f"Verdict counts per {band_size}-item band", color="#818cf8")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("Count")
    ax.legend()


def plot_persona_decisive(rows: list[dict], ax: plt.Axes) -> None:
    """Horizontal bar: decisive-L2% by persona."""
    stats: dict[str, dict] = {}
    for r in rows:
        p = r["persona"]
        s = stats.setdefault(p, {"l2": 0, "nat": 0, "n": 0})
        s["n"] += 1
        if r["verdict"] == "local2_better":   s["l2"] += 1
        elif r["verdict"] == "native_better": s["nat"] += 1

    personas = sorted(stats, key=lambda p: -stats[p]["n"])
    dl2s = []
    counts = []
    for p in personas:
        s = stats[p]
        d = s["l2"] + s["nat"]
        dl2s.append(s["l2"] * 100 / d if d else 0)
        counts.append(s["n"])

    colors = [PALETTE["local2_better"] if v >= 60 else
              PALETTE["native_better"] if v < 50 else
              PALETTE["tie"] for v in dl2s]

    bars = ax.barh(personas, dl2s, color=colors, alpha=0.85)
    ax.axvline(50, color="#ffffff", lw=0.8, linestyle="--", alpha=0.4, label="50% (even)")
    for bar, n in zip(bars, counts):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                f"n={n}", va="center", fontsize=9, color="#5a6080")

    ax.set_title("Decisive-L2% by persona", color="#818cf8")
    ax.set_xlabel("L2 win rate among decisive outcomes (%)")
    ax.set_xlim(0, 115)
    ax.legend()


def plot_caps_decisive(rows: list[dict], ax: plt.Axes) -> None:
    """Bar chart: decisive-L2% by capsule count."""
    stats: dict[int, dict] = {}
    for r in rows:
        s = stats.setdefault(r["caps"], {"l2": 0, "nat": 0, "n": 0})
        s["n"] += 1
        if r["verdict"] == "local2_better":   s["l2"] += 1
        elif r["verdict"] == "native_better": s["nat"] += 1

    caps_vals = sorted(stats)
    dl2s   = []
    counts = []
    for c in caps_vals:
        s = stats[c]
        d = s["l2"] + s["nat"]
        dl2s.append(s["l2"] * 100 / d if d else 0)
        counts.append(s["n"])

    colors = [PALETTE["local2_better"] if v >= 60 else
              PALETTE["native_better"] if v < 50 else
              PALETTE["tie"] for v in dl2s]

    bars = ax.bar(caps_vals, dl2s, color=colors, alpha=0.85, width=0.6)
    ax.axhline(50, color="#ffffff", lw=0.8, linestyle="--", alpha=0.4)
    for bar, n in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5,
                f"n={n}", ha="center", fontsize=8, color="#5a6080")

    ax.set_title("Decisive-L2% by capsules used", color="#818cf8")
    ax.set_xlabel("Capsules injected")
    ax.set_ylabel("L2 win rate among decisive (%)")
    ax.set_ylim(0, 115)
    ax.set_xticks(caps_vals)


def make_figure(rows: list[dict], run_id: str | None, window: int, band_size: int) -> plt.Figure:
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle(f"LoCAL2 Harness — {run_id or 'all runs'}  ({len(rows)} items)",
                 fontsize=14, color="#818cf8", y=1.01)
    fig.patch.set_facecolor("#0f1117")

    plot_rolling_rates(rows,    window,    axes[0][0])
    plot_band_bars(rows,        band_size, axes[0][1])
    plot_persona_decisive(rows,            axes[1][0])
    plot_caps_decisive(rows,               axes[1][1])

    fig.tight_layout()
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(description="LoCAL2 harness visualizations")
    parser.add_argument("--run-id",    default=None)
    parser.add_argument("--window",    default=15,  type=int, help="Rolling window size (default: 15)")
    parser.add_argument("--band-size", default=25,  type=int, help="Band size for bar chart (default: 25)")
    parser.add_argument("--save",      action="store_true",   help="Save PNGs to harness/plots/")
    args = parser.parse_args()

    rows = _load(args.run_id)
    if not rows:
        print("No data found.")
        return

    print(f"Loaded {len(rows)} items.")
    fig = make_figure(rows, args.run_id, args.window, args.band_size)

    if args.save:
        out_dir = Path(__file__).parent / "plots"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"{args.run_id or 'all'}_charts.png"
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        print(f"Saved: {path}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
