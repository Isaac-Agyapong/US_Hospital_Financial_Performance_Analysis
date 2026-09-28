"""Shared chart style: a financial report look (serif headlines under a thick navy rule, warm white paper,
thin horizontal gridlines, direct labels). The Power BI report uses the same palette.

Colour meanings, used the same way in every chart, the dashboard and the README:
    NAVY    all hospitals / the main series
    BRICK   losing money and financial stress (used for nothing else)
    OCHRE   the highlighted comparison group (rural hospitals, states that did not expand Medicaid)
    GREY    everything else
"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter

NAVY, NAVY_L = "#1d3557", "#8da9c4"
BRICK, BRICK_L = "#b23a2e", "#e8a79b"
OCHRE, OCHRE_L = "#d08c15", "#f1d193"
GREY, GREY_L = "#8b8f94", "#d3d5d8"
PAPER, INK, INK_2, RULE = "#fbf9f4", "#1a1d21", "#5d636b", "#e6e2d8"
HEAD_FONT = ["Georgia", "DejaVu Serif"]
BODY_FONT = ["Segoe UI", "DejaVu Sans"]

# Tile-map positions (column, row) for the 50 states + DC, arranged like a US map
STATE_GRID = {
    "AK": (0, 0), "ME": (10, 0),
    "WI": (5, 1), "VT": (9, 1), "NH": (10, 1),
    "WA": (0, 2), "ID": (1, 2), "MT": (2, 2), "ND": (3, 2), "MN": (4, 2), "IL": (5, 2), "MI": (6, 2),
    "NY": (8, 2), "MA": (9, 2),
    "OR": (0, 3), "NV": (1, 3), "WY": (2, 3), "SD": (3, 3), "IA": (4, 3), "IN": (5, 3), "OH": (6, 3),
    "PA": (7, 3), "NJ": (8, 3), "CT": (9, 3), "RI": (10, 3),
    "CA": (0, 4), "UT": (1, 4), "CO": (2, 4), "NE": (3, 4), "MO": (4, 4), "KY": (5, 4), "WV": (6, 4),
    "VA": (7, 4), "MD": (8, 4), "DE": (9, 4),
    "AZ": (1, 5), "NM": (2, 5), "KS": (3, 5), "AR": (4, 5), "TN": (5, 5), "NC": (6, 5), "SC": (7, 5), "DC": (8, 5),
    "OK": (3, 6), "LA": (4, 6), "MS": (5, 6), "AL": (6, 6), "GA": (7, 6),
    "HI": (0, 7), "TX": (3, 7), "FL": (8, 7),
}

IMAGE_DIR = Path(__file__).resolve().parents[1] / "Image"
IMAGE_DIR.mkdir(exist_ok=True)


def apply():
    plt.rcParams.update({
        "figure.facecolor": PAPER, "axes.facecolor": PAPER, "savefig.facecolor": PAPER,
        "figure.dpi": 110, "savefig.dpi": 150, "figure.figsize": (9, 4.8),
        "font.family": BODY_FONT, "font.size": 10.5,
        "text.color": INK, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
        "axes.edgecolor": INK_2, "axes.linewidth": 0.9,
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": RULE, "grid.linewidth": 0.9,
        "axes.axisbelow": True, "legend.frameon": False,
        "lines.linewidth": 2.4, "lines.solid_capstyle": "round",
        "xtick.major.size": 0, "ytick.major.size": 0,
    })


def title(ax, text, sub=None):
    """Serif headline stating the finding, a grey one-line takeaway, and a thick navy rule above both."""
    ax.set_title(text, fontsize=16, fontweight="bold", loc="left", pad=34 if sub else 16, color=INK,
                 fontfamily=HEAD_FONT)
    if sub:
        ax.annotate(sub, (0, 1), xycoords="axes fraction", xytext=(0, 10), textcoords="offset points",
                    color=INK_2, fontsize=10.5, va="bottom")
    ax._navy_rule = True    # thick navy rule above the headline, drawn in save() once the layout is final


def pct(ax, axis="y", decimals=0):
    fmt = FuncFormatter(lambda v, _: f"{v:.{decimals}f}%")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def dollars(ax, axis="y"):
    fmt = FuncFormatter(lambda v, _: f"${v/1000:,.0f}k" if abs(v) >= 1000 else f"${v:,.0f}")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def source(fig, text="Source: CMS Hospital Provider Cost Reports 2011-2023. General acute care and critical access "
                     "hospitals, 50 states + DC."):
    fig.text(0.01, -0.02, text, color=GREY, fontsize=8, ha="left", va="top")


def save(fig, name):
    fig.tight_layout()
    fig.canvas.draw()
    w, h = fig.get_size_inches() * fig.dpi
    for ax in fig.axes:
        if getattr(ax, "_navy_rule", False):
            top = ax.title.get_window_extent().y1 + 24 / 72 * fig.dpi     # 24 points above the headline
            x0 = ax.get_position().x0
            fig.add_artist(Line2D([x0, x0 + 60 / 72 * fig.dpi / w], [top / h] * 2, transform=fig.transFigure,
                                  color=NAVY, lw=5, solid_capstyle="butt"))
    fig.savefig(IMAGE_DIR / f"{name}.png", bbox_inches="tight")
    plt.close(fig)
