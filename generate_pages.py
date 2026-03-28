"""Write report artifacts for GitHub Pages."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis.bridge_accounts import detect_bridge_accounts
from analysis.clustering import detect_affinity_clusters
from analysis.community_size import compute_all_sizes
from analysis.overlap import (
    build_affinity_matrix_from_results,
    build_overlap_matrix,
    compute_follow_affinity,
    compute_pairwise_overlap,
)
from tools.frontier_expansion import build_expansion_proposals, load_composite_community_ids

MARKER_START = "// __REPORT_DATA_START__"
MARKER_END = "// __REPORT_DATA_END__"
MAX_BRIDGE_ROWS = 120
MAX_BRIDGE_PAIR_ROWS = 40
MAX_PAIR_REPRESENTATIVES = 5
MAX_TOP_INFLUENCERS = 8
MAX_PROPOSALS = 12
MAX_PROPOSAL_ACCOUNTS = 6


def _trim_bridge_view(view: dict[str, object]) -> None:
    view["attention_hubs"] = list(view.get("attention_hubs", []))[:MAX_BRIDGE_ROWS]
    view["cross_cluster_attention_hubs"] = list(view.get("cross_cluster_attention_hubs", []))[:MAX_BRIDGE_ROWS]
    pairs = list(view.get("cluster_pairs", []))[:MAX_BRIDGE_PAIR_ROWS]
    for pair in pairs:
        pair["top_accounts"] = list(pair.get("top_accounts", []))[:MAX_PAIR_REPRESENTATIVES]
    view["cluster_pairs"] = pairs


def _trim_report_data_for_pages(report_data: dict[str, object]) -> dict[str, object]:
    page_data = copy.deepcopy(report_data)
    for community in page_data.get("communities", []):
        community["top_influencers"] = list(community.get("top_influencers", []))[:MAX_TOP_INFLUENCERS]

    bridges = page_data.get("bridges", {})
    if bridges:
        bridges["attention_hubs"] = list(bridges.get("attention_hubs", []))[:MAX_BRIDGE_ROWS]
        bridges["cross_cluster_attention_hubs"] = list(bridges.get("cross_cluster_attention_hubs", []))[:MAX_BRIDGE_ROWS]
        bridges["cluster_pairs"] = list(bridges.get("cluster_pairs", []))[:MAX_BRIDGE_PAIR_ROWS]
        for pair in bridges["cluster_pairs"]:
            pair["top_accounts"] = list(pair.get("top_accounts", []))[:MAX_PAIR_REPRESENTATIVES]
        for key in ("all_view", "no_nanpa_view", "frontier_view", "frontier_seed_view"):
            if key in bridges:
                _trim_bridge_view(bridges[key])

    expansion = page_data.get("expansion", {})
    if expansion:
        for key in ("proposals", "explore_proposals"):
            proposals = list(expansion.get(key, []))[:MAX_PROPOSALS]
            for proposal in proposals:
                proposal["top_accounts"] = list(proposal.get("top_accounts", []))[:MAX_PROPOSAL_ACCOUNTS]
                proposal["top_actionable_accounts"] = list(
                    proposal.get("top_actionable_accounts", [])
                )[:MAX_PROPOSAL_ACCOUNTS]
            expansion[key] = proposals
    return page_data


def generate_pages(min_confidence: float = 0.5) -> None:
    sizes = compute_all_sizes(min_confidence)
    overlaps = compute_pairwise_overlap(min_confidence)
    community_ids, overlap_matrix = build_overlap_matrix(min_confidence)

    affinities = compute_follow_affinity(min_confidence=min_confidence)
    affinity_ids, affinity_matrix = build_affinity_matrix_from_results(affinities)
    clusters = detect_affinity_clusters(min_confidence=min_confidence)
    bridges = detect_bridge_accounts(
        min_confidence=min_confidence,
        cluster_analysis=clusters,
    )
    composite_community_ids = load_composite_community_ids()
    expansion_proposals = build_expansion_proposals(
        min_confidence=min_confidence,
        bridge_analysis=bridges,
        composite_community_ids=composite_community_ids,
    )
    explore_expansion_proposals = build_expansion_proposals(
        min_confidence=min_confidence,
        bridge_analysis=bridges,
        exclude_composite_communities=True,
        composite_community_ids=composite_community_ids,
    )

    report_data = {
        "min_confidence": min_confidence,
        "communities": [asdict(size) for size in sizes],
        "overlaps": [asdict(overlap) for overlap in overlaps],
        "overlap_matrix": {
            "community_ids": community_ids,
            "matrix": overlap_matrix,
        },
        "affinities": [asdict(affinity) for affinity in affinities],
        "affinity_matrix": {
            "community_ids": affinity_ids,
            "matrix": affinity_matrix,
        },
        "clusters": asdict(clusters),
        "bridges": asdict(bridges),
        "expansion": {
            "combo_size": 3,
            "min_support": 6,
            "composite_community_ids": sorted(composite_community_ids),
            "proposals": [asdict(proposal) for proposal in expansion_proposals],
            "explore_proposals": [asdict(proposal) for proposal in explore_expansion_proposals],
        },
    }

    page_data = _trim_report_data_for_pages(report_data)

    docs_dir = Path(__file__).resolve().parent / "docs"
    html_path = docs_dir / "index.html"
    html = html_path.read_text(encoding="utf-8")
    json_str = json.dumps(page_data, ensure_ascii=False, separators=(",", ":"))
    placeholder = "const REPORT_DATA = null;"

    docs_json_path = docs_dir / "report.json"
    docs_json_path.write_text(json_str, encoding="utf-8")

    if MARKER_START in html and MARKER_END in html:
        before = html[:html.index(MARKER_START) + len(MARKER_START)]
        after = html[html.index(MARKER_END):]
        html = before + "\n" + placeholder + "\n" + after
    else:
        old = "const REPORT_DATA = null;"
        replacement = f"{MARKER_START}\n{placeholder}\n{MARKER_END}"
        if old in html:
            html = html.replace(old, replacement)
        else:
            lines = html.splitlines()
            new_lines: list[str] = []
            skipping = False
            for line in lines:
                if not skipping and "const REPORT_DATA = " in line:
                    new_lines.append(MARKER_START)
                    new_lines.append(placeholder)
                    new_lines.append(MARKER_END)
                    if line.rstrip().endswith(";"):
                        continue
                    skipping = True
                    continue
                if skipping:
                    if line.strip().endswith("};"):
                        skipping = False
                    continue
                new_lines.append(line)
            html = "\n".join(new_lines)

    html_path.write_text(html, encoding="utf-8")

    print("[OK] docs/index.html updated")
    print(f"[OK] docs/report.json updated")
    print(f"  Communities: {len(sizes)}")
    print(f"  Total members: {sum(size.member_count for size in sizes):,}")
    print(f"  Overlap pairs: {len([row for row in overlaps if row.jaccard > 0])}")
    print(f"  Affinity pairs: {len(affinities)}")
    print(f"  Bridge hubs: {bridges.attention_hub_count}")
    print(f"  Bridge hubs without nanpa: {bridges.no_nanpa_view.attention_hub_count}")
    print(f"  Frontier candidates: {bridges.frontier_view.attention_hub_count}")
    print(f"  Expansion proposals: {len(expansion_proposals)}")
    print(f"  Explore proposals: {len(explore_expansion_proposals)}")


if __name__ == "__main__":
    confidence = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    generate_pages(confidence)
