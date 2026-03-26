"""Embed current report data into docs/index.html for GitHub Pages."""

from __future__ import annotations

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

MARKER_START = "// __REPORT_DATA_START__"
MARKER_END = "// __REPORT_DATA_END__"


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
    }

    docs_dir = Path(__file__).resolve().parent / "docs"
    html_path = docs_dir / "index.html"
    html = html_path.read_text(encoding="utf-8")
    json_str = json.dumps(report_data, ensure_ascii=False, indent=2)

    if MARKER_START in html and MARKER_END in html:
        before = html[:html.index(MARKER_START) + len(MARKER_START)]
        after = html[html.index(MARKER_END):]
        html = before + "\nconst REPORT_DATA = " + json_str + ";\n" + after
    else:
        old = "const REPORT_DATA = null;"
        replacement = f"{MARKER_START}\nconst REPORT_DATA = {json_str};\n{MARKER_END}"
        if old in html:
            html = html.replace(old, replacement)
        else:
            lines = html.splitlines()
            new_lines: list[str] = []
            skipping = False
            for line in lines:
                if not skipping and "const REPORT_DATA = " in line:
                    new_lines.append(MARKER_START)
                    new_lines.append(f"const REPORT_DATA = {json_str};")
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
    print(f"  Communities: {len(sizes)}")
    print(f"  Total members: {sum(size.member_count for size in sizes):,}")
    print(f"  Overlap pairs: {len([row for row in overlaps if row.jaccard > 0])}")
    print(f"  Affinity pairs: {len(affinities)}")
    print(f"  Bridge hubs: {bridges.attention_hub_count}")
    print(f"  Bridge hubs without nanpa: {bridges.no_nanpa_view.attention_hub_count}")
    print(f"  Frontier candidates: {bridges.frontier_view.attention_hub_count}")


if __name__ == "__main__":
    confidence = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    generate_pages(confidence)
