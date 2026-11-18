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

ブラウザで `http://127.0.0.1:8421/` を開きます。開発中は `just serve` でソース版を直接表示できます。初回表示では地形・建物テクスチャを含む約145 MiBのデータを読み込みます。

## 撮影と再構築

```sh
just review
just render
```

撮影にはChromeとFFmpegを使用します。Chromeの場所は `CHROME_BIN` で指定できます。レビューは北から時計回りに45°間隔で周回し、低高度4方位、俯瞰、濠、草、フレア比較、空撮ルートの静止画を保存します。動画は1080p、30 fpsのH.264 MP4です。

都市データを再取得するには `uv run tools/prepare-data.py` を実行します。ImageMagickを使い、公開データから約2.7 km圏のタイルを切り出して圧縮します。個々のPLATEAUタイルの出典URLは `assets/manifest.json` に収録しています。

## 自動デプロイ

GitHub Actionsは `main` へのpushで配布版をビルドし、GitHub Pagesへデプロイします。PRでは同じビルドと静的検査を実行します。tsdownはThree.jsとアプリのJavaScriptをバンドルし、minifyします。3D素材と動画はビルド時に `dist/` へコピーします。

## データ出典

- 建築物・道路・水域・植生は国土交通省 Project PLATEAUの千代田区・中央区・港区の2025年度データを加工して作成。建物の写真テクスチャはタイルのUVを保持し、距離に応じて解像度を調整しています。 [PLATEAU配信サービス](https://docs.plateauview.mlit.go.jp/datasets/3d-tiles/) · [利用規約](https://www.mlit.go.jp/plateau/site-policy/)
- 航空写真と標高は国土地理院の地理院タイル「全国最新写真（シームレス）」と「標高タイル」を加工して作成。航空写真の色から抽出した緑地に手続き生成の樹冠を補っています。 [地理院タイル一覧](https://maps.gsi.go.jp/help/use.html) · [出典の記載](https://www.gsi.go.jp/LAW/2930-meizi.html)
- 雲は64×64×64の3Dノイズテクスチャで生成。太陽、道路、地面、草、建物、水面の素材と、視点からの深度・高度・太陽の方位に応じたフォグを適用しています。
