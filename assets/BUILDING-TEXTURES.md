# 建物ごとの外装テクスチャ

千代田区・中央区・港区の収録範囲には、公式の建物IDを持つ23,115棟がある。Blenderでは各棟が一つのメッシュを持ち、建物IDと同名の画像をファイルに埋め込む。材質のベースカラーにこの画像を接続し、面の角ごとに専用UV `BuildingPaint` を割り当てる。元のPLATEAUのUVも保存する。棟のIDと原典の撮影範囲は[全棟の出典一覧](../review/building-textures.csv)で照合できる。

## 原典と加工

- 国土交通省PLATEAUの2025年度3D Tilesから、建物ID、LOD、三角形と外装写真を取得した。写真がある面は元のUVから該当する画像領域だけを切り出し、棟別アトラスに余白を設けて配置した。先行版で手作業の補修を施した大丸有の建物と市街地の3タイルには、補修済みの画像を使った。原典には撮影画像のある建物が22,620棟あり、495棟は写真なしのLOD1。部分的に写真がない建物もある。[PLATEAU配信サービス](https://docs.plateauview.mlit.go.jp/datasets/3d-tiles/) ・ [PLATEAUの利用条件](https://www.mlit.go.jp/plateau/site-policy/) ・ [タイル別の出典URL](manifest.json)
- 撮影画像がない上向きの屋根は、Blenderの頂点を測地座標に戻し、該当位置の東京都のオルソ画像・地理院写真をサンプリングした。画面端の建物には地理院写真の周辺5タイルを補った。屋根に樹影や別の撮影時点の構造物が写っている場合がある。[東京都の区部点群・オルソ画像（CC BY 4.0）](https://www.geospatial.jp/ckan/dataset/tokyopc-23ku-2024) ・ [国土地理院の地理院タイル](https://maps.gsi.go.jp/development/ichiran.html) ・ [追加タイルの出典・SHA-256](roof-orthophotos/layers.json)
- 外壁・軒裏の写真が欠けている部分は、棟の外形と高さ、元の面色に合わせて仕上げを作成した。推定した窓割り、目地、材質の粗さは現地の実測値ではない。実在する建物の名称や所在が判明していても、それだけでは各面の外観を特定できない。
- 壁に屋上設備・隣棟が写った箇所は、同じ建物の判読可能な壁面を優先して補修した。星のや東京では汚染範囲が広く、公式資料にある外装の金属格子を参考にして、窓、階ごとの帯、格子を推定制作した。[星のや東京「江戸小紋が東京に結界を張る」](https://hoshinoresorts.com/ja/hotels/hoshinoyatokyo/sp/story2/) ・ [先行版の大丸有22棟の補修記録](OMY-FACADE-REPAIRS.md)

## 元の外壁写真を確認した例

`13101-bldg-1033` のCityGMLでは、26枚の壁ポリゴンの全てが同じ外壁用JPEGを参照する。そのJPEGに隣棟の屋上と設備が大きく写り込んでいた。2023年度の同名画像と2025年度の画像のファイルサイズ・CRCも一致した。星のや東京を含む他の4棟でも年度間で一致した。`13101-bldg-1771` と `13101-bldg-1643` では両年度の建物IDと壁面を照合し、2025年度の画像を縦横とも半分に縮小して旧年度画像と比べたところ、RGBの相関係数はいずれも0.99を超えた。これらの旧年度画像からは、写り込みの裏側の実景を回復できなかった。[1033の原典の外壁JPEG](../review/13101-bldg-1033-source-wall.jpg) ・ [国交省PLATEAUの2025年度千代田区CityGML](https://www.geospatial.jp/ckan/dataset/plateau-13101-chiyoda-ku-2025) ・ [2023年度千代田区CityGML](https://www.geospatial.jp/ckan/dataset/plateau-13101-chiyoda-ku-2023)

写真なしの495棟についても、千代田区・中央区の2025年度CityGMLの14図郭に戻り、全ての地物IDを確認した。該当する全棟の幾何は `lod0RoofEdge` と `lod1Solid` で、ポリゴンを指す画像テクスチャの指定は0件だった。[地物ID・図郭・ポリゴン別の照合結果](../review/citygml-appearance.json) ・ [原典の照合スクリプト](../tools/audit-citygml-appearance.py) ・ [2025年度中央区CityGML](https://www.geospatial.jp/ckan/dataset/plateau-13102-chuo-ku-2025)

霞が関ビルディング（`13101-bldg-1591`）は地上写真からも外壁を照合した。2022年4月23日に東南側から撮られた写真では、窓の縦の方立と水平帯が塔の下部まで続き、広場に接して低い曲面のガラスひさしが見える。撮影位置の違いを考慮し、塔の途中に投影された屋上設備と足元に実在するひさしを区別する際の参照とした。[Akonnchiroll撮影「Kasumigaseki Building 2022」](https://commons.wikimedia.org/wiki/File:Kasumigaseki_Building_2022.jpg)

## Blenderでの確認

オブジェクトの `id` が公開ビューアの検索IDに対応する。画像エディターで同じIDを選べば、その棟の画像を単独で表示・保存できる。全画像を取り出す場合はBlenderの「ファイル」→「外部データ」→「リソースを展開」を使う。専用画像は `building-textures/<タイル>/<建物ID>.png` または `.jpg` のパスを持つ。シェーダーエディターで画像テクスチャの入力UVを確認できる。ジオメトリのスプレッドシートで面ドメインを開くと、次の属性が読める。

| 属性 | 値と意味 |
| --- | --- |
| `TextureKind` | 0 は引き継いだ写真、1 は推定の外壁、2 は写真欠損を補った上面、3 は推定の下面、4 は誤投影を補修した元写真面。 |
| `TextureOrtho` | 上面の補完に、その棟の位置のオルソ画像を採用した面。 |
| `TextureRepairReason` | 1 は屋根と壁のUV重複、2 は壁のUVのつぶれ、3 は星のや東京の格子外装の再構成、4 は別UVに複製された屋根模様、5 はAstraが特定して同じ棟の壁面写真で補修した側壁、6 は適切な採取面が残らず推定制作した側壁。7・9 は側面と近傍の航空写真の一致、8 は同じ立面内の模様の不連続、10・11 は近傍の屋根による遮蔽と撮影画像の特徴から判断したもの。7・8・10 は同じ棟の写真を採取し、9・11 は寸法と色を使って推定制作した。 |
| `TexturePriorRepair` | 先行版で個別補修した写真面のうち、専用の補修用材質で追跡できたもの。 |

全棟版の検査では、Blenderの保存ファイルを開き直し、元の形状、画像とUVの接続、画像の全画素、描画用に書き出した三角形を照合する。個別ビューアのWebPには配信用の圧縮が入る。全景用には同じ棟別画像を縮小してタイルにまとめる。[監査スクリプト](../tools/audit-building-paint.py) ・ [回転レビューと修正記録](../review/README.md)

建物のカスタムプロパティ `textureScreenState` に棟単位の確認結果を記録した。Astraが特定した補修、正常と判断して保持した写真、機械判定による局所補修、判定保留を区別できる。位置を絞れた保留範囲は `textureUncertainRegions` に方位と立面内の矩形を記録した。写真を見る方向は原典の撮影時カメラから復元できないため、周囲の別の建物と実際の高さ関係を調べ、三つの候補方向での遮蔽、写真に写る窓桟の周期や斜材の有無を組み合わせて補修範囲を選んだ。[棟別の判定データ](../tools/blender_texture_screening.json.gz) ・ [Astraが別途確認した標本の建物IDと方位](../review/building-paint-holdouts.csv)

自動で補修する範囲では、元写真に残る外壁と採取元の明るさを局所的に合わせ、境界をなじませた。明るさの差が大きく、補修元の窓・仕上げを読み取れない箇所は元の写真を残す。`textureScreenQuality` は判定した立面上の画素数、そのうち維持・補修した画素数、保留した領域数、補修境界に追加した最大の輝度・色差（RGB各成分、0–255）を保持する。この画素数は正投影した立面上の評価値である。

## 元の写真から再判定する

通常の制作では `just build-blender-mcp` が同梱の判定を適用する。撮影画像や幾何を更新して判定からやり直す場合は、専用画像を貼る前の[全棟モデル](https://github.com/kexi/astra-5-2-tokyo/releases/download/tokyo-station-buildings-v1/tokyo-station.blend)を `source.blend` として用意し、リポジトリのルートで次を実行する。`blender` はBlender 5.2の実行ファイルを指す。

```sh
mkdir -p /tmp/tokyo-facades
blender -b source.blend --python-exit-code 1 -P tools/blender_texture_elevations.py -- /tmp/tokyo-facades/elevations
uv run tools/blender_texture_screen.py /tmp/tokyo-facades/elevations /tmp/tokyo-facades/screen
uv run tools/blender_texture_aerial_match.py /tmp/tokyo-facades/elevations /tmp/tokyo-facades/screen/screen.json.gz /tmp/tokyo-facades/aerial.json.gz
blender -b source.blend --python-exit-code 1 -P tools/blender_texture_visibility.py -- /tmp/tokyo-facades/visibility.npz
uv run tools/blender_texture_occlusion.py /tmp/tokyo-facades/visibility.npz /tmp/tokyo-facades/horizons.json.gz
uv run tools/blender_texture_wall_detail.py /tmp/tokyo-facades/elevations /tmp/tokyo-facades/detail.json.gz
uv run tools/blender_texture_decisions.py /tmp/tokyo-facades/screen/screen.json.gz /tmp/tokyo-facades/result.json.gz --geographic /tmp/tokyo-facades/aerial.json.gz --horizons /tmp/tokyo-facades/horizons.json.gz --details /tmp/tokyo-facades/detail.json.gz
```
