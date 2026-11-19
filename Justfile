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

# 都市全体・大丸有のBlenderファイルと棟別外装のGLBを生成する
build-blender:
    node tools/export-flight.mjs output/.flight-timeline.json
    "{{ env_var_or_default("BLENDER_BIN", "/Applications/Blender.app/Contents/MacOS/Blender") }}" --background --factory-startup --python-exit-code 1 --python tools/create-blender.py -- --flight output/.flight-timeline.json --export-buildings assets/omy-buildings.glb

# Blender LabのMCPを通じて同じ編集用ファイルとGLBを生成する
build-blender-mcp:
    node tools/export-flight.mjs output/.flight-timeline.json
    uv run tools/build-with-blender-mcp.py
