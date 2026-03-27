"""Generate markdown/json reports for kaiwai analysis."""

from __future__ import annotations

import json
from dataclasses import asdict

from analysis.bridge_accounts import AttentionBridgeView, detect_bridge_accounts
from analysis.clustering import detect_affinity_clusters
from analysis.community_size import compute_all_sizes
from analysis.overlap import build_overlap_matrix, compute_pairwise_overlap
from config.settings import EXPORT_DIR
from tools.frontier_expansion import build_expansion_proposals


def _format_attention_account(account) -> str:
    label = f"@{account.screen_name}" if account.screen_name else account.user_id
    if account.display_name:
        label = f"{label} / {account.display_name}"
    communities = ", ".join(account.source_community_ids)
    clusters = ", ".join(account.cluster_labels)
    return (
        f"- {label}: score={account.bridge_score:.3f}, "
        f"type={account.account_category}, "
        f"communities={account.source_community_count}, clusters={account.cluster_count}, "
        f"edges={account.follow_edge_count}, followers={account.followers_count:,} "
        f"[{communities}] ({clusters})"
    )


def _format_member_account(account) -> str:
    label = f"@{account.screen_name}" if account.screen_name else account.user_id
    if account.display_name:
        label = f"{label} / {account.display_name}"
    communities = ", ".join(account.community_ids)
    clusters = ", ".join(account.cluster_labels)
    return (
        f"- {label}: score={account.bridge_score:.3f}, "
        f"communities={account.community_count}, clusters={account.cluster_count}, "
        f"followers={account.followers_count:,} [{communities}] ({clusters})"
    )


def _write_attention_view_section(f, view: AttentionBridgeView, limit: int = 20) -> None:
    community_seeds = [
        account for account in view.attention_hubs
        if account.account_category == "community_seed"
    ]
    generic_hubs = [
        account for account in view.attention_hubs
        if account.account_category != "community_seed"
    ]

    f.write(f"\n### {view.label}\n\n")
    f.write(f"- description: {view.description}\n")
    if view.excluded_community_ids:
        f.write(f"- excluded_communities: {', '.join(view.excluded_community_ids)}\n")
    f.write(f"- attention_hubs: {view.attention_hub_count}\n")
    f.write(f"- cross_cluster_attention_hubs: {view.cross_cluster_attention_hub_count}\n")
    f.write(f"- community_seeds: {view.community_seed_count}\n")
    f.write(f"- generic_hubs: {view.generic_hub_count}\n")
    if view.category_counts:
        f.write(
            "- category_counts: "
            + ", ".join(f"{key}={value}" for key, value in sorted(view.category_counts.items()))
            + "\n"
        )

    if community_seeds:
        f.write("\nTop community seeds:\n")
        for account in community_seeds[:limit]:
            f.write(_format_attention_account(account) + "\n")
    if generic_hubs:
        f.write("\nTop generic hubs:\n")
        for account in generic_hubs[: min(10, limit)]:
            f.write(_format_attention_account(account) + "\n")
    if not view.attention_hubs:
        f.write("\n- none\n")

    if view.cluster_pairs:
        f.write("\nTop cluster pairs:\n")
        for pair in view.cluster_pairs[:10]:
            reps = ", ".join(f"@{account.screen_name}" for account in pair.top_accounts[:3])
            suffix = f" ({reps})" if reps else ""
            f.write(f"- {pair.cluster_a} x {pair.cluster_b}: {pair.account_count} accounts{suffix}\n")
    else:
        f.write("\n- cluster_pairs: none\n")


def generate_report(min_confidence: float = 0.5) -> None:
    """Generate community report artifacts."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    sizes = compute_all_sizes(min_confidence)
    overlaps = compute_pairwise_overlap(min_confidence)
    community_ids, matrix = build_overlap_matrix(min_confidence)
    clusters = detect_affinity_clusters(min_confidence=min_confidence)
    bridges = detect_bridge_accounts(
        min_confidence=min_confidence,
        cluster_analysis=clusters,
    )
    expansion_proposals = build_expansion_proposals(
        min_confidence=min_confidence,
        bridge_analysis=bridges,
    )

    report_data = {
        "min_confidence": min_confidence,
        "communities": [asdict(size) for size in sizes],
        "overlaps": [asdict(overlap) for overlap in overlaps],
        "overlap_matrix": {
            "community_ids": community_ids,
            "matrix": matrix,
        },
        "clusters": asdict(clusters),
        "bridges": asdict(bridges),
        "expansion": {
            "combo_size": 3,
            "min_support": 6,
            "proposals": [asdict(proposal) for proposal in expansion_proposals],
        },
    }

    json_path = EXPORT_DIR / "report.json"
    json_path.write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[SAVE] {json_path}")

    md_path = EXPORT_DIR / "report.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# Twitter Kaiwai Report\n")
        f.write(f"\n- min_confidence: {min_confidence}\n")

        f.write("\n## Community Sizes\n\n")
        f.write("| Community | Members | Active | Reach | Median FL | Influencers |\n")
        f.write("|-----------|--------:|-------:|------:|----------:|------------:|\n")
        for size in sizes:
            f.write(
                f"| {size.community_name} | {size.member_count:,} | {size.active_member_count:,} "
                f"| {size.total_followers_reach:,} | {size.median_followers:,} | {size.influencer_count} |\n"
            )

        if overlaps:
            f.write("\n## Member Overlap\n\n")
            f.write("| Community A | Community B | Shared | Jaccard | A in B | B in A |\n")
            f.write("|-------------|-------------|-------:|--------:|-------:|-------:|\n")
            for overlap in overlaps:
                f.write(
                    f"| {overlap.community_a} | {overlap.community_b} | {overlap.intersection_count:,} "
                    f"| {overlap.jaccard:.3f} | {overlap.containment_a_in_b:.1%} | {overlap.containment_b_in_a:.1%} |\n"
                )

        f.write("\n## Affinity Clusters\n\n")
        f.write(
            f"- method: {clusters.method}\n"
            f"- min_edge_weight: {clusters.min_edge_weight:.6f} "
            f"(max_affinity={clusters.max_affinity:.6f}, ratio={clusters.min_edge_ratio:.2f})\n"
        )
        for cluster in clusters.clusters:
            f.write(f"\n### Cluster {cluster.cluster_id}\n\n")
            f.write(f"- communities: {', '.join(cluster.communities)}\n")
            f.write(f"- internal_weight_sum: {cluster.internal_weight_sum:.6f}\n")
            if cluster.strongest_edges:
                f.write("- strongest_edges:\n")
                for edge in cluster.strongest_edges:
                    f.write(
                        f"  - {edge.community_a} x {edge.community_b}: {edge.affinity:.6f} "
                        f"({edge.a_follows_b_count}<->{edge.b_follows_a_count})\n"
                    )
            if cluster.representative_accounts:
                f.write("- representative_accounts:\n")
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

        f.write("\n## Bridge Accounts\n\n")
        f.write(f"- shared_member_bridges: {bridges.member_bridge_account_count}\n")
        f.write(f"- shared_member_cross_cluster: {bridges.cross_cluster_member_bridge_count}\n")
        _write_attention_view_section(f, bridges.all_view)
        _write_attention_view_section(f, bridges.no_nanpa_view)
        _write_attention_view_section(f, bridges.frontier_view)
        _write_attention_view_section(f, bridges.frontier_seed_view)

        f.write("\n### Shared-Member Bridges\n\n")
        if bridges.member_bridges:
            for account in bridges.member_bridges[:10]:
                f.write(_format_member_account(account) + "\n")
        else:
            f.write("- none\n")

        f.write("\n## Expansion Proposals\n\n")
        if expansion_proposals:
            for proposal in expansion_proposals:
                communities = ", ".join(
                    f"{name} (`{cid}`)"
                    for cid, name in zip(proposal.community_ids, proposal.community_names)
                )
                f.write(f"### {proposal.proposal_name}\n\n")
                f.write(f"- proposal_id: `{proposal.proposal_id}`\n")
                f.write(f"- novelty_score: `{proposal.novelty_score:.3f}`\n")
                f.write(f"- new_account_count: `{proposal.new_account_count}`\n")
                f.write(f"- actionable_support_count: `{proposal.actionable_support_count}`\n")
                f.write(f"- generic_hub_count: `{proposal.generic_hub_count}`\n")
                f.write(f"- community_seed_ratio: `{proposal.community_seed_ratio:.1%}`\n")
                f.write(f"- total_follow_edges: `{proposal.total_follow_edges}`\n")
                f.write(f"- avg_bridge_score: `{proposal.avg_bridge_score:.3f}`\n")
                f.write(f"- avg_cluster_count: `{proposal.avg_cluster_count:.3f}`\n")
                f.write(f"- spillover_community_count: `{proposal.spillover_community_count}`\n")
                f.write(f"- communities: {communities}\n")
                f.write("- top_actionable_accounts:\n")
                for account in proposal.top_actionable_accounts[:6]:
                    f.write(_format_attention_account(account) + "\n")
                f.write("\n")
        else:
            f.write("- none\n")

        f.write("\n## Top Influencers\n\n")
        for size in sizes:
            if not size.top_influencers:
                continue
            f.write(f"### {size.community_name}\n\n")
            for influencer in size.top_influencers[:5]:
                f.write(
                    f"- @{influencer['screen_name']} "
                    f"({influencer['followers_count']:,} followers) - {influencer['bio']}\n"
                )
            f.write("\n")

    print(f"[SAVE] {md_path}")

    print("\n" + "=" * 60)
    print("Kaiwai Summary")
    print("=" * 60)
    for size in sizes:
        print(
            f"  {size.community_name}: members={size.member_count:,}, "
            f"reach={size.total_followers_reach:,}"
        )
    if overlaps:
        print("\nTop overlap pairs:")
        for overlap in overlaps[:5]:
            print(
                f"  {overlap.community_a} x {overlap.community_b}: "
                f"Jaccard={overlap.jaccard:.3f} shared={overlap.intersection_count}"
            )
    if clusters.clusters:
        print("\nClusters:")
        for cluster in clusters.clusters:
            print(f"  Cluster {cluster.cluster_id}: {', '.join(cluster.communities)}")
    if clusters.isolated_communities:
        print(f"\nIsolated: {', '.join(clusters.isolated_communities)}")
    print(
        "\nBridge views: "
        f"all={bridges.attention_hub_count}, "
        f"no_nanpa={bridges.no_nanpa_view.attention_hub_count}, "
        f"frontier={bridges.frontier_view.attention_hub_count}, "
        f"seed={bridges.frontier_seed_view.attention_hub_count}"
    )
    if expansion_proposals:
        print(
            " | proposals="
            f"{len(expansion_proposals)} top={expansion_proposals[0].proposal_id}"
        )
