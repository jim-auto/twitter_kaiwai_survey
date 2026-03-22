# Twitter 界隈調査 (twitter_kaiwai_survey)

日本のTwitter(X)における各「界隈」の規模と界隈間の重複度を調査するシステム。

## 概要

- **界隈の規模測定**: メンバー数、アクティブユーザー数、総フォロワーリーチ、インフルエンサー数
- **界隈間の重複分析**: Jaccard類似度、非対称含有率（AのうちBにもいる割合）
- **可視化**: 重複ヒートマップ、規模バーチャート、ネットワークグラフ

## セットアップ

```bash
pip install -r requirements.txt
python main.py init
```

sokusuu-ranking のCookieファイルを自動検出するため、同ディレクトリ構成であれば追加設定不要。

## 使い方

```bash
# 界隈一覧
python main.py list-communities

# 個別ステージ実行
python main.py discover nanpa          # Stage 1: シード解決 + 検索発見
python main.py collect-follows nanpa   # Stage 2: フォローグラフ拡張
python main.py collect-profiles nanpa  # Stage 3: プロフィール収集
python main.py analyze                 # 規模・重複レポート生成
python main.py visualize              # ヒートマップ・グラフ生成

# 全ステージ一括
python main.py run-all nanpa
```

## 界隈定義

`communities/definitions/` にYAMLファイルで定義。`_template.yaml` を参考に追加可能。

```yaml
id: nanpa
name: ナンパ界隈
seeds:
  - username: example_user
    role: influencer
hashtags: ["#ナンパ", "#ストナン"]
keywords: ["ナンパ 即"]
bio_patterns: ["(ナンパ|即数|斬り)"]
exclude_patterns: ["(公式|企業)"]
expansion:
  max_depth: 2
  min_shared_follows: 3
  max_members: 5000
```

### 定義済み界隈

| ID | 界隈名 |
|----|--------|
| nanpa | ナンパ界隈 |
| muscle | 筋トレ界隈 |
| fx_traders | FX・投資界隈 |
| startup | 起業・スタートアップ界隈 |
| host | ホスト界隈 |

## データ収集パイプライン

```
Stage 1: シード解決 + キーワード/ハッシュタグ検索
    ↓
Stage 2: フォローグラフ拡張（マルチワーカー並列）
    ↓
Stage 3: プロフィール一括収集
    ↓
分析: 規模メトリクス + Jaccard重複マトリクス
    ↓
出力: JSON/Markdown レポート + ヒートマップ + ネットワークグラフ
```

## 出力

- `data/exports/report.json` - 全界隈の規模・重複データ
- `data/exports/report.md` - Markdownレポート
- `data/exports/overlap_heatmap.png` - 重複ヒートマップ
- `data/exports/community_sizes.png` - 規模バーチャート
- `data/exports/community_network.html` - インタラクティブネットワークグラフ

## 技術スタック

- Python 3.9+
- Twitter GraphQL API（Cookie認証）
- SQLite + SQLAlchemy
- matplotlib / seaborn / networkx / pyvis
