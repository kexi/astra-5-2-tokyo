# 東京駅上空のチェック項目

変更した内容に該当する項目を確認する。制作手順は [README](README.md)、使用したデータは [出典と加工範囲](assets/OMY-SOURCES.md)、実際に見つかった問題と修正後の画像は [レビュー記録](review/README.md) を参照する。

## コードとビルド

- [ ] Pythonの実行スクリプトにPEP 723の `# /// script` ブロックを置き、`requires-python` と `dependencies` を実際の環境に合わせる。データ処理には `uv run`、`bpy` を使う制作処理にはBlender同梱のPythonを使う。
- [ ] `just check-python` でRuffのリントと整形を、`just check-web` でOxfmtの整形とOxlintの静的解析を検査する。
- [ ] 公開前に `pnpm install --frozen-lockfile`、`just check`、`pnpm run build` が通り、tsdownのminify済み配布版がPagesのサブパスで動くことを確認する。

## Blenderとテクスチャ

- [ ] Astraが公開データを建物ID・位置・LOD・撮影面と照合する。測量した形状、元の外装写真、推定した仕上げの範囲を出典に記録する。
- [ ] Blender Lab MCPで制作・保存する。再生成には `just build-blender-mcp` を使う。都市全体と配布用の両方の `.blend` を別プロセスで開き、画像の埋め込み、メッシュ、材質、UV、初期視点を確認する。
- [ ] macOSでBlenderが起動直後にSIGSEGVで終了した場合、同じ実行ファイルを `--background --factory-startup --python-expr "import bpy; print(bpy.app.version_string)"` で単独起動する。空のシーンの起動、MCP接続、生成後の再読込を段階ごとに確認し、どの段階で再現したかを記録する。
- [ ] 公式b3dmと配信用GLBを建物タイル全件で比較する。`uv run tools/audit-building-textures.py --fetch-missing --batch-index /tmp/tokyo-building-batches.json.gz` と `cmp /tmp/tokyo-building-batches.json.gz tools/building-batches.json.gz` で、全建物ID、タイル内の地物番号、棟別の写真付き・写真なし三角形数を検査する。取得した原典は次のBlender監査でも使う。
- [ ] 保存後の全棟版を再読込し、監査CSVの全23,115件が各1個の編集可能なメッシュに対応すること、元タイルの重複・欠落がないことを確認する。建物ごとの世界座標、UV、法線、材質と画像の埋め込みを照合し、16ビットの地物番号を持つ中央区の6タイルと、写真のないLOD1の495棟も対象にする。
- [ ] 基礎形状を照合するには、分割前の全景とテクスチャ制作前の棟別版を指定して `just audit-buildings /tmp/tokyo-before-all-buildings.blend /tmp/tokyo-station-v1.blend` を実行する。[比較用の棟別版](https://github.com/kexi/astra-5-2-tokyo/releases/download/tokyo-station-buildings-v1/tokyo-station.blend)を使える。分割前の全景はコミット `046444a` から再生成できる。
- [ ] 貼付後は `just audit-paint /tmp/tokyo-station-v1.blend` で、全棟の建物ID、メッシュ、建物専用画像の全画素、ベースカラーの接続、専用UV、面別の出典が一致することを確かめる。オルソ画像が垂直面や下面に貼られていないことを確認する。
- [ ] 実写の有無は建物IDからCityGMLの面と画像の参照先までたどる。`uv run tools/audit-citygml-appearance.py --report /tmp/appearance.json <千代田区のCityGML.zip> <中央区のCityGML.zip>` を実行し、配信上写真のない495棟の原典を検査する。他の棟は `--id` で追加できる。側壁に屋根が見えるときは原典JPEG、専用アトラス、3D表示を見比べて原因を記録する。
- [ ] 全棟に `just review-buildings /tmp/tokyo-turntables` を実行し、方位45°間隔の8枚と俯瞰1枚が全IDで揃うことを確認する。Astraが機械抽出候補に加えて、高層、駅近、各区、写真なしの建物を回転させ、側面の屋上設備・隣棟の誤投影、継ぎ目、色ずれ、窓列を確認する。指摘は建物IDと方位で記録し、同じ画角で修正とAstraの再レビューを繰り返す。全件の機械検査とAstraが目視した件数を別々に報告する。
- [ ] 健全な窓、看板、屋上設備を残せているか、検出規則を決めた後に別の標本で照合する。自動補修の候補には貼付前後を同じ方位で確認し、色の違う矩形が目立つ場合は周囲の塗色と境界を修正する。`textureScreenQuality` の処理画素数と維持画素数を監査CSVに残し、判定を保留した棟は公開ビューアにも明示する。
- [ ] `node tools/render.mjs underground /tmp/tokyo-underground` で各階、天井の表示切替、床と壁の近景を確認する。`just review` で都市全周・低高度・俯瞰を撮り、空・雲・太陽、深度フォグ、フレア、道路、地面、草、樹木・葉、窓ガラス、水面を実際の画面で確認する。

## 動画と公開

- [ ] 最終モデルから `just render` で `output/tokyo-station.mp4` を作る。Astraが完成MP4の全カットと切替付近、接写のちらつき・遮蔽を確認する。現行版は1920×1080、30 fps、39.5秒。フレアの初期状態はオン。
- [ ] `main` 反映後、対象コミットのGitHub Pagesデプロイ成功を `gh run` で確認する。公開URLから3Dビューア、棟別・地下街表示、MP4、編集用Blenderファイルを開き、SNSカードの画像とGitHubリポジトリのWebsite欄も確認する。
- [ ] Pagesから全棟版のGitHub Releaseアセットをダウンロードし、ローカルで再読込・レビューした `tokyo-station.blend` とSHA-256を照合する。
- [ ] 全棟版を可逆圧縮して配布するときは `tools/compact-building-paint.py` で元と保存後の全棟のRGBAを比較する。配布するファイルを `just audit-paint` の対象にし、レビュー画像ZIPに記録するBlenderのSHA-256も配布ファイルと揃える。
