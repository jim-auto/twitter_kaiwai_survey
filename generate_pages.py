"""分析結果をGitHub Pages (docs/index.html) に埋め込む"""
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis.community_size import compute_all_sizes
from analysis.overlap import build_overlap_matrix, compute_pairwise_overlap
from db.models import init_db


def generate_pages(min_confidence: float = 0.5):
    init_db()

    sizes = compute_all_sizes(min_confidence)
    overlaps = compute_pairwise_overlap(min_confidence)
    community_ids, matrix = build_overlap_matrix(min_confidence)

    report_data = {
        "min_confidence": min_confidence,
        "communities": [asdict(s) for s in sizes],
        "overlaps": [asdict(o) for o in overlaps],
        "overlap_matrix": {
            "community_ids": community_ids,
            "matrix": matrix,
        },
    }

    json_str = json.dumps(report_data, ensure_ascii=False, indent=2)

    docs_dir = Path(__file__).resolve().parent / "docs"
    html_path = docs_dir / "index.html"

    html = html_path.read_text(encoding="utf-8")
    # Replace REPORT_DATA whether it's null or already has data
    html = re.sub(
        r"const REPORT_DATA = .+?;\s*\n",
        f"const REPORT_DATA = {json_str};\n",
        html,
        count=1,
        flags=re.DOTALL,
    )
    html_path.write_text(html, encoding="utf-8")

    print(f"[OK] docs/index.html updated")
    print(f"  Communities: {len(sizes)}")
    print(f"  Total members: {sum(s.member_count for s in sizes):,}")
    print(f"  Overlap pairs: {len(overlaps)}")
    for o in overlaps[:5]:
        if o.jaccard > 0:
            print(f"    {o.community_a} x {o.community_b}: Jaccard={o.jaccard:.3f} ({o.intersection_count} shared)")


if __name__ == "__main__":
    confidence = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    generate_pages(confidence)
