# CLAUDE.md — twitter_kaiwai_survey

## 概要
日本のTwitter界隈の規模と重複を調査するシステム。18界隈、99K+ユーザー、173K+フォローエッジ。

## 関連プロジェクト
- `../sokusuu-ranking` — Cookieファイル（data/.twitter_cookies*.json）とフォローグラフデータを共有

## よく使うコマンド
```bash
python main.py list-communities        # 界隈一覧
python main.py run-all <community_id>  # 全パイプライン実行
python main.py pages                   # GitHub Pages更新（23分かかる→要高速化）
python tools/content_scout.py pairs    # 界隈親和度ペア
```

## 重要な技術的注意
- GraphQLハッシュ（config/settings.py）はTwitterが定期変更する。動かなくなったらmain.jsから再抽出
- SearchTimeline APIは全ハッシュ404。Cookie認証レベルの制限。代替: typeahead + Playwright
- screen_nameはGraphQLレスポンスのcoreオブジェクトに移動済み（legacy→core）
- SQLAlchemy ORM → SQLite直接クエリに移行中（community_size.py, overlap.pyは移行済み）
- nanpaの102Kエッジがaffinity計算のボトルネック。nanpa-onlyユーザーをスキップで対応済み
- Windowsでは `set PYTHONIOENCODING=utf-8` が必要

## 最優先の改善点
1. generate_pages.pyの高速化（build_affinity_matrixがcompute_follow_affinityを2回呼ぶ問題）
2. 各界隈のメンバー数増加（collect-followsの繰り返し実行）
3. SearchTimeline復旧またはPlaywright検索の強化
