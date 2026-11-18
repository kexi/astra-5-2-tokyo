# 鉄道の加工データ

[個別軌道の中心線](rail-tracks.json)はOpenStreetMapから範囲・構造種別を絞り込んで作成しました。© OpenStreetMap contributors。中心線の加工データは[Open Data Commons Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/)で提供します。 [OpenStreetMapの著作権とライセンス](https://www.openstreetmap.org/copyright)

[支持面の標高推定](survey-rail.json)はOpenStreetMapの中心線と、東京都デジタルツイン実現プロジェクトの区部航空レーザ点群の09LD1884図郭を加工して作成しました。東京都の点群はCC BY 4.0です。支持面の測位と中心線の組合せはODbL 1.0で提供します。 [東京都の配布案内](https://www.metro.tokyo.lg.jp/information/press/2024/10/2024103126)

[レールの3D形状](rail-model.glb)には、測量点列から推定した支持面、軌間、中心線に基づくレール、まくらぎ、路盤を含めています。路盤側面とまくらぎは測量データに合わせた再構成です。

[平面参照データ](rail-reference.json)は国土地理院の鉄道中心線と国土交通省の国土数値情報 N02・2025年度（CC BY 4.0）を加工して作成しました。ジオイド高は国土地理院の測量計算APIで取得しました。 [地理院の鉄道中心線](https://github.com/gsi-cyberjapan/experimental_railcl) · [国土数値情報](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N02-2025.html) · [ジオイド高](https://vldb.gsi.go.jp/sokuchi/surveycalc/api_help.html)
