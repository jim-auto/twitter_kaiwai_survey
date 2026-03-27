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
from analysis.clustering import detect_affinity_clusters
from config.settings import DB_PATH


@dataclass
class ExpansionProposal:
    proposal_id: str
    proposal_name: str
    community_ids: tuple[str, ...]
    community_names: tuple[str, ...]
    support_count: int
    total_follow_edges: int
    avg_bridge_score: float
    top_accounts: list[AttentionBridgeAccount]


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
    screen_name = (account.screen_name or "").lower()
    if any(token in screen_name for token in ("news", "press", "official", "media")):
        return "media"
    if account.followers_count >= 100_000 or index < 2:
        return "influencer"
    return "active"


def build_expansion_proposals(
    min_confidence: float = 0.5,
    combo_size: int = 3,
    min_support: int = 6,
    max_proposals: int = 12,
) -> list[ExpansionProposal]:
    names = _load_community_names()
    clusters = detect_affinity_clusters(min_confidence=min_confidence)
    bridges = detect_bridge_accounts(
        min_confidence=min_confidence,
        cluster_analysis=clusters,
    )
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
                row.follow_edge_count,
                row.bridge_score,
                row.followers_count,
                row.screen_name,
            ),
            reverse=True,
        )
        community_names = tuple(names.get(cid, cid) for cid in community_ids)
        proposals.append(ExpansionProposal(
            proposal_id=_proposal_id(community_ids),
            proposal_name=_proposal_name(community_names),
            community_ids=community_ids,
            community_names=community_names,
            support_count=len(account_rows),
            total_follow_edges=sum(row.follow_edge_count for row in account_rows),
            avg_bridge_score=(
                sum(row.bridge_score for row in account_rows) / len(account_rows)
                if account_rows else 0.0
            ),
            top_accounts=account_rows[:10],
        ))

    proposals.sort(
        key=lambda row: (
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
    lines.append("")

    for index, proposal in enumerate(proposals, start=1):
        lines.append(f"## {index}. {proposal.proposal_name}")
        lines.append("")
        lines.append(f"- proposal_id: `{proposal.proposal_id}`")
        lines.append(f"- support_count: `{proposal.support_count}`")
        lines.append(f"- total_follow_edges: `{proposal.total_follow_edges}`")
        lines.append(f"- avg_bridge_score: `{proposal.avg_bridge_score:.3f}`")
        lines.append(
            "- communities: " + ", ".join(
                f"{name} (`{cid}`)"
                for cid, name in zip(proposal.community_ids, proposal.community_names)
            )
        )
        lines.append("- top_accounts:")
        for account in proposal.top_accounts[:8]:
            label = f"@{account.screen_name}" if account.screen_name else account.user_id
            lines.append(
                f"  - {label}: edges={account.follow_edge_count}, "
                f"score={account.bridge_score:.3f}, followers={account.followers_count:,}"
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
        lines.append(f"# total_follow_edges: {proposal.total_follow_edges}")
        lines.append(f"# avg_bridge_score: {proposal.avg_bridge_score:.3f}")
        lines.append("# source_communities: " + ", ".join(proposal.community_ids))
        lines.append("")
        lines.append("seeds:")
        for index, account in enumerate(proposal.top_accounts[:8]):
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
