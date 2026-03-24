# Twitter 界隈調査 (twitter_kaiwai_survey)

日本のTwitter(X)における各「界隈」の規模と界隈間の重複・親和度を調査するシステム。

**Dashboard**: https://jim-auto.github.io/twitter_kaiwai_survey/

## 概要

- **界隈の規模測定**: メンバー数、アクティブユーザー数、総フォロワーリーチ、インフルエンサー数
- **界隈間の重複分析**: Jaccard類似度、非対称含有率
- **フォロー親和度分析**: フォローグラフから界隈間のつながりを可視化
- **コンテンツスカウト**: 界隈間のインフルエンサー分析ツール
- **ダッシュボード**: GitHub Pagesでリアルタイム表示（ネットワークグラフ、ヒートマップ）

## 調査済み界隈（18界隈）

| ID | 界隈名 | メンバー | リーチ |
|----|--------|---------|--------|
| anime | アニメ・漫画界隈 | 44 | 18.7M |
| food | 料理・グルメ界隈 | 47 | 13.7M |
| crypto | 仮想通貨・Web3界隈 | 57 | 11.0M |
| vtuber | VTuber界隈 | 30 | 10.5M |
| idol | アイドル界隈 | 37 | 9.8M |
| fx_traders | FX・投資界隈 | 30 | 4.4M |
| beauty | 美容界隈 | 57 | 3.4M |
| fortune | 占い・スピリチュアル界隈 | 60 | 2.6M |
| politics | 政治界隈 | 16 | 2.4M |
| education | 教育界隈 | 29 | 1.9M |
| photo | 写真・カメラ界隈 | 31 | 1.5M |
| gaming | ゲーム界隈 | 36 | 1.2M |
| engineer | エンジニア界隈 | 25 | 1.0M |
| startup | 起業・スタートアップ界隈 | 22 | 1.0M |
| nanpa | ナンパ界隈 | 693 | 953K |
| sedori | 副業・物販界隈 | 45 | 845K |
| muscle | 筋トレ界隈 | 29 | 595K |
| host | ホスト界隈 | 40 | 514K |

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
python main.py pages                   # GitHub Pages更新

# 全ステージ一括
python main.py run-all nanpa

# コンテンツスカウト
python tools/content_scout.py pairs                    # 親和度の高い界隈ペア
python tools/content_scout.py scout food muscle        # 料理→筋トレの翻案
python tools/content_scout.py influencers engineer     # エンジニア界隈のインフルエンサー
```

## データ収集パイプライン

```
Stage 1: シード解決 + typeahead検索 + Playwright検索
    |
Stage 2: フォローグラフ拡張（6ワーカー並列）
    |
Stage 3: プロフィール一括収集
    |
分析: 規模メトリクス + Jaccard重複 + フォロー親和度
    |
出力: Dashboard + JSON/Markdown + ネットワークグラフ
```

## 界隈間フォロー親和度 TOP5

| 界隈A | 界隈B | 親和度 | 意味 |
|-------|-------|--------|------|
| FX・投資 | 副業・物販 | 0.00145 | 投資クラスタ |
| 仮想通貨 | FX・投資 | 0.00117 | 投資クラスタ |
| 政治 | スタートアップ | 0.00085 | ビジネスクラスタ |
| アニメ | VTuber | 0.00082 | エンタメクラスタ |
| エンジニア | スタートアップ | 0.00071 | テッククラスタ |

## 技術スタック

- Python 3.9+
- Twitter GraphQL API + Playwright（Cookie認証、6ワーカー並列）
- SQLite + SQLAlchemy
- GitHub Pages（ダッシュボード）
