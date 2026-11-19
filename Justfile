# 利用できるコマンドを表示する
default:
    @just --list

# ローカルの Three.js ビューアを起動する
serve port="8421":
    python3 -m http.server {{ port }} --bind 127.0.0.1

# 360度のレビュー画像を出力する
review dir="review/current":
    node tools/render.mjs review {{ dir }}

# 空撮動画を MP4 に書き出す
render:
    node tools/render.mjs film output/tokyo-station.mp4

# ワークフロー、タスク定義、シークレット混入を検査する
check:
    just check-web
    just check-python
    just --fmt --check
    actionlint
    pinact run --check --fix=false --no-api
    gitleaks dir --redact --no-banner --max-target-megabytes 5 .

# WebコードをOxfmtで整形する
format-web:
    pnpm run format

# Webコードの整形と静的解析を検査する
check-web:
    pnpm run format:check
    pnpm run lint

# Pythonツールのリントと整形を検査する
check-python:
    ruff check tools
    ruff format --check tools

# 棟専用のテクスチャを焼く前の基礎モデルをBlenderで生成する
build-blender:
    node tools/export-flight.mjs output/.flight-timeline.json
    "{{ env_var_or_default("BLENDER_BIN", "/Applications/Blender.app/Contents/MacOS/Blender") }}" --background --factory-startup --python-exit-code 1 --python tools/create-blender.py -- --flight output/.flight-timeline.json --export-buildings assets/omy-buildings.glb

# Blender Lab MCPで全棟にテクスチャを貼り、都市・棟別の配信用データを生成する
build-blender-mcp:
    node tools/export-flight.mjs output/.flight-timeline.json
    uv run tools/build-with-blender-mcp.py --texture-export output/building-paint
    uv run tools/prepare-building-web.py --stage output/building-paint

# テクスチャ制作前の棟別形状を開き直し、分割前のシーンとPLATEAUの原典を照合する
audit-buildings baseline model="output/tokyo-station.blend":
    "{{ env_var_or_default("BLENDER_BIN", "/Applications/Blender.app/Contents/MacOS/Blender") }}" --background "{{ model }}" --python-exit-code 1 --python tools/audit-blender-buildings.py -- --baseline "{{ baseline }}"

# 保存した全棟の形状、専用画像の画素とUV、面の分類を配信用の書き出しと照合する
audit-paint baseline stage="output/building-paint" model="output/tokyo-station.blend":
    "{{ env_var_or_default("BLENDER_BIN", "/Applications/Blender.app/Contents/MacOS/Blender") }}" --background "{{ model }}" --python-exit-code 1 --python tools/audit-building-paint.py -- --baseline "{{ baseline }}" --stage "{{ stage }}"

# 貼付済みの全建物を8方位と俯瞰から描き、建物IDで検索できる索引を作る
review-buildings dir model="output/tokyo-station.blend":
    "{{ env_var_or_default("BLENDER_BIN", "/Applications/Blender.app/Contents/MacOS/Blender") }}" --background "{{ model }}" --python-exit-code 1 --python tools/review-building-textures.py -- "{{ dir }}"
    uv run tools/index-building-reviews.py "{{ dir }}"
