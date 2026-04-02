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
from communities import load_all_communities
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
    base_novelty_score: float
    second_order_penalty: float
    family_penalty: float
    redundancy_penalty: float
    novelty_score: float
    included_composite_community_ids: tuple[str, ...]
    parent_overlap_community_ids: tuple[str, ...]
    family_overlap_pair_keys: tuple[str, ...]
    category_counts: dict[str, int]
    top_accounts: list[AttentionBridgeAccount]
    top_actionable_accounts: list[AttentionBridgeAccount]


@dataclass
class FamilyPairAuditEntry:
    pair_key: str
    community_ids: tuple[str, ...]
    community_names: tuple[str, ...]
    composite_community_ids: tuple[str, ...]
    composite_community_names: tuple[str, ...]
    coverage_count: int


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


def _family_pair_key(community_ids: Iterable[str]) -> str:
    return "+".join(sorted(community_ids))


def load_composite_community_ids() -> set[str]:
    composite_ids: set[str] = set()
    for community in load_all_communities():
        if len(community.hashtags) >= 5 or len(community.keywords) >= 4:
            composite_ids.add(community.id)
    return composite_ids


def _load_member_sets(min_confidence: float) -> dict[str, set[str]]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT community_id, user_id FROM community_members WHERE confidence >= ?",
        (min_confidence,),
    ).fetchall()
    conn.close()
    member_sets: dict[str, set[str]] = {}
    for community_id, user_id in rows:
        member_sets.setdefault(community_id, set()).add(user_id)
    return member_sets


def load_composite_parent_map(
    composite_community_ids: set[str] | None = None,
    min_confidence: float = 0.5,
) -> dict[str, tuple[str, ...]]:
    composite_ids = composite_community_ids or load_composite_community_ids()
    community_ids = {community.id for community in load_all_communities()}
    member_sets = _load_member_sets(min_confidence)
    parent_map: dict[str, tuple[str, ...]] = {}
    for composite_id in sorted(composite_ids):
        lexical_parents = {
            candidate_id
            for candidate_id in community_ids
            if candidate_id != composite_id
            and len(candidate_id) < len(composite_id)
            and (
                composite_id.startswith(f"{candidate_id}_")
                or composite_id.endswith(f"_{candidate_id}")
                or f"_{candidate_id}_" in composite_id
            )
        }
        overlap_parents: list[tuple[float, int, str]] = []
        composite_members = member_sets.get(composite_id, set())
        composite_size = len(composite_members)
        if composite_size:
            for candidate_id, candidate_members in member_sets.items():
                if (
                    candidate_id == composite_id
                    or candidate_id in composite_ids
                    or not candidate_members
                ):
                    continue
                shared_members = len(composite_members & candidate_members)
                if shared_members < 2:
                    continue
                containment = shared_members / composite_size
                if containment < 0.08:
                    continue
                overlap_parents.append((containment, shared_members, candidate_id))
        overlap_parents.sort(reverse=True)
        inferred_overlap_parents = {
            candidate_id
            for _, _, candidate_id in overlap_parents[:4]
        }
        parents = sorted(lexical_parents | inferred_overlap_parents)
        parent_map[composite_id] = tuple(parents)
    return parent_map


def load_family_pair_map(
    composite_community_ids: set[str] | None = None,
    composite_parent_map: dict[str, tuple[str, ...]] | None = None,
    min_confidence: float = 0.5,
) -> dict[str, tuple[str, ...]]:
    composite_ids = composite_community_ids or load_composite_community_ids()
    parent_map = composite_parent_map or load_composite_parent_map(
        composite_ids,
        min_confidence=min_confidence,
    )
    pair_map: dict[str, set[str]] = {}
    for composite_id in sorted(composite_ids):
        parents = tuple(sorted(set(parent_map.get(composite_id, ()))))
        if len(parents) < 2:
            continue
        for pair in combinations(parents, 2):
            pair_map.setdefault(_family_pair_key(pair), set()).add(composite_id)
    return {
        pair_key: tuple(sorted(composite_ids_for_pair))
        for pair_key, composite_ids_for_pair in sorted(pair_map.items())
    }


def build_family_pair_audit(
    *,
    composite_community_ids: set[str] | None = None,
    composite_parent_map: dict[str, tuple[str, ...]] | None = None,
    family_pair_map: dict[str, tuple[str, ...]] | None = None,
    min_confidence: float = 0.5,
    names: dict[str, str] | None = None,
) -> list[FamilyPairAuditEntry]:
    names = names or _load_community_names()
    pair_map = family_pair_map or load_family_pair_map(
        composite_community_ids=composite_community_ids,
        composite_parent_map=composite_parent_map,
        min_confidence=min_confidence,
    )
    rows: list[FamilyPairAuditEntry] = []
    for pair_key, composite_ids_for_pair in pair_map.items():
        community_ids = tuple(pair_key.split("+"))
        rows.append(FamilyPairAuditEntry(
            pair_key=pair_key,
            community_ids=community_ids,
            community_names=tuple(names.get(cid, cid) for cid in community_ids),
            composite_community_ids=composite_ids_for_pair,
            composite_community_names=tuple(
                names.get(cid, cid) for cid in composite_ids_for_pair
            ),
            coverage_count=len(composite_ids_for_pair),
        ))
    rows.sort(
        key=lambda row: (row.coverage_count, row.pair_key),
        reverse=True,
    )
    return rows


def _pick_role(account: AttentionBridgeAccount, index: int) -> str:
    if account.account_category in {"media_hub", "official_hub", "placeholder_hub"}:
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


def _score_direct_redundancy_penalty(
    *,
    base_novelty_score: float,
    composite_count: int,
) -> float:
    if composite_count <= 0 or base_novelty_score <= 0:
        return 0.0

    penalty = (base_novelty_score * 0.45 * composite_count) + (12.0 * max(0, composite_count - 1))
    return round(min(base_novelty_score, penalty), 3)


def _score_second_order_penalty(
    *,
    base_novelty_score: float,
    parent_overlap_count: int,
) -> float:
    if parent_overlap_count <= 0 or base_novelty_score <= 0:
        return 0.0

    penalty = (base_novelty_score * 0.18 * parent_overlap_count) + (8.0 * parent_overlap_count)
    return round(min(base_novelty_score, penalty), 3)


def _score_family_penalty(
    *,
    base_novelty_score: float,
    family_overlap_pair_keys: tuple[str, ...],
    family_pair_map: dict[str, tuple[str, ...]],
) -> float:
    if not family_overlap_pair_keys or base_novelty_score <= 0:
        return 0.0

    coverage_count = sum(
        len(family_pair_map.get(pair_key, ()))
        for pair_key in family_overlap_pair_keys
    )
    penalty = (base_novelty_score * 0.14 * len(family_overlap_pair_keys)) + (6.0 * coverage_count)
    return round(min(base_novelty_score, penalty), 3)


def build_expansion_proposals(
    min_confidence: float = 0.5,
    combo_size: int = 3,
    min_support: int = 6,
    max_proposals: int = 12,
    bridge_analysis: BridgeAccountAnalysisResult | None = None,
    exclude_composite_communities: bool = False,
    composite_community_ids: set[str] | None = None,
    composite_parent_map: dict[str, tuple[str, ...]] | None = None,
    family_pair_map: dict[str, tuple[str, ...]] | None = None,
) -> list[ExpansionProposal]:
    names = _load_community_names()
    composite_community_ids = composite_community_ids or load_composite_community_ids()
    composite_parent_map = composite_parent_map or load_composite_parent_map(
        composite_community_ids,
        min_confidence=min_confidence,
    )
    family_pair_map = family_pair_map or load_family_pair_map(
        composite_community_ids=composite_community_ids,
        composite_parent_map=composite_parent_map,
        min_confidence=min_confidence,
    )
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
        included_composite_community_ids = tuple(
            cid for cid in community_ids
            if cid in composite_community_ids
        )
        if exclude_composite_communities and included_composite_community_ids:
            continue
        parent_overlap_community_ids = tuple(sorted({
            parent_id
            for composite_id in included_composite_community_ids
            for parent_id in composite_parent_map.get(composite_id, ())
            if parent_id in community_ids
        }))
        family_overlap_pair_keys = tuple(sorted({
            _family_pair_key(pair)
            for pair in combinations(sorted(community_ids), 2)
            if _family_pair_key(pair) in family_pair_map
        }))
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
        base_novelty_score = _score_proposal(
            support_count=support_count,
            actionable_support_count=actionable_support_count,
            generic_hub_count=generic_hub_count,
            total_follow_edges=total_follow_edges,
            avg_bridge_score=avg_bridge_score,
            avg_cluster_count=avg_cluster_count,
            spillover_community_count=len(spillover_community_ids),
        )
        direct_redundancy_penalty = _score_direct_redundancy_penalty(
            base_novelty_score=base_novelty_score,
            composite_count=len(included_composite_community_ids),
        )
        second_order_penalty = _score_second_order_penalty(
            base_novelty_score=base_novelty_score,
            parent_overlap_count=len(parent_overlap_community_ids),
        )
        family_penalty = _score_family_penalty(
            base_novelty_score=base_novelty_score,
            family_overlap_pair_keys=family_overlap_pair_keys,
            family_pair_map=family_pair_map,
        )
        redundancy_penalty = round(min(
            base_novelty_score,
            direct_redundancy_penalty + second_order_penalty + family_penalty,
        ), 3)
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
            base_novelty_score=base_novelty_score,
            second_order_penalty=second_order_penalty,
            family_penalty=family_penalty,
            redundancy_penalty=redundancy_penalty,
            novelty_score=round(max(base_novelty_score - redundancy_penalty, 0.0), 3),
            included_composite_community_ids=included_composite_community_ids,
            parent_overlap_community_ids=parent_overlap_community_ids,
            family_overlap_pair_keys=family_overlap_pair_keys,
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
    explore_proposals: list[ExpansionProposal],
    *,
    min_confidence: float,
    combo_size: int,
    min_support: int,
    composite_community_ids: set[str] | None = None,
    family_pair_audit: list[FamilyPairAuditEntry] | None = None,
) -> str:
    composite_community_ids = composite_community_ids or load_composite_community_ids()
    family_pair_audit = family_pair_audit or build_family_pair_audit(
        composite_community_ids=composite_community_ids,
        min_confidence=min_confidence,
    )
    lines: list[str] = []
    lines.append("# Frontier Expansion Proposals")
    lines.append("")
    lines.append(f"- generated_at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- db_path: `{DB_PATH}`")
    lines.append(f"- min_confidence: `{min_confidence}`")
    lines.append(f"- combo_size: `{combo_size}`")
    lines.append(f"- min_support: `{min_support}`")
    lines.append(f"- proposal_count: `{len(proposals)}`")
    lines.append(f"- explore_proposal_count: `{len(explore_proposals)}`")
    lines.append(f"- composite_communities: `{', '.join(sorted(composite_community_ids)) or '-'}`")
    lines.append(f"- family_pair_count: `{len(family_pair_audit)}`")
    lines.append(
        "- novelty_score: adjusted score after composite, parent-overlap, and family redundancy penalties"
    )
    lines.append("")

    lines.append("## Family Audit")
    lines.append("")
    if family_pair_audit:
        for row in family_pair_audit[:12]:
            lines.append(
                f"- {' x '.join(row.community_names)}: "
                f"coverage={row.coverage_count}, "
                f"hybrids={', '.join(row.composite_community_ids)}"
            )
    else:
        lines.append("- none")
    lines.append("")

    def append_section(title: str, rows: list[ExpansionProposal]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not rows:
            lines.append("- none")
            lines.append("")
            return

        for index, proposal in enumerate(rows, start=1):
            lines.append(f"### {index}. {proposal.proposal_name}")
            lines.append("")
            lines.append(f"- proposal_id: `{proposal.proposal_id}`")
            lines.append(f"- novelty_score: `{proposal.novelty_score:.3f}`")
            lines.append(f"- base_novelty_score: `{proposal.base_novelty_score:.3f}`")
            lines.append(f"- second_order_penalty: `{proposal.second_order_penalty:.3f}`")
            lines.append(f"- family_penalty: `{proposal.family_penalty:.3f}`")
            lines.append(f"- redundancy_penalty: `{proposal.redundancy_penalty:.3f}`")
            lines.append(
                f"- included_composite_communities: "
                f"`{', '.join(proposal.included_composite_community_ids) or '-'}`"
            )
            lines.append(
                f"- parent_overlap_communities: "
                f"`{', '.join(proposal.parent_overlap_community_ids) or '-'}`"
            )
            lines.append(
                f"- family_overlap_pairs: "
                f"`{', '.join(proposal.family_overlap_pair_keys) or '-'}`"
            )
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

    append_section("Adjusted Ranking", proposals)
    append_section("Explore Lane", explore_proposals)

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
        lines.append(f"# base_novelty_score: {proposal.base_novelty_score:.3f}")
        lines.append(f"# second_order_penalty: {proposal.second_order_penalty:.3f}")
        lines.append(f"# family_penalty: {proposal.family_penalty:.3f}")
        lines.append(f"# redundancy_penalty: {proposal.redundancy_penalty:.3f}")
        lines.append(f"# novelty_score: {proposal.novelty_score:.3f}")
        lines.append(f"# new_account_count: {proposal.new_account_count}")
        lines.append(f"# actionable_support_count: {proposal.actionable_support_count}")
        lines.append(f"# generic_hub_count: {proposal.generic_hub_count}")
        lines.append(f"# community_seed_ratio: {proposal.community_seed_ratio:.3f}")
        lines.append(f"# total_follow_edges: {proposal.total_follow_edges}")
        lines.append(f"# avg_bridge_score: {proposal.avg_bridge_score:.3f}")
        lines.append(f"# avg_cluster_count: {proposal.avg_cluster_count:.3f}")
        lines.append(f"# spillover_community_count: {proposal.spillover_community_count}")
        lines.append("# included_composite_communities: " + ", ".join(proposal.included_composite_community_ids))
        lines.append("# parent_overlap_communities: " + ", ".join(proposal.parent_overlap_community_ids))
        lines.append("# family_overlap_pairs: " + ", ".join(proposal.family_overlap_pair_keys))
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
    composite_community_ids = load_composite_community_ids()
    composite_parent_map = load_composite_parent_map(
        composite_community_ids,
        min_confidence=args.confidence,
    )
    family_pair_map = load_family_pair_map(
        composite_community_ids=composite_community_ids,
        composite_parent_map=composite_parent_map,
        min_confidence=args.confidence,
    )
    family_pair_audit = build_family_pair_audit(
        composite_community_ids=composite_community_ids,
        composite_parent_map=composite_parent_map,
        family_pair_map=family_pair_map,
        min_confidence=args.confidence,
    )
    proposals = build_expansion_proposals(
        min_confidence=args.confidence,
        combo_size=args.combo_size,
        min_support=args.min_support,
        max_proposals=args.max_proposals,
        composite_community_ids=composite_community_ids,
        composite_parent_map=composite_parent_map,
        family_pair_map=family_pair_map,
    )
    explore_proposals = build_expansion_proposals(
        min_confidence=args.confidence,
        combo_size=args.combo_size,
        min_support=args.min_support,
        max_proposals=args.max_proposals,
        exclude_composite_communities=True,
        composite_community_ids=composite_community_ids,
        composite_parent_map=composite_parent_map,
        family_pair_map=family_pair_map,
    )
    report = render_markdown(
        proposals,
        explore_proposals,
        min_confidence=args.confidence,
        combo_size=args.combo_size,
        min_support=args.min_support,
        composite_community_ids=composite_community_ids,
        family_pair_audit=family_pair_audit,
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
