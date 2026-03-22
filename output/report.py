"""レポート生成"""
import json
from dataclasses import asdict
from pathlib import Path

from analysis.community_size import SizeMetrics, compute_all_sizes
from analysis.overlap import OverlapResult, build_overlap_matrix, compute_pairwise_overlap
from config.settings import EXPORT_DIR


def generate_report(min_confidence: float = 0.5):
    """全界隈の規模 + 重複レポートを生成"""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    # 規模メトリクス
    sizes = compute_all_sizes(min_confidence)
    overlaps = compute_pairwise_overlap(min_confidence)
    community_ids, matrix = build_overlap_matrix(min_confidence)

    # JSON出力
    report_data = {
        "min_confidence": min_confidence,
        "communities": [asdict(s) for s in sizes],
        "overlaps": [asdict(o) for o in overlaps],
        "overlap_matrix": {
            "community_ids": community_ids,
            "matrix": matrix,
        },
    }
    json_path = EXPORT_DIR / "report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    print(f"[SAVE] {json_path}")

    # Markdown出力
    md_path = EXPORT_DIR / "report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Twitter 界隈調査レポート\n\n")

        # 規模テーブル
        f.write("## 界隈規模\n\n")
        f.write("| 界隈 | メンバー数 | アクティブ | 総リーチ | 中央値フォロワー | インフルエンサー |\n")
        f.write("|------|-----------|-----------|---------|----------------|----------------|\n")
        for s in sizes:
            f.write(
                f"| {s.community_name} | {s.member_count:,} | {s.active_member_count:,} "
                f"| {s.total_followers_reach:,} | {s.median_followers:,} | {s.influencer_count} |\n"
            )

        # 重複テーブル
        if overlaps:
            f.write("\n## 界隈間の重複\n\n")
            f.write("| 界隈A | 界隈B | 共通メンバー | Jaccard | A→B含有率 | B→A含有率 |\n")
            f.write("|-------|-------|-------------|---------|----------|----------|\n")
            for o in overlaps:
                f.write(
                    f"| {o.community_a} | {o.community_b} | {o.intersection_count:,} "
                    f"| {o.jaccard:.3f} | {o.containment_a_in_b:.1%} | {o.containment_b_in_a:.1%} |\n"
                )

        # 各界隈のトップインフルエンサー
        f.write("\n## トップインフルエンサー\n\n")
        for s in sizes:
            if s.top_influencers:
                f.write(f"### {s.community_name}\n\n")
                for inf in s.top_influencers[:5]:
                    f.write(f"- @{inf['screen_name']} ({inf['followers_count']:,} followers) - {inf['bio']}\n")
                f.write("\n")

    print(f"[SAVE] {md_path}")

    # コンソール出力
    print("\n" + "=" * 60)
    print("界隈規模サマリ")
    print("=" * 60)
    for s in sizes:
        print(f"  {s.community_name}: {s.member_count:,} メンバー, リーチ {s.total_followers_reach:,}")
    if overlaps:
        print("\n界隈重複 TOP5:")
        for o in overlaps[:5]:
            print(f"  {o.community_a} × {o.community_b}: Jaccard={o.jaccard:.3f} (共通{o.intersection_count}人)")
