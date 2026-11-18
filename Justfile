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
    just --fmt --check
    actionlint
    pinact run --check --fix=false --no-api
    gitleaks dir --redact --no-banner --max-target-megabytes 5 .
