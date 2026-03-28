"""Twitter 界隈調査 CLI"""
import sys
from pathlib import Path

import click

# プロジェクトルートをsys.pathに追加
sys.path.insert(0, str(Path(__file__).resolve().parent))


@click.group()
def cli():
    """Twitter 界隈調査ツール"""
    pass


@cli.command()
def init():
    """データベースを初期化"""
    from db.models import init_db
    init_db()
    print("[OK] データベース初期化完了")


@cli.command()
def list_communities():
    """定義済み界隈を一覧表示"""
    from communities import load_all_communities
    defs = load_all_communities()
    if not defs:
        print("界隈定義がありません。communities/definitions/ にYAMLファイルを追加してください")
        return
    for d in defs:
        seeds_count = len(d.seeds)
        print(f"  {d.id}: {d.name} (シード: {seeds_count}, ハッシュタグ: {len(d.hashtags)}, キーワード: {len(d.keywords)})")


@cli.command()
@click.argument("community_id", required=False)
def discover(community_id):
    """界隈メンバーを発見（Stage 1: シード解決 + 検索）"""
    from communities import load_all_communities
    from pipeline.discover import discover_community

    defs = load_all_communities()
    targets = [d for d in defs if not community_id or d.id == community_id]

    if not targets:
        print(f"界隈 '{community_id}' が見つかりません")
        return

    for d in targets:
        discover_community(d)


@cli.command()
@click.argument("community_id", required=False)
def collect_follows(community_id):
    """フォローグラフを収集（Stage 2: フォロー拡張）"""
    from communities import load_all_communities
    from pipeline.collect_follows import collect_follow_graph

    defs = load_all_communities()
    targets = [d for d in defs if not community_id or d.id == community_id]

    if not targets:
        print(f"界隈 '{community_id}' が見つかりません")
        return

    for d in targets:
        collect_follow_graph(d)


@cli.command()
@click.argument("community_id", required=False)
def collect_profiles(community_id):
    """プロフィールを一括収集（Stage 3）"""
    from pipeline.collect_profiles import collect_profiles as _collect
    _collect(community_id)


@cli.command()
@click.argument("community_id", required=False)
@click.option("--skip-seeds", is_flag=True, help="seed再解決をスキップ")
def repair_users(community_id, skip_seeds):
    """壊れた user 参照を補修し、プロフィールを再取得"""
    from pipeline.repair_users import repair_users as _repair
    _repair(community_id=community_id, rerun_seeds=not skip_seeds)


@cli.command()
@click.option("--confidence", "-c", default=0.5, help="最低confidence閾値")
def analyze(confidence):
    """規模・重複を分析してレポート生成"""
    from output.report import generate_report
    generate_report(min_confidence=confidence)


@cli.command()
@click.option("--confidence", "-c", default=0.5, help="最低confidence閾値")
def visualize(confidence):
    """可視化（ヒートマップ・グラフ）を生成"""
    from output.visualize import plot_community_sizes, plot_network_graph, plot_overlap_heatmap
    plot_overlap_heatmap(min_confidence=confidence)
    plot_community_sizes(min_confidence=confidence)
    plot_network_graph(min_confidence=confidence)


@cli.command()
@click.option("--confidence", "-c", default=0.5, help="最低confidence閾値")
def pages(confidence):
    """GitHub Pages (docs/index.html) を更新"""
    from generate_pages import generate_pages
    generate_pages(min_confidence=confidence)


@cli.command()
@click.option("--confidence", "-c", default=0.5, help="譛菴残onfidence髢ｾ蛟､")
@click.option("--combo-size", default=3, help="combo size for proposal extraction")
@click.option("--min-support", default=6, help="minimum supporting accounts")
@click.option("--max-proposals", default=12, help="maximum proposal count")
@click.option("--write-yaml", is_flag=True, help="write YAML stubs into communities/proposals")
def frontier_expand(confidence, combo_size, min_support, max_proposals, write_yaml):
    """frontier seed 縺九ｉ新規界隈候補繧呈歓蜃ｺ"""
    from pathlib import Path

    from tools.frontier_expansion import (
        build_expansion_proposals,
        load_composite_community_ids,
        render_markdown,
        write_yaml_stubs,
    )

    composite_community_ids = load_composite_community_ids()
    proposals = build_expansion_proposals(
        min_confidence=confidence,
        combo_size=combo_size,
        min_support=min_support,
        max_proposals=max_proposals,
        composite_community_ids=composite_community_ids,
    )
    explore_proposals = build_expansion_proposals(
        min_confidence=confidence,
        combo_size=combo_size,
        min_support=min_support,
        max_proposals=max_proposals,
        exclude_composite_communities=True,
        composite_community_ids=composite_community_ids,
    )

    output_path = Path("data/exports/frontier_expansion_2026-03-27.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_markdown(
            proposals,
            explore_proposals,
            min_confidence=confidence,
            combo_size=combo_size,
            min_support=min_support,
            composite_community_ids=composite_community_ids,
        ),
        encoding="utf-8",
    )
    print(f"[OK] {output_path} written")

    if write_yaml:
        written = write_yaml_stubs(proposals, Path("communities/proposals"))
        print(f"[OK] wrote {len(written)} YAML proposal stubs")


@cli.command()
@click.argument("community_id", required=False)
def run_all(community_id):
    """全パイプラインを実行（discover → follows → profiles → analyze）"""
    from communities import load_all_communities
    from pipeline.collect_follows import collect_follow_graph
    from pipeline.collect_profiles import collect_profiles as _collect_profiles
    from pipeline.discover import discover_community
    from output.report import generate_report

    defs = load_all_communities()
    targets = [d for d in defs if not community_id or d.id == community_id]

    if not targets:
        print(f"界隈 '{community_id}' が見つかりません")
        return

    for d in targets:
        print(f"\n{'#'*60}")
        print(f"# {d.name} ({d.id})")
        print(f"{'#'*60}")
        discover_community(d)
        collect_follow_graph(d)

    _collect_profiles(community_id)
    generate_report()


if __name__ == "__main__":
    cli()
