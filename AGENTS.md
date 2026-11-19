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
- [ ] 分割前の全景を `/tmp/tokyo-before-all-buildings.blend` に保管し、`just audit-buildings /tmp/tokyo-before-all-buildings.blend` が成功することを確認する。比較用ファイルは分割前のコミット `046444a` の別チェックアウトで `just build-blender-mcp` を実行しても生成できる。
- [ ] 貼り付け後、全棟版の全周8方位と3区の代表棟の接写をSolが確認する。大丸有22棟は `node tools/render.mjs buildings /tmp/tokyo-buildings` で各棟を8方位から撮影し、屋根・隣棟の誤投影、継ぎ目、色ずれ、窓列を点検する。指摘は同じ画角で修正前後を比較し、再レビューで収束を確認する。
- [ ] `node tools/render.mjs underground /tmp/tokyo-underground` で各階、天井の表示切替、床と壁の近景を確認する。`just review` で都市全周・低高度・俯瞰を撮り、空・雲・太陽、深度フォグ、フレア、道路、地面、草、樹木・葉、窓ガラス、水面を実際の画面で確認する。

## 動画と公開

- [ ] 最終モデルから `just render` で `output/tokyo-station.mp4` を作る。Solが完成MP4の全カットと切替付近、接写のちらつき・遮蔽を確認する。現行版は1920×1080、30 fps、39.5秒。フレアの初期状態はオン。
- [ ] `main` 反映後、対象コミットのGitHub Pagesデプロイ成功を `gh run` で確認する。公開URLから3Dビューア、棟別・地下街表示、MP4、編集用Blenderファイルを開き、SNSカードの画像とGitHubリポジトリのWebsite欄も確認する。
- [ ] Pagesから全棟版のGitHub Releaseアセットをダウンロードし、ローカルで再読込・レビューした `tokyo-station.blend` とSHA-256を照合する。
