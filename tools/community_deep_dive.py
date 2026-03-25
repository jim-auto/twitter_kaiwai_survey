"""Additional structural analysis for community graph data."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.clustering import detect_affinity_clusters
from config.settings import DB_PATH, EXPORT_DIR


def load_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {row[0]: row[1] for row in conn.execute("SELECT id, name FROM communities")}


def load_members(
    conn: sqlite3.Connection,
    min_confidence: float,
) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    member_sets: dict[str, set[str]] = defaultdict(set)
    user_to_cids: dict[str, list[str]] = defaultdict(list)
    rows = conn.execute(
        "SELECT community_id, user_id FROM community_members WHERE confidence >= ?",
        (min_confidence,),
    ).fetchall()
    for cid, uid in rows:
        member_sets[cid].add(uid)
        user_to_cids[uid].append(cid)
    return member_sets, user_to_cids


def load_user_map(
    conn: sqlite3.Connection,
    user_ids: set[str],
) -> dict[str, dict[str, object]]:
    if not user_ids:
        return {}

    qmarks = ",".join("?" for _ in user_ids)
    rows = conn.execute(
        f"""
        SELECT user_id,
               screen_name,
               display_name,
               COALESCE(followers_count, 0) AS followers_count,
               COALESCE(following_count, 0) AS following_count,
               COALESCE(tweet_count, 0) AS tweet_count,
               last_scraped
        FROM users
        WHERE user_id IN ({qmarks})
        """,
        tuple(user_ids),
    ).fetchall()
    return {
        row[0]: {
            "user_id": row[0],
            "screen_name": row[1],
            "display_name": row[2],
            "followers_count": row[3],
            "following_count": row[4],
            "tweet_count": row[5],
            "last_scraped": row[6],
        }
        for row in rows
    }


def user_record(users: dict[str, dict[str, object]], user_id: str) -> dict[str, object]:
    return users.get(user_id, {
        "user_id": user_id,
        "screen_name": None,
        "display_name": None,
        "followers_count": 0,
        "following_count": 0,
        "tweet_count": 0,
        "last_scraped": None,
    })


def compute_overlaps(
    member_sets: dict[str, set[str]],
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for cid_a, cid_b in combinations(sorted(member_sets), 2):
        set_a = member_sets[cid_a]
        set_b = member_sets[cid_b]
        intersection_count = len(set_a & set_b)
        if not intersection_count:
            continue
        union_count = len(set_a | set_b)
        results.append({
            "community_a": cid_a,
            "community_b": cid_b,
            "intersection_count": intersection_count,
            "jaccard": intersection_count / union_count if union_count else 0.0,
            "containment_a": intersection_count / len(set_a) if set_a else 0.0,
            "containment_b": intersection_count / len(set_b) if set_b else 0.0,
        })
    results.sort(
        key=lambda row: (row["jaccard"], row["intersection_count"]),
        reverse=True,
    )
    return results


def compute_cross_metrics(
    conn: sqlite3.Connection,
    member_sets: dict[str, set[str]],
    user_to_cids: dict[str, list[str]],
    users: dict[str, dict[str, object]],
) -> dict[str, object]:
    internal_edges: Counter[str] = Counter()
    cross_out: Counter[str] = Counter()
    cross_in: Counter[str] = Counter()
    out_targets: dict[str, set[str]] = defaultdict(set)
    in_sources: dict[str, set[str]] = defaultdict(set)
    pair_dir: Counter[tuple[str, str]] = Counter()
    bridge_target_users: Counter[tuple[str, str]] = Counter()

    for src, tgt in conn.execute("SELECT source_user_id, target_user_id FROM follow_edges"):
        src_cids = user_to_cids.get(src)
        tgt_cids = user_to_cids.get(tgt)
        if not src_cids or not tgt_cids:
            continue
        for src_cid in src_cids:
            for tgt_cid in tgt_cids:
                if src_cid == tgt_cid:
                    internal_edges[src_cid] += 1
                    continue
                cross_out[src_cid] += 1
                cross_in[tgt_cid] += 1
                out_targets[src_cid].add(tgt_cid)
                in_sources[tgt_cid].add(src_cid)
                pair_dir[(src_cid, tgt_cid)] += 1
                bridge_target_users[(src_cid, tgt)] += 1

    community_rows: list[dict[str, object]] = []
    for cid, members in member_sets.items():
        follower_counts = sorted(
            int(user_record(users, uid)["followers_count"]) for uid in members
        )
        follower_counts.reverse()
        total_reach = sum(follower_counts)
        member_count = len(members)
        possible_internal = member_count * (member_count - 1)
        total_known_focus = internal_edges[cid] + cross_out[cid]
        community_rows.append({
            "cid": cid,
            "member_count": member_count,
            "reach": total_reach,
            "top1_share": (
                follower_counts[0] / total_reach if total_reach and follower_counts else 0.0
            ),
            "top5_share": (
                sum(follower_counts[:5]) / total_reach if total_reach and follower_counts else 0.0
            ),
            "internal_edges": internal_edges[cid],
            "cross_out": cross_out[cid],
            "cross_in": cross_in[cid],
            "cross_out_per_member": cross_out[cid] / member_count if member_count else 0.0,
            "cross_in_per_member": cross_in[cid] / member_count if member_count else 0.0,
            "internal_density": (
                internal_edges[cid] / possible_internal if possible_internal else 0.0
            ),
            "outward_ratio": (
                cross_out[cid] / total_known_focus if total_known_focus else 0.0
            ),
            "out_targets": len(out_targets[cid]),
            "in_sources": len(in_sources[cid]),
        })

    bridges: list[dict[str, object]] = []
    for uid, cids in user_to_cids.items():
        if len(cids) < 2:
            continue
        record = user_record(users, uid)
        bridges.append({
            "screen_name": record["screen_name"] or uid,
            "followers_count": int(record["followers_count"]),
            "communities": sorted(cids),
        })
    bridges.sort(
        key=lambda row: (len(row["communities"]), row["followers_count"]),
        reverse=True,
    )

    bridge_targets: list[dict[str, object]] = []
    for (src_cid, tgt_uid), count in bridge_target_users.items():
        record = user_record(users, tgt_uid)
        bridge_targets.append({
            "source_cid": src_cid,
            "target_screen_name": record["screen_name"] or tgt_uid,
            "target_followers": int(record["followers_count"]),
            "target_communities": sorted(user_to_cids.get(tgt_uid, [])),
            "count": count,
        })
    bridge_targets.sort(
        key=lambda row: (row["count"], row["target_followers"]),
        reverse=True,
    )

    asymmetry_rows: list[dict[str, object]] = []
    communities = sorted(member_sets)
    for index, cid_a in enumerate(communities):
        for cid_b in communities[index + 1:]:
            a_to_b = pair_dir[(cid_a, cid_b)]
            b_to_a = pair_dir[(cid_b, cid_a)]
            total = a_to_b + b_to_a
            if total == 0:
                continue
            asymmetry_rows.append({
                "community_a": cid_a,
                "community_b": cid_b,
                "a_to_b": a_to_b,
                "b_to_a": b_to_a,
                "total": total,
                "skew": abs(a_to_b - b_to_a) / total,
                "dominant": (
                    f"{cid_a}->{cid_b}" if a_to_b > b_to_a
                    else f"{cid_b}->{cid_a}" if b_to_a > a_to_b
                    else "balanced"
                ),
            })
    asymmetry_rows.sort(
        key=lambda row: (row["skew"], row["total"]),
        reverse=True,
    )

    return {
        "community_rows": community_rows,
        "bridges": bridges,
        "bridge_targets": bridge_targets,
        "asymmetry_rows": asymmetry_rows,
    }


def compute_missing_profiles(
    member_sets: dict[str, set[str]],
    users: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for cid, members in member_sets.items():
        unresolved = sum(1 for uid in members if not user_record(users, uid)["last_scraped"])
        if not unresolved:
            continue
        rows.append({
            "cid": cid,
            "member_count": len(members),
            "missing_count": unresolved,
            "missing_ratio": unresolved / len(members) if members else 0.0,
        })
    rows.sort(
        key=lambda row: (row["missing_count"], row["missing_ratio"]),
        reverse=True,
    )
    return rows


def format_named_line(names: dict[str, str], cid: str, suffix: str) -> str:
    return f"- {names.get(cid, cid)} (`{cid}`): {suffix}"


def build_report(
    conn: sqlite3.Connection,
    high_confidence: float,
    low_confidence: float,
) -> str:
    names = load_names(conn)

    high_members, high_user_to_cids = load_members(conn, high_confidence)
    low_members, _ = load_members(conn, low_confidence)
    member_user_ids = {uid for member_ids in high_members.values() for uid in member_ids}
    users = load_user_map(conn, member_user_ids)

    high_overlaps = compute_overlaps(high_members)
    low_overlaps = compute_overlaps(low_members)
    cross_metrics = compute_cross_metrics(conn, high_members, high_user_to_cids, users)
    missing_profiles = compute_missing_profiles(high_members, users)
    total_missing_profiles = sum(row["missing_count"] for row in missing_profiles)
    clusters = detect_affinity_clusters(min_confidence=high_confidence)

    community_rows = cross_metrics["community_rows"]
    bridges = cross_metrics["bridges"]
    bridge_targets = cross_metrics["bridge_targets"]
    asymmetry_rows = cross_metrics["asymmetry_rows"]

    lines: list[str] = []
    lines.append("# Additional Kaiwai Deep Dive")
    lines.append("")
    lines.append(f"- Generated at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- DB path: `{DB_PATH}`")
    lines.append(f"- High-confidence threshold: `{high_confidence}`")
    lines.append(f"- Exploratory threshold: `{low_confidence}`")
    lines.append("")

    community_count = conn.execute("SELECT COUNT(*) FROM communities").fetchone()[0]
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_edges = conn.execute("SELECT COUNT(*) FROM follow_edges").fetchone()[0]
    high_members_total = sum(len(member_ids) for member_ids in high_members.values())
    low_members_total = sum(len(member_ids) for member_ids in low_members.values())
    lines.append("## Snapshot")
    lines.append("")
    lines.append(f"- Communities: `{community_count}`")
    lines.append(f"- Users: `{total_users:,}`")
    lines.append(f"- Follow edges: `{total_edges:,}`")
    lines.append(f"- Members at conf>={high_confidence}: `{high_members_total:,}`")
    lines.append(f"- Members at conf>={low_confidence}: `{low_members_total:,}`")
    lines.append(f"- Missing user profiles inside conf>={high_confidence}: `{total_missing_profiles}`")
    lines.append("")

    lines.append("## High-Confidence Signals")
    lines.append("")
    lines.append(f"- Non-zero member overlap pairs at conf>={high_confidence}: `{len(high_overlaps)}`")
    if high_overlaps:
        top_overlap = high_overlaps[0]
        lines.append(
            f"- Strongest hard overlap: {names[top_overlap['community_a']]} (`{top_overlap['community_a']}`) × "
            f"{names[top_overlap['community_b']]} (`{top_overlap['community_b']}`) "
            f"with `{top_overlap['intersection_count']}` shared members and Jaccard `{top_overlap['jaccard']:.4f}`"
        )
    lines.append("")

    lines.append("### Hubs By Cross-Attention")
    for row in sorted(
        community_rows,
        key=lambda item: (item["cross_out"] + item["cross_in"], item["in_sources"]),
        reverse=True,
    )[:8]:
        lines.append(format_named_line(
            names,
            str(row["cid"]),
            "cross_out={cross_out}, cross_in={cross_in}, out_targets={out_targets}, in_sources={in_sources}".format(**row),
        ))
    lines.append("")

    lines.append("### Audience Exporters")
    for row in sorted(community_rows, key=lambda item: item["outward_ratio"], reverse=True)[:6]:
        lines.append(format_named_line(
            names,
            str(row["cid"]),
            "outward_ratio={:.3f}, cross_out/member={:.3f}, internal_density={:.4f}".format(
                row["outward_ratio"],
                row["cross_out_per_member"],
                row["internal_density"],
            ),
        ))
    lines.append("")

    lines.append("### Attention Magnets")
    for row in sorted(community_rows, key=lambda item: item["cross_in_per_member"], reverse=True)[:6]:
        lines.append(format_named_line(
            names,
            str(row["cid"]),
            "cross_in/member={:.3f}, cross_in={}, in_sources={}".format(
                row["cross_in_per_member"],
                row["cross_in"],
                row["in_sources"],
            ),
        ))
    lines.append("")

    lines.append("### Internal Cohesion")
    for row in sorted(community_rows, key=lambda item: item["internal_density"], reverse=True)[:6]:
        lines.append(format_named_line(
            names,
            str(row["cid"]),
            "internal_density={:.4f}, internal_edges={}, members={}".format(
                row["internal_density"],
                row["internal_edges"],
                row["member_count"],
            ),
        ))
    lines.append("")

    lines.append("### Reach Concentration")
    lines.append("- Most concentrated by top 5 share:")
    for row in sorted(community_rows, key=lambda item: item["top5_share"], reverse=True)[:5]:
        lines.append(format_named_line(
            names,
            str(row["cid"]),
            "top1_share={:.3f}, top5_share={:.3f}, reach={:,}".format(
                row["top1_share"],
                row["top5_share"],
                row["reach"],
            ),
        ))
    lines.append("- Least concentrated by top 5 share:")
    for row in sorted(community_rows, key=lambda item: item["top5_share"])[:5]:
        lines.append(format_named_line(
            names,
            str(row["cid"]),
            "top5_share={:.3f}, reach={:,}".format(
                row["top5_share"],
                row["reach"],
            ),
        ))
    lines.append("")

    lines.append("### Bridge Users")
    if bridges:
        for row in bridges[:10]:
            community_names = ", ".join(f"{names[cid]} (`{cid}`)" for cid in row["communities"])
            lines.append(
                f"- @{row['screen_name']} ({row['followers_count']:,} followers): {community_names}"
            )
    else:
        lines.append("- None at this threshold")
    lines.append("")

    lines.append("### One-Way Attention Pairs")
    for row in [item for item in asymmetry_rows if item["total"] >= 2][:10]:
        lines.append(
            "- {} (`{}`) × {} (`{}`): total={}, {} dominates ({} vs {}, skew={:.3f})".format(
                names[row["community_a"]],
                row["community_a"],
                names[row["community_b"]],
                row["community_b"],
                row["total"],
                row["dominant"],
                row["a_to_b"],
                row["b_to_a"],
                row["skew"],
            )
        )
    lines.append("")

    lines.append("### Example Bridge Targets")
    for row in bridge_targets[:10]:
        target_communities = ", ".join(
            f"{names[cid]} (`{cid}`)" for cid in row["target_communities"]
        )
        lines.append(
            "- {} (`{}`) follows @{} {} times -> {} | target communities: {}".format(
                names[row["source_cid"]],
                row["source_cid"],
                row["target_screen_name"],
                row["count"],
                f"{row['target_followers']:,} followers",
                target_communities or "-",
            )
        )
    lines.append("")

    lines.append("### Affinity Clusters")
    lines.append(
        f"- method=`{clusters.method}`, min_edge_weight=`{clusters.min_edge_weight:.6f}`, "
        f"max_affinity=`{clusters.max_affinity:.6f}`"
    )
    for cluster in clusters.clusters:
        cluster_members = ", ".join(f"{names[cid]} (`{cid}`)" for cid in cluster.communities)
        lines.append(f"- Cluster {cluster.cluster_id}: {cluster_members}")
        if cluster.strongest_edges:
            edge_bits = []
            for edge in cluster.strongest_edges[:3]:
                edge_bits.append(
                    f"{edge.community_a}×{edge.community_b}={edge.affinity:.6f}"
                )
            lines.append(f"-   strongest: {', '.join(edge_bits)}")
        if cluster.representative_accounts:
            rep_bits = []
            for account in cluster.representative_accounts[:4]:
                communities = "/".join(account.community_ids)
                rep_bits.append(
                    f"@{account.screen_name} ({account.followers_count:,} FL, {communities})"
                )
            lines.append(f"-   reps: {', '.join(rep_bits)}")
    if clusters.isolated_communities:
        isolated_names = ", ".join(
            f"{names[cid]} (`{cid}`)" for cid in clusters.isolated_communities
        )
        lines.append(f"- Isolated: {isolated_names}")
    lines.append("")

    lines.append("## Threshold Sensitivity")
    lines.append("")
    lines.append(
        f"- Non-zero overlap pairs jump from `{len(high_overlaps)}` at conf>={high_confidence} "
        f"to `{len(low_overlaps)}` at conf>={low_confidence}"
    )
    for row in low_overlaps[:10]:
        lines.append(
            "- {} (`{}`) × {} (`{}`): shared={}, jaccard={:.4f}, containment=({:.3f}, {:.3f})".format(
                names[row["community_a"]],
                row["community_a"],
                names[row["community_b"]],
                row["community_b"],
                row["intersection_count"],
                row["jaccard"],
                row["containment_a"],
                row["containment_b"],
            )
        )
    lines.append("")

    lines.append("## Data Caveats")
    lines.append("")
    for row in missing_profiles[:10]:
        lines.append(format_named_line(
            names,
            str(row["cid"]),
            "missing_profiles={}/{} ({:.1%})".format(
                row["missing_count"],
                row["member_count"],
                row["missing_ratio"],
            ),
        ))

    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Additional structural analysis for community graph data.")
    parser.add_argument(
        "--high-confidence",
        type=float,
        default=0.5,
        help="Threshold for primary analysis.",
    )
    parser.add_argument(
        "--low-confidence",
        type=float,
        default=0.3,
        help="Threshold for exploratory overlap analysis.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional markdown output path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = sqlite3.connect(str(DB_PATH))
    report = build_report(conn, args.high_confidence, args.low_confidence)
    conn.close()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
