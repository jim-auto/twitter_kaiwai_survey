"""Generate proposal candidates for expanding kaiwai coverage."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Iterable

from analysis.bridge_accounts import AttentionBridgeAccount, detect_bridge_accounts
from analysis.bridge_accounts import BridgeAccountAnalysisResult
from config.settings import DB_PATH


@dataclass
class ExpansionProposal:
    proposal_id: str
    proposal_name: str
    community_ids: tuple[str, ...]
    community_names: tuple[str, ...]
    support_count: int
    new_account_count: int
    actionable_support_count: int
    generic_hub_count: int
    community_seed_ratio: float
    total_follow_edges: int
    avg_bridge_score: float
    avg_cluster_count: float
    spillover_community_count: int
    novelty_score: float
    category_counts: dict[str, int]
    top_accounts: list[AttentionBridgeAccount]
    top_actionable_accounts: list[AttentionBridgeAccount]


def _load_community_names() -> dict[str, str]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("SELECT id, name FROM communities ORDER BY id").fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}


def _short_name(name: str) -> str:
    short = name.replace("界隈", "").replace("・", " ").replace("  ", " ").strip()
    return short or name


def _proposal_id(community_ids: Iterable[str]) -> str:
    return "proposal_" + "_".join(community_ids)


def _proposal_name(community_names: Iterable[str]) -> str:
    return " x ".join(_short_name(name) for name in community_names) + " 候補"


def _proposal_keywords(community_names: list[str]) -> list[str]:
    short_names = [_short_name(name) for name in community_names]
    keywords: list[str] = []
    for left, right in combinations(short_names, 2):
        keywords.append(f"{left} {right}")
    if len(short_names) >= 3:
        keywords.append(" ".join(short_names))
    return keywords[:6]


def _pick_role(account: AttentionBridgeAccount, index: int) -> str:
    if account.account_category in {"media_hub", "official_hub"}:
        return "media"
    if account.account_category == "celebrity_hub":
        return "influencer"
    if account.followers_count >= 100_000 or index < 2:
        return "influencer"
    return "active"


def _score_proposal(
    *,
    support_count: int,
    actionable_support_count: int,
    generic_hub_count: int,
    total_follow_edges: int,
    avg_bridge_score: float,
    avg_cluster_count: float,
    spillover_community_count: int,
) -> float:
    if support_count <= 0:
        return 0.0

    community_seed_ratio = actionable_support_count / support_count
    avg_follow_edges = total_follow_edges / support_count
    bridge_lift = max(avg_cluster_count - 1.0, 0.0)
    return round(
        (support_count * 2.5)
        + (actionable_support_count * 5.0)
        + (avg_follow_edges * 1.1)
        + (avg_bridge_score * 0.4)
        + (bridge_lift * 8.0)
        + (spillover_community_count * 1.75)
        - (generic_hub_count * 3.5)
        - ((1.0 - community_seed_ratio) * 12.0),
        3,
    )


def build_expansion_proposals(
    min_confidence: float = 0.5,
    combo_size: int = 3,
    min_support: int = 6,
    max_proposals: int = 12,
    bridge_analysis: BridgeAccountAnalysisResult | None = None,
) -> list[ExpansionProposal]:
    names = _load_community_names()
    bridges = bridge_analysis or detect_bridge_accounts(min_confidence=min_confidence)
    rows = bridges.frontier_seed_view.attention_hubs

    combo_map: dict[tuple[str, ...], list[AttentionBridgeAccount]] = {}
    for account in rows:
        if len(account.source_community_ids) < combo_size:
            continue
        for combo in combinations(account.source_community_ids, combo_size):
            combo_map.setdefault(combo, []).append(account)

    proposals: list[ExpansionProposal] = []
    for community_ids, accounts in combo_map.items():
        if len(accounts) < min_support:
            continue
        unique_accounts: dict[str, AttentionBridgeAccount] = {row.user_id: row for row in accounts}
        account_rows = sorted(
            unique_accounts.values(),
            key=lambda row: (
                row.account_category == "community_seed",
                row.follow_edge_count,
                row.bridge_score,
                row.followers_count,
                row.screen_name,
            ),
            reverse=True,
        )
        actionable_rows = [
            row for row in account_rows
            if row.account_category == "community_seed"
        ]
        category_counts: dict[str, int] = {}
        for row in account_rows:
            category_counts[row.account_category] = category_counts.get(row.account_category, 0) + 1
        community_names = tuple(names.get(cid, cid) for cid in community_ids)
        support_count = len(account_rows)
        actionable_support_count = len(actionable_rows)
        generic_hub_count = support_count - actionable_support_count
        total_follow_edges = sum(row.follow_edge_count for row in account_rows)
        avg_bridge_score = (
            sum(row.bridge_score for row in account_rows) / support_count
            if account_rows else 0.0
        )
        avg_cluster_count = (
            sum(row.cluster_count for row in account_rows) / support_count
            if account_rows else 0.0
        )
        spillover_community_ids = sorted({
            cid
            for row in account_rows
            for cid in row.source_community_ids
            if cid not in community_ids
        })
        proposals.append(ExpansionProposal(
            proposal_id=_proposal_id(community_ids),
            proposal_name=_proposal_name(community_names),
            community_ids=community_ids,
            community_names=community_names,
            support_count=support_count,
            new_account_count=support_count,
            actionable_support_count=actionable_support_count,
            generic_hub_count=generic_hub_count,
            community_seed_ratio=(
                actionable_support_count / support_count if support_count else 0.0
            ),
            total_follow_edges=total_follow_edges,
            avg_bridge_score=avg_bridge_score,
            avg_cluster_count=avg_cluster_count,
            spillover_community_count=len(spillover_community_ids),
            novelty_score=_score_proposal(
                support_count=support_count,
                actionable_support_count=actionable_support_count,
                generic_hub_count=generic_hub_count,
                total_follow_edges=total_follow_edges,
                avg_bridge_score=avg_bridge_score,
                avg_cluster_count=avg_cluster_count,
                spillover_community_count=len(spillover_community_ids),
            ),
            category_counts=category_counts,
            top_accounts=account_rows[:10],
            top_actionable_accounts=(actionable_rows or account_rows)[:10],
        ))

    proposals.sort(
        key=lambda row: (
            row.novelty_score,
            row.actionable_support_count,
            row.support_count,
            row.total_follow_edges,
            row.avg_bridge_score,
            row.proposal_id,
        ),
        reverse=True,
    )
    return proposals[:max_proposals]


def render_markdown(
    proposals: list[ExpansionProposal],
    *,
    min_confidence: float,
    combo_size: int,
    min_support: int,
) -> str:
    lines: list[str] = []
    lines.append("# Frontier Expansion Proposals")
    lines.append("")
    lines.append(f"- generated_at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- db_path: `{DB_PATH}`")
    lines.append(f"- min_confidence: `{min_confidence}`")
    lines.append(f"- combo_size: `{combo_size}`")
    lines.append(f"- min_support: `{min_support}`")
    lines.append(f"- proposal_count: `{len(proposals)}`")
    lines.append(
        "- novelty_score: heuristic based on new-account count, community-seed ratio, "
        "bridge lift, spillover coverage, and generic-hub penalty"
    )
    lines.append("")

    for index, proposal in enumerate(proposals, start=1):
        lines.append(f"## {index}. {proposal.proposal_name}")
        lines.append("")
        lines.append(f"- proposal_id: `{proposal.proposal_id}`")
        lines.append(f"- novelty_score: `{proposal.novelty_score:.3f}`")
        lines.append(f"- support_count: `{proposal.support_count}`")
        lines.append(f"- new_account_count: `{proposal.new_account_count}`")
        lines.append(f"- actionable_support_count: `{proposal.actionable_support_count}`")
        lines.append(f"- generic_hub_count: `{proposal.generic_hub_count}`")
        lines.append(f"- community_seed_ratio: `{proposal.community_seed_ratio:.1%}`")
        lines.append(f"- total_follow_edges: `{proposal.total_follow_edges}`")
        lines.append(f"- avg_bridge_score: `{proposal.avg_bridge_score:.3f}`")
        lines.append(f"- avg_cluster_count: `{proposal.avg_cluster_count:.3f}`")
        lines.append(f"- spillover_community_count: `{proposal.spillover_community_count}`")
        lines.append(
            "- category_counts: " + ", ".join(
                f"{category}={count}" for category, count in sorted(proposal.category_counts.items())
            )
        )
        lines.append(
            "- communities: " + ", ".join(
                f"{name} (`{cid}`)"
                for cid, name in zip(proposal.community_ids, proposal.community_names)
            )
        )
        lines.append("- top_actionable_accounts:")
        for account in proposal.top_actionable_accounts[:8]:
            label = f"@{account.screen_name}" if account.screen_name else account.user_id
            lines.append(
                f"  - {label}: edges={account.follow_edge_count}, "
                f"score={account.bridge_score:.3f}, followers={account.followers_count:,}, "
                f"type={account.account_category}"
            )
        lines.append("- top_accounts:")
        for account in proposal.top_accounts[:8]:
            label = f"@{account.screen_name}" if account.screen_name else account.user_id
            lines.append(
                f"  - {label}: edges={account.follow_edge_count}, "
                f"score={account.bridge_score:.3f}, followers={account.followers_count:,}, "
                f"type={account.account_category}"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def write_yaml_stubs(proposals: list[ExpansionProposal], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for proposal in proposals:
        keywords = _proposal_keywords(list(proposal.community_names))
        lines: list[str] = []
        lines.append(f"id: {proposal.proposal_id}")
        lines.append(f'name: "{proposal.proposal_name}"')
        lines.append(
            'description: "frontier seed analysis から抽出した新規界隈候補。要手動調整。"'
        )
        lines.append("")
        lines.append(f"# support_count: {proposal.support_count}")
        lines.append(f"# novelty_score: {proposal.novelty_score:.3f}")
        lines.append(f"# new_account_count: {proposal.new_account_count}")
        lines.append(f"# actionable_support_count: {proposal.actionable_support_count}")
        lines.append(f"# generic_hub_count: {proposal.generic_hub_count}")
        lines.append(f"# community_seed_ratio: {proposal.community_seed_ratio:.3f}")
        lines.append(f"# total_follow_edges: {proposal.total_follow_edges}")
        lines.append(f"# avg_bridge_score: {proposal.avg_bridge_score:.3f}")
        lines.append(f"# avg_cluster_count: {proposal.avg_cluster_count:.3f}")
        lines.append(f"# spillover_community_count: {proposal.spillover_community_count}")
        lines.append("# source_communities: " + ", ".join(proposal.community_ids))
        lines.append("")
        lines.append("seeds:")
        for index, account in enumerate(proposal.top_actionable_accounts[:8]):
            label = account.screen_name or account.user_id
            role = _pick_role(account, index)
            lines.append(f"  - username: {label}")
            lines.append(f"    role: {role}")
        lines.append("")
        lines.append("hashtags: []")
        lines.append("")
        lines.append("keywords:")
        for keyword in keywords:
            lines.append(f'  - "{keyword}"')
        lines.append("")
        lines.append("bio_patterns: []")
        lines.append("")
        lines.append("exclude_patterns:")
        lines.append('  - "(公式|official|news|press|media)"')
        lines.append("")
        lines.append("expansion:")
        lines.append("  max_depth: 2")
        lines.append("  min_shared_follows: 3")
        lines.append("  max_members: 5000")
        lines.append("")

        path = out_dir / f"{proposal.proposal_id}.yaml"
        path.write_text("\n".join(lines), encoding="utf-8")
        written.append(path)
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate frontier expansion proposals.")
    parser.add_argument("--confidence", type=float, default=0.5)
    parser.add_argument("--combo-size", type=int, default=3)
    parser.add_argument("--min-support", type=int, default=6)
    parser.add_argument("--max-proposals", type=int, default=12)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/exports/frontier_expansion_2026-03-27.md"),
    )
    parser.add_argument(
        "--proposal-dir",
        type=Path,
        default=Path("communities/proposals"),
    )
    parser.add_argument("--write-yaml", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    proposals = build_expansion_proposals(
        min_confidence=args.confidence,
        combo_size=args.combo_size,
        min_support=args.min_support,
        max_proposals=args.max_proposals,
    )
    report = render_markdown(
        proposals,
        min_confidence=args.confidence,
        combo_size=args.combo_size,
        min_support=args.min_support,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote {args.output}")

    if args.write_yaml:
        written = write_yaml_stubs(proposals, args.proposal_dir)
        print(f"Wrote {len(written)} proposal stubs to {args.proposal_dir}")
        for path in written[:10]:
            print(f"  - {path}")


if __name__ == "__main__":
    main()
