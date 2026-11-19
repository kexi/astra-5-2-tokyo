# 丸の内二丁目ビルの外装と屋上設備

`marunouchi-nichome.glb` は丸の内仲通りに面した西側と北側の上部外装、屋上の設備架台3区画、円形設備2基の追加モデルです。既存のPLATEAU建物が躯体、屋上の塔屋、1階店舗を受け持ちます。

## 測量と建物の特定

| 資料 | 使用箇所 |
| --- | --- |
| 東京都「東京都デジタルツイン実現プロジェクト 区部点群データ」図郭 `09LD1884`、CC BY 4.0 | 航空レーザ点群から設備の位置・上面高・半径を抽出。地上画素寸法20 cmの簡易オルソ画像を架台表面用に切り出し、点群上の二つの円形設備に合わせて水平補正した。 |
| 国土交通省 Project PLATEAU、東京都千代田区2025年度LOD2建築物 | `chiyoda-data454` バッチ1、GML ID `bldg_40c858e0-1b52-460b-87e7-30595af73a6b`。地物の四隅、方位、外壁上端、既存屋上の高さを採用。外壁写真を方位別に正射化し、柱間約6.7 m、上層階の繰り返し約3.25 m、窓ごとの明暗を読み取った。 |
| 三菱地所オフィス情報「丸の内二丁目ビル」 | 丸の内二丁目5番1号、地上10階、塔屋2階、最高部43 m。掲載地図の北緯35.680229888865度、東経139.76316001457度からPLATEAUの建物に鉛直照合した。 |

東京都のLASの水平座標はJGD2011平面直角第IX系で、標高に本シーンのジオイド高36.7647 mを足してから東京駅原点の東・北・上へ変換しています。確認窓35.5 × 100.8 mに入った高所の点は155,088点でした。2基の上端の高さは原点上42.241 mと42.240 mで、円形部分の抽出に各341点と360点を使いました。架台3区画の上面高は41.770 m、41.773 m、41.749 mです。

簡易オルソには高い屋上物の倒れ込みがあるため、設備の位置は点群から定めました。オルソのサンプル位置は建物短軸の東向きに0.71 m、長軸の南向きに2.10 m補正しています。立体にした円形設備の真下では、隣接する同じ架台の色を用いて元の写真に映る設備を取り除いています。

写真で読み取れる範囲には上層の窓割り、窓ごとの明暗、設備上部の円形輪郭が含まれます。サッシの出寸法、石調パネルの粒、内部の羽根・保護格子・板継ぎ目は接写用に再構成しました。航空測量から設備のメーカーや内部機構を特定することはできません。

元資料は [東京都の公開案内](https://www.metro.tokyo.lg.jp/information/press/2024/10/2024103126)、[区部点群データ](https://www.geospatial.jp/ckan/dataset/tokyopc-23ku-2024)、[LAS](https://gic-tokyo.s3.ap-northeast-1.amazonaws.com/2024/dig/lp/09LD1884.zip)、[簡易オルソ](https://gic-tokyo.s3.ap-northeast-1.amazonaws.com/2024/dig/ortho/09LD1884.zip)、[PLATEAU原タイル](https://assets.cms.plateau.reearth.io/assets/28/07d0a1-b6be-46ef-bd87-4f0683b5ef6e/13101_chiyoda-ku_pref_2025_citygml_1_op_bldg_3dtiles_13101_chiyoda-ku_lod2/data/data454.b3dm)、[PLATEAU利用規約](https://www.mlit.go.jp/plateau/site-policy/)、[三菱地所の建物概要](https://office.mec.co.jp/search/detail/011408/) で確認できます。

2026年11月19日に大丸有データライブラリの「ロボット走行環境形成 点群データ_実測」も確認しました。ロボットが走行した丸の内仲通りアーバンテラスとビル内通路を対象にした2022年の2.3 MiB ZIPです。当日は運営者のセキュリティ対策に伴う配信停止で、リソースとZIPがHTTP 503を返したため、モデルに使った点群は上記の東京都航空測量です。[OMYの実測点群カタログ](https://data.tokyo-omy-w.jp/en/dataset/tengundata-jissoku/resource/893072cd-39b7-4441-96de-c1c1d3e8cf4d)

## 再生成

リポジトリのルートで実行します。2番目のコマンドでBlender自身がPLATEAUの元タイルから二方向のガラステクスチャを生成し、窓と設備をメッシュにして、4枚のPNGをGLBに埋め込みます。再生成に使ったBlenderは5.2.2 LTSです。

```sh
uv run tools/prepare-marunouchi.py --work /tmp/marunouchi-nichome
blender --background --factory-startup --python-exit-code 1 --python tools/build-marunouchi.py -- --work /tmp/marunouchi-nichome
```

東京都ZIPのSHA-256はLASが `011d8c0421094b817a8db84c4cc08f0832f2d60e6fc13d47c631552a70746be7`、簡易オルソが `a60d5b24106d157ff730f68c17c32084350a5442161ca10bbf510e665cefeb21` です。実行時のハッシュ、各設備に用いた点数、位置・高さは作業フォルダの `roof.json` に記録されます。

GLBは4メッシュ、15プリミティブ、22,568三角形です。Three.jsでは東が+X、高さが+Y、北が−Zです。Blenderでも通常のglTFインポート時の位置・回転を使います。全体を確かめるカメラはThree.jsで `position [-344,88,180]`、`target [-268,30,130]`、円形設備の近景は `position [-286,61,152]`、`target [-261.5,42,139.5]` です。
