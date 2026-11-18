# 東京駅上空

PLATEAUの2025年度3D都市モデルを使い、東京駅・丸の内・八重洲をThree.jsで再現した空撮作品です。

- [GitHub Pagesで3Dモデルを見る](https://kexi.github.io/astra-5-2-tokyo/)
- [空撮動画（MP4）](output/tokyo-station.mp4)
- [360°レビュー](review/README.md)

画面下部の「空撮を再生」でカメラルートを再生できます。「360° 見回す」に切り替えると、ドラッグで回転、スクロールでズームできます。ボタンにフォーカスを置いた状態では、矢印キーで方位と俯角を変更できます。フレアの表示は切り替えられます。

## ローカルで開く

24.11以降のNode.js 24とpnpm 10.32.1を使用します。WebGL 2対応ブラウザで表示してください。

```sh
pnpm install --frozen-lockfile
pnpm run build
python3 -m http.server 8421 --bind 127.0.0.1 --directory dist
```

ブラウザで `http://127.0.0.1:8421/` を開きます。開発中は `just serve` でソース版を直接表示できます。初回表示では地形・建物テクスチャを含む約166 MiBのデータを読み込みます。

## Blenderで編集する

Blender 5.2で `just build-blender` を実行すると、`output/tokyo-station.blend` が生成されます。Blenderの場所は `BLENDER_BIN` で指定できます。写真テクスチャはプロジェクトに同梱され、建物・道路・水域・樹木・橋梁・街路設備・軌道をコレクション別に編集できます。タイムラインには空撮7カットのカメラとマーカーがあります。

ケヤキとクスノキをもとにした木は、根張り、幹、枝、立体の葉を作成しました。葉脈、樹皮、逆光の透過、葉と枝の風揺れをBlenderのノードとシェイプキーで調整できます。Three.jsでも同じBlenderの形状を読み込み、遠くの木は撮影角度別の画像を使って表示します。窓の反射にはPLATEAUの外壁写真から推定した窓面を使います。

## 撮影と再構築

```sh
just review
just render
```

撮影にはChromeとFFmpegを使用します。Chromeの場所は `CHROME_BIN` で指定できます。レビューは北から時計回りに45°間隔で周回し、低高度4方位、俯瞰、濠、草、フレア比較、空撮ルートの静止画を保存します。動画はBlenderで制作した樹木を読み込んだThree.jsシーンから、39.5秒の1080p・30 fpsのH.264 MP4として書き出します。

都市データを再取得する手順です。鉄道参照データの取得後に、航空レーザと照合して軌道を生成します。

```sh
uv run tools/prepare-data.py
uv run tools/prepare-rail.py
uv run tools/prepare-survey.py
uv run tools/build-rail-model.py
just build-blender
```

基礎都市タイルを保持して橋梁・街路設備・植生などを更新するには、`uv run tools/prepare-data.py --extend-plateau` を使用します。取得先はPLATEAUの各タイルを `assets/manifest.json`、地盤・写真・樹高の元図郭とチェックサムを `assets/survey.json` に記録しています。

## 自動デプロイ

GitHub Actionsは `main` へのpushで配布版をビルドし、GitHub Pagesへデプロイします。PRでは同じビルドと静的検査を実行します。tsdownはThree.jsとアプリのJavaScriptをバンドルし、minifyします。3D素材と動画はビルド時に `dist/` へコピーします。

## データ出典と反映範囲

| データ | モデルへの反映 |
| --- | --- |
| 国土交通省 Project PLATEAU 2025年度・千代田区、中央区、港区 | 建築物、道路、植生、水域の写真・形状に加え、橋梁121件、都市設備974件、独立木98本、道路と水部各1件を追加。重複する地物を除いた全863タイルを使用。 |
| 東京都デジタルツイン実現プロジェクト・区部点群データ | 公開オルソ20 cm・地盤DEM25 cmの16図郭を切り出し、地表の写真と標高に使用。レーザ点群2図郭で68地点の樹高を推定し、駅南側の在来線29連続区間・619地点の支持面を推定。 |
| 国土地理院 | シームレス航空写真、DEM10Bと5 m DEM5A、原点のジオイド2024の値36.7647 mを使用。鉄道中心線38区間をBlenderの平面参照コレクションに収録。 |
| OpenStreetMap | 東京駅南北の個別軌道209区間を抽出。東京都の点群で支持面を推定できた27本の軌道にレール、まくらぎ、路盤を作成。 |
| 国土交通省・国土数値情報 N02（2025年度） | 路線と駅区間89件をBlenderの平面参照コレクションに収録。 |

オルソは局所で約24 cm/画素、局所DEMは配布時約0.97 m/画素です。東京都の配布画像の欠測は地理院写真で補完し、地面のメッシュは駅周辺を2.5 m間隔でサンプリングしています。鉄道は中心線・軌間と点群から推定した支持面に沿って描画します。路盤の側面とまくらぎは再構成した形状です。

- [PLATEAU配信サービス](https://docs.plateauview.mlit.go.jp/datasets/3d-tiles/) · [利用規約](https://www.mlit.go.jp/plateau/site-policy/)
- [東京都デジタルツイン実現プロジェクト 区部点群データ（CC BY 4.0）](https://www.geospatial.jp/ckan/dataset/tokyopc-23ku-2024) · [東京都の公開案内](https://www.metro.tokyo.lg.jp/information/press/2024/10/2024103126)
- [地理院タイル一覧](https://maps.gsi.go.jp/help/use.html) · [鉄道中心線](https://github.com/gsi-cyberjapan/experimental_railcl) · [測量計算API](https://vldb.gsi.go.jp/sokuchi/surveycalc/api_help.html)
- [国土数値情報 鉄道データ（CC BY 4.0）](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N02-2025.html)
- [© OpenStreetMap contributors](https://www.openstreetmap.org/copyright) · [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) · [軌道の加工データと利用条件](assets/RAIL-SOURCES.md)

雲は64×64×64の3Dノイズテクスチャで生成します。太陽、道路、地面、草、建物、水面のシェーダと、視点の深度・高度・太陽の方位に応じたフォグを適用しています。
