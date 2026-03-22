"""可視化（ヒートマップ・ネットワークグラフ）"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

from analysis.community_size import compute_all_sizes
from analysis.overlap import build_overlap_matrix
from config.settings import EXPORT_DIR


def plot_overlap_heatmap(min_confidence: float = 0.5):
    """界隈間Jaccard重複ヒートマップ"""
    community_ids, matrix = build_overlap_matrix(min_confidence)
    if len(community_ids) < 2:
        print("[INFO] ヒートマップには2つ以上の界隈が必要です")
        return

    # 界隈名を取得
    from db.models import Community, get_session, init_db
    init_db()
    session = get_session()
    names = []
    for cid in community_ids:
        c = session.get(Community, cid)
        names.append(c.name if c else cid)
    session.close()

    fig, ax = plt.subplots(figsize=(max(8, len(names) * 1.2), max(6, len(names) * 1.0)))
    plt.rcParams["font.family"] = "MS Gothic"  # 日本語フォント

    arr = np.array(matrix)
    mask = np.eye(len(names), dtype=bool)  # 対角線をマスク

    sns.heatmap(
        arr, mask=mask, annot=True, fmt=".3f",
        xticklabels=names, yticklabels=names,
        cmap="YlOrRd", vmin=0, vmax=1,
        ax=ax, square=True,
    )
    ax.set_title("界隈間 Jaccard 重複度", fontsize=14)
    plt.tight_layout()

    out_path = EXPORT_DIR / "overlap_heatmap.png"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[SAVE] {out_path}")


def plot_community_sizes(min_confidence: float = 0.5):
    """界隈規模バーチャート"""
    sizes = compute_all_sizes(min_confidence)
    if not sizes:
        print("[INFO] 界隈データがありません")
        return

    plt.rcParams["font.family"] = "MS Gothic"

    names = [s.community_name for s in sizes]
    counts = [s.member_count for s in sizes]

    fig, ax = plt.subplots(figsize=(max(8, len(names) * 1.5), 6))
    bars = ax.barh(range(len(names)), counts, color="steelblue")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names)
    ax.set_xlabel("メンバー数")
    ax.set_title("界隈規模ランキング", fontsize=14)
    ax.invert_yaxis()

    for bar, count in zip(bars, counts):
        ax.text(bar.get_width() + max(counts) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{count:,}", va="center", fontsize=10)

    plt.tight_layout()
    out_path = EXPORT_DIR / "community_sizes.png"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[SAVE] {out_path}")


def plot_network_graph(min_confidence: float = 0.5):
    """界隈ネットワークグラフ（重複をエッジで表現）"""
    try:
        import networkx as nx
        from pyvis.network import Network
    except ImportError:
        print("[WARN] networkx/pyvis が必要です: pip install networkx pyvis")
        return

    from analysis.overlap import compute_pairwise_overlap
    from analysis.community_size import compute_all_sizes

    sizes = {s.community_id: s for s in compute_all_sizes(min_confidence)}
    overlaps = compute_pairwise_overlap(min_confidence)

    G = nx.Graph()
    for cid, s in sizes.items():
        G.add_node(cid, label=s.community_name, size=max(10, min(s.member_count / 50, 80)))

    for o in overlaps:
        if o.jaccard > 0.01:  # 1%以上の重複のみ
            G.add_edge(o.community_a, o.community_b, weight=o.jaccard,
                       title=f"Jaccard: {o.jaccard:.3f}\n共通: {o.intersection_count}人")

    net = Network(height="700px", width="100%", bgcolor="#ffffff", font_color="black")
    net.from_nx(G)
    net.toggle_physics(True)

    out_path = EXPORT_DIR / "community_network.html"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    net.save_graph(str(out_path))
    print(f"[SAVE] {out_path}")
