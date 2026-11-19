# 東京駅上空

PLATEAUの2025年度3D都市モデルを使い、東京駅・丸の内・八重洲をThree.jsで再現した空撮作品です。

- [GitHub Pagesで3Dモデルを見る](https://kexi.github.io/astra-5-2-tokyo/)
- [空撮動画（MP4）](https://kexi.github.io/astra-5-2-tokyo/output/tokyo-station.mp4)
- [全23,115棟のBlenderファイル（350 MiB）](https://github.com/kexi/astra-5-2-tokyo/releases/download/tokyo-station-buildings-v1/tokyo-station.blend)
- [360°レビュー](review/README.md)
- [建物23,115件のLODと画像テクスチャ](review/building-textures.md)
- [大丸有22棟の外装を棟別に見る](https://kexi.github.io/astra-5-2-tokyo/?model=buildings) ・ [東京駅周辺地下街の断面を見る](https://kexi.github.io/astra-5-2-tokyo/?model=underground)
- [22棟・屋上設備・地下街の編集用Blenderファイル](https://kexi.github.io/astra-5-2-tokyo/output/omy-architecture.blend)

画面下部の「空撮を再生」でカメラルートを再生できます。「360° 見回す」に切り替えると、ドラッグで回転、スクロールでズームできます。ボタンにフォーカスを置いた状態では、矢印キーで方位と俯角を変更できます。フレアの表示は切り替えられます。

## ローカルで開く

24.11以降のNode.js 24とpnpm 10.32.1を使用します。WebGL 2対応ブラウザで表示してください。

```sh
pnpm install --frozen-lockfile
pnpm run build
python3 -m http.server 8421 --bind 127.0.0.1 --directory dist
```

ブラウザで `http://127.0.0.1:8421/` を開きます。開発中は `just serve` でソース版を直接表示できます。空撮を開くと地形・建物テクスチャを含む約187 MiBを読み込みます。棟別の外装と地下街は、それぞれ専用ページから直接開けます。

### ローカルチェック

Webコードは `just format-web` で整形し、`just check-web` でOxfmtとOxlintの両方を実行できます。依存パッケージの導入後は、`just check` でCIと同じ検査を実行できます。

## Blenderで編集する

都市全体の `output/tokyo-station.blend` では、収録したPLATEAU建物23,115棟をそれぞれ選択し、編集モードで頂点や面を編集できます。建物はアウトライナーで千代田区・中央区・港区に分かれ、各区の下に元のタイルがあります。各棟のカスタムプロパティには建物ID（`id`）、地物ID（`gml`）、実際のLOD、写真のある面数が入っています。外壁写真はファイルに埋め込みました。道路・水域・樹木・橋梁・街路設備・軌道は別コレクションにあり、タイムラインには空撮8カットのカメラとマーカーがあります。

Web版は同じ都市形状をタイル単位で読み込み、外装を個別に仕上げた大丸有22棟を専用のGLBから表示します。Blenderの全棟版には写真のある22,620棟と、形状と材質で表現したLOD1の495棟が含まれます。各棟の写真の範囲は[建物ID別の集計](review/building-textures.md)で確認できます。

三菱地所設計の公開事例図に載る22物件の主棟を公開PLATEAU形状と照合し、建物ごとに独立したメッシュと編集用の外装写真アトラスを作成しました。丸の内二丁目ビルの屋上と上層外壁には、東京都の航空レーザ点群や同一建物の画像から再構成した設備と窓を追加しています。地下街は国交省の2023年度実証で公開されたLOD4.0の地物を取り込みました。床と壁は元データの形状に合わせ、推定仕上げのPBRテクスチャを貼っています。[データ出典・加工範囲と利用条件](assets/OMY-SOURCES.md)

Blender 5.2にBlender LabのMCP拡張と `blender-mcp` を導入し、`just build-blender-mcp` を実行すると、都市全体と大丸有22棟・地下街の `output/omy-architecture.blend` を再生成できます。作品専用のバックグラウンドプロセスを起動し、MCP経由で作成・保存して終了します。`just build-blender` では同じ制作スクリプトを直接実行できます。Blenderの場所は `BLENDER_BIN` で指定できます。

データ取得とMCPクライアントは、各スクリプトのPEP 723メタデータを使って `uv run` で起動します。Blender内で動く制作スクリプトは同梱のPython 3.13、`bpy`、NumPyを使用します。Python全体のリントと整形は `just check-python` で検査できます。

ケヤキとクスノキをもとにした木は、根張り、幹、枝、立体の葉を作成しました。葉脈、樹皮、逆光の透過、葉と枝の風揺れをBlenderのノードとシェイプキーで調整できます。Three.jsでも同じBlenderの形状を読み込み、遠くの木は撮影角度別の画像を使って表示します。窓の反射にはPLATEAUの外壁写真から推定した窓面を使います。

PLATEAUの独立木98本は、元データの位置、樹高、樹冠の幅を保ち、幹・枝・葉房をBlenderで作り直しました。編集用プロジェクトでは「05 PLATEAU・樹木」にあり、各オブジェクトのカスタムプロパティから測量元のタイルと地物をたどれます。

## 撮影と再構築

```sh
just review
just render
node tools/render.mjs buildings /tmp/tokyo-buildings
node tools/render.mjs underground /tmp/tokyo-underground
```

撮影にはChromeとFFmpegを使用します。Chromeの場所は `CHROME_BIN` で指定できます。レビューは北から時計回りに45°間隔で周回し、低高度4方位、俯瞰、濠、草、フレア比較、空撮ルートの静止画を保存します。動画はBlenderで編集した22棟の外装と屋上設備、樹木を読み込んだThree.jsシーンから、39.5秒の1080p・30 fpsのH.264 MP4として書き出します。

都市データを再取得する手順です。鉄道参照データの取得後に、航空レーザと照合して軌道を生成します。

```sh
uv run tools/prepare-data.py
uv run tools/prepare-rail.py
uv run tools/prepare-survey.py
uv run tools/prepare-park-ground.py
uv run tools/prepare-expressway.py
uv run tools/build-rail-model.py
just build-blender
```

丸の内駅舎と新丸の内ビルディングでは同じ建物の写真から壁の写り込みを補修し、八重洲のグランルーフ東面は外観資料から推定して描き直しました。元の建物形状とUVを保持しています。[修正前後の比較](review/README.md#空撮で見つけた写真の誤投影)

基礎都市タイルを保持して橋梁・街路設備・植生などを更新するには、`uv run tools/prepare-data.py --extend-plateau` を使用します。取得先はPLATEAUの各タイルを `assets/manifest.json`、地盤・写真・樹高の元図郭とチェックサムを `assets/survey.json` に記録しています。

独立木を再生成するには、Blenderの実行バイナリで `--background --factory-startup --python-exit-code 1 --python tools/build-street-trees.py` を実行します。このGLBをBlenderとThree.jsが読み込みます。

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
