"""分析結果をGitHub Pages (docs/index.html) に埋め込む"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis.community_size import compute_all_sizes
from analysis.overlap import (
    build_affinity_matrix,
    build_overlap_matrix,
    compute_follow_affinity,
    compute_pairwise_overlap,
)
from db.models import init_db

MARKER_START = "// __REPORT_DATA_START__"
MARKER_END = "// __REPORT_DATA_END__"


def generate_pages(min_confidence: float = 0.5):
    sizes = compute_all_sizes(min_confidence)
    overlaps = compute_pairwise_overlap(min_confidence)
    community_ids, matrix = build_overlap_matrix(min_confidence)

    affinities = compute_follow_affinity(min_confidence=0.5)
    affinity_ids, affinity_matrix = build_affinity_matrix(min_confidence=0.5)

    report_data = {
        "min_confidence": min_confidence,
        "communities": [asdict(s) for s in sizes],
        "overlaps": [asdict(o) for o in overlaps],
        "overlap_matrix": {
            "community_ids": community_ids,
            "matrix": matrix,
        },
        "affinities": [asdict(a) for a in affinities],
        "affinity_matrix": {
            "community_ids": affinity_ids,
            "matrix": affinity_matrix,
        },
    }

    json_str = json.dumps(report_data, ensure_ascii=False, indent=2)

    docs_dir = Path(__file__).resolve().parent / "docs"
    html_path = docs_dir / "index.html"
    html = html_path.read_text(encoding="utf-8")

    # マーカーベースの高速置換
    if MARKER_START in html and MARKER_END in html:
        before = html[:html.index(MARKER_START) + len(MARKER_START)]
        after = html[html.index(MARKER_END):]
        html = before + "\nconst REPORT_DATA = " + json_str + ";\n" + after
    else:
        # 初回：nullを置換してマーカー追加
        old = "const REPORT_DATA = null;"
        if old in html:
            html = html.replace(old, f"{MARKER_START}\nconst REPORT_DATA = {json_str};\n{MARKER_END}")
        else:
            # 既にデータが入っている場合: 行単位で探す
            lines = html.split("\n")
            new_lines = []
            skipping = False
            for line in lines:
                if "const REPORT_DATA = " in line and not skipping:
                    new_lines.append(f"{MARKER_START}")
                    new_lines.append(f"const REPORT_DATA = {json_str};")
                    new_lines.append(f"{MARKER_END}")
                    # 後続の}; まで飛ばすのは難しいので、この行だけ置換
                    # ただしJSON複数行の場合はスキップが必要
                    if line.rstrip().endswith(";"):
                        continue
                    else:
                        skipping = True
                        continue
                elif skipping:
                    if line.strip().endswith("};"):
                        skipping = False
                    continue
                else:
                    new_lines.append(line)
            html = "\n".join(new_lines)

    html_path.write_text(html, encoding="utf-8")

    print(f"[OK] docs/index.html updated")
    print(f"  Communities: {len(sizes)}")
    print(f"  Total members: {sum(s.member_count for s in sizes):,}")
    print(f"  Overlap pairs: {len([o for o in overlaps if o.jaccard > 0])}")
    print(f"  Affinity pairs: {len(affinities)}")
    for a in affinities[:5]:
        print(f"    {a.community_a} x {a.community_b}: {a.affinity:.6f}")


if __name__ == "__main__":
    confidence = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    generate_pages(confidence)
