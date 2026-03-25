"""レポート生成"""
import json
from dataclasses import asdict
from pathlib import Path

from analysis.clustering import detect_affinity_clusters
from analysis.community_size import SizeMetrics, compute_all_sizes
from analysis.overlap import OverlapResult, build_overlap_matrix, compute_pairwise_overlap
from config.settings import EXPORT_DIR


def generate_report(min_confidence: float = 0.5):
    """全界隈の規模 + 重複 + クラスタレポートを生成"""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    # 規模メトリクス
    sizes = compute_all_sizes(min_confidence)
    overlaps = compute_pairwise_overlap(min_confidence)
    community_ids, matrix = build_overlap_matrix(min_confidence)
    clusters = detect_affinity_clusters(min_confidence=min_confidence)

    # JSON出力
    report_data = {
        "min_confidence": min_confidence,
        "communities": [asdict(s) for s in sizes],
        "overlaps": [asdict(o) for o in overlaps],
        "overlap_matrix": {
            "community_ids": community_ids,
            "matrix": matrix,
        },
        "clusters": asdict(clusters),
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

        f.write("\n## 界隈クラスタ（フォロー親和度ベース）\n\n")
        f.write(
            f"- 手法: {clusters.method}\n"
            f"- 親和度しきい値: {clusters.min_edge_weight:.6f} "
            f"(max_affinity={clusters.max_affinity:.6f}, ratio={clusters.min_edge_ratio:.2f})\n"
        )
        if clusters.clusters:
            for cluster in clusters.clusters:
                members = ", ".join(cluster.communities)
                f.write(f"\n### Cluster {cluster.cluster_id}\n\n")
                f.write(f"- 界隈: {members}\n")
                f.write(f"- 内部親和度合計: {cluster.internal_weight_sum:.6f}\n")
                if cluster.strongest_edges:
                    f.write("- 強い結線:\n")
                    for edge in cluster.strongest_edges:
                        f.write(
                            f"  - {edge.community_a} × {edge.community_b}: {edge.affinity:.6f} "
                            f"({edge.a_follows_b_count}<->{edge.b_follows_a_count})\n"
                        )
                if cluster.representative_accounts:
                    f.write("- 代表アカウント:\n")
                    for account in cluster.representative_accounts:
                        communities = ", ".join(account.community_ids)
                        label = f"@{account.screen_name}" if account.screen_name else account.user_id
                        display = f" / {account.display_name}" if account.display_name else ""
                        f.write(
                            f"  - {label}{display}: {account.followers_count:,} followers "
                            f"[{communities}]\n"
                        )
        if clusters.isolated_communities:
            f.write("\n### Isolated\n\n")
            f.write(f"- {', '.join(clusters.isolated_communities)}\n")

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
    if clusters.clusters:
        print("\n界隈クラスタ:")
        for cluster in clusters.clusters:
            print(f"  Cluster {cluster.cluster_id}: {', '.join(cluster.communities)}")
    if clusters.isolated_communities:
        print(f"\n孤立界隈: {', '.join(clusters.isolated_communities)}")
