# 東京駅上空のチェック項目

変更した内容に該当する項目を確認する。制作手順は [README](README.md)、使用したデータは [出典と加工範囲](assets/OMY-SOURCES.md)、実際に見つかった問題と修正後の画像は [レビュー記録](review/README.md) を参照する。

## コードとビルド

- [ ] Pythonの実行スクリプトにPEP 723の `# /// script` ブロックを置き、`requires-python` と `dependencies` を実際の環境に合わせる。データ処理には `uv run`、`bpy` を使う制作処理にはBlender同梱のPythonを使う。
- [ ] `just check-python` でRuffのリントと整形を、`just check-web` でOxfmtの整形とOxlintの静的解析を検査する。
- [ ] 公開前に `pnpm install --frozen-lockfile`、`just check`、`pnpm run build` が通り、tsdownのminify済み配布版がPagesのサブパスで動くことを確認する。

## Blenderとテクスチャ

- [ ] Astraが公開データを建物ID・位置・LOD・撮影面と照合する。測量した形状、元の外装写真、推定した仕上げの範囲を出典に記録する。
- [ ] Blender Lab MCPで制作・保存する。再生成には `just build-blender-mcp` を使う。都市全体と配布用の両方の `.blend` を別プロセスで開き、画像の埋め込み、メッシュ、材質、UV、初期視点を確認する。
- [ ] 元タイルから外形を分離した場合、建物IDごとの三角形数と写真付き面数を照合する。都市全体で元の形状と重なっていないこと、補修対象外の屋上や保存棟のUV・材質が保たれていることを確認する。
- [ ] 貼り付け後、`node tools/render.mjs buildings /tmp/tokyo-buildings` で各棟を8方位から撮影する。Solが屋根・隣棟の誤投影、継ぎ目、色ずれ、窓列を点検する。指摘は同じ画角で修正前後を比較し、再レビューで収束を確認する。
- [ ] `node tools/render.mjs underground /tmp/tokyo-underground` で各階、天井の表示切替、床と壁の近景を確認する。`just review` で都市全周・低高度・俯瞰を撮り、空・雲・太陽、深度フォグ、フレア、道路、地面、草、樹木・葉、窓ガラス、水面を実際の画面で確認する。

## 動画と公開

- [ ] 最終モデルから `just render` で `output/tokyo-station.mp4` を作る。Solが完成MP4の全カットと切替付近、接写のちらつき・遮蔽を確認する。現行版は1920×1080、30 fps、39.5秒。フレアの初期状態はオン。
- [ ] `main` 反映後、対象コミットのGitHub Pagesデプロイ成功を `gh run` で確認する。公開URLから3Dビューア、棟別・地下街表示、MP4、編集用Blenderファイルを開き、SNSカードの画像とGitHubリポジトリのWebsite欄も確認する。
