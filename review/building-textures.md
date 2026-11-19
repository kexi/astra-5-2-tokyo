# 東京駅周辺の建物LODと画像テクスチャ

2025年度のPLATEAUから本モデルに採用した建物410タイル、23,115件の建物IDを調べた。**22,620件に画像付きの面があり、そのうち全三角形に画像がある建物は710件**だった。残りの21,910件は一部の面のみ、495件は画像なし。全件で元の建物IDと配信用GLBの面が対応した。対象は千代田区・中央区・港区のうち、この作品に収録した範囲。 [千代田区の配信タイル](https://api.plateauview.mlit.go.jp/datacatalog/3dtiles/13101-bldg-lod2-texture-2025/tileset.json) ・ [中央区](https://api.plateauview.mlit.go.jp/datacatalog/3dtiles/13102-bldg-lod2-texture-2025/tileset.json) ・ [港区](https://api.plateauview.mlit.go.jp/datacatalog/3dtiles/13103-bldg-lod2-texture-2025/tileset.json)

[全23,115件の建物別CSV](building-textures.csv) には公式ID・名称・実LOD・画像付きと画像なしの三角形数を収録。

## 配信メッシュの実LODと画像の範囲

棟別の一覧には、公式名称、地物ID、建物ID、実LOD、画像付き・画像なしの三角形数を記録した。2026年11月19日に独立した二つの集計結果を地物IDで突き合わせ、23,115件すべてでLOD、LOD細分、画像の有無が一致した。 [全棟のCSV](building-textures.csv)

| 元b3dmの実LOD（`_lod`） | 建物ID数 | 全面に画像あり | 一部に画像あり | 画像なし | 画像付き三角形 | 画像なし三角形 |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 495 | 0 | 0 | 495 | 0 | 12,778 |
| 2 | 22,620 | 710 | 21,910 | 0 | 2,413,259 | 420,640 |
| 合計 | 23,115 | 710 | 21,910 | 495 | 2,413,259 | 433,418 |

配信タイルの名称はいずれも `lod2-texture`。各建物の地物属性から実LODを読むと、LOD1の形状で収録された495件が含まれる。また、元データの `uro:lodType` に複数の値を持つ102件はすべて港区の実LOD2だった。LOD3.0の併記は、この作品でLOD3を描画したことを表さない。 [PLATEAUの3D Tiles配信仕様](https://docs.plateauview.mlit.go.jp/datasets/3d-tiles/)

| 元の属性 `uro:lodType` | 実LOD | 建物ID数 | 全面に画像あり | 一部に画像あり | 画像なし |
|---|---:|---:|---:|---:|---:|
| 値なし | 1 | 495 | 0 | 0 | 495 |
| LOD2.0 | 2 | 6,661 | 550 | 6,111 | 0 |
| LOD2.2 | 2 | 15,857 | 160 | 15,697 | 0 |
| LOD2.0 と LOD3.0 | 2 | 102 | 0 | 102 | 0 |

同じタイル `chiyoda-data11` のLOD2.2同士でも、建物ID `13101-bldg-471` は16面すべてに画像があり、`13101-bldg-70` は230面中226面だった。テクスチャの有無・貼り付け範囲は棟別に確認できる。三角形数は表面積と比例しないため、面積の被覆率、画像の解像感、歪みは別途、実画面を見て評価する。

## 東京駅での照合

公式名称が「東京駅」と完全一致した建物IDは4件。すべて同一メッシュ `53394611`、タイル `chiyoda-data442` の実LOD2・LOD2.2で、4件合計で668三角形のうち555面に画像があった。新丸の内ビルディングは1,228面中1,070面、丸の内ビルディングは2,322面中1,771面だった。東京駅メッシュについて2025年度の千代田区・中央区の公式カタログを調べた範囲では、建築物LOD3は0件だった。 [CityGMLメッシュ検索API](https://docs.plateauview.mlit.go.jp/api/rest/operations/datacatalogcitygmlconditions/)

空撮で補修した丸の内駅舎の西面は、名称欄が空の建物ID `13101-bldg-1663` に属する。LOD2.2で全3,165面のうち2,531面に画像がある。八重洲口のグランルーフに対応する `13101-bldg-3635` も名称欄が空で、LOD2.2・20面中18面に画像がある。地物の座標とカメラから当たる面を照合して特定した。

## 再集計

リポジトリのルートから実行する。通常は `tools/prepare-data.py` と同じHTTPキャッシュを読み、各配信用GLBの三角形を `_BATCHID` で元b3dmの `gml_id` に照合する。Base Colorが参照する画像のバイト列と対応するUV属性をともに確認する。画像はWebP拡張の参照も解決する。Draco圧縮されたメッシュではデコードした面インデックスを使っている。 [監査CLI](../tools/audit-building-textures.py)

```sh
uv run --script tools/audit-building-textures.py
uv run --script tools/audit-building-textures.py --building 東京駅
uv run --script tools/audit-building-textures.py --building 13101-bldg-471
uv run --script tools/audit-building-textures.py --csv /tmp/building-textures.csv
```

初回で元b3dmのキャッシュがなければ `--fetch-missing` を付けると、配布マニフェストに記録された公式URLから不足タイルを取得する。別の場所に保管したキャッシュは `--cache` で指定できる。Blenderの再読込で `_BATCHID` を直接確認できなかった6件も配信用GLBでは判定できた。この6件のBlender上の面対応は未確認。
