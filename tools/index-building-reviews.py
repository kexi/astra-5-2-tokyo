# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Create an offline index and review queue for building texture screenshots."""

import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

WARDS = {"chiyoda": "千代田区", "chuo": "中央区", "minato": "港区"}

PAGE = """<!doctype html>
<html lang="ja">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>建物テクスチャのレビュー</title>
<style>
  body{margin:0 auto;padding:1.5rem 1rem;max-width:96rem;background:#181e23;color:#f3f4f5;font:16px system-ui}
  h1{margin-top:0}a{color:#a5d6ff}a:hover{color:#fff}button,input,select{font:inherit;color:inherit;background:#303a44;padding:.35rem .55rem;border:1px solid #788691;border-radius:.25rem}
  nav{display:flex;flex-wrap:wrap;gap:.85rem;margin-block:1rem;align-items:center}input{max-width:80vw}
  #list{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(min(100%,19rem),1fr))}
  article{background:#222b32;padding:.75rem}article h2{font-size:1rem;margin:0 0 .35rem}
  article p{font-size:.875rem;color:#d0d9e0;margin-block:.35rem}
  figure{margin:.7rem 0 0}img{display:block;max-width:100%;width:100%;height:auto}
  figcaption{font-size:.875rem;margin-top:.35rem}
  .paired{display:grid;gap:.45rem;grid-template-columns:repeat(2,minmax(0,1fr))}
  #player{margin:1rem 0;padding:1rem;background:#222b32}#player h2{margin:0 0 .6rem}
  #view-frame{width:min(100%,24rem);aspect-ratio:1;overflow:hidden;background:#1b2025}
  #turntable{max-width:none;transform-origin:top left}
  #source{color:#d0d9e0;overflow-wrap:anywhere;font-size:.875rem}
  #validation{margin:.75rem 0}#validation li{margin-block:.4rem}
  :focus-visible{outline:2px solid #faf67d;outline-offset:3px}
</style>
<header>
  <h1>建物テクスチャのレビュー</h1>
  <p id="summary"></p>
  <p>画像は左上から北を0°として時計回りの8方位と俯瞰です。色はベースカラーを表示しています。リンク先で9コマを開けます。推定・補完には、撮影写真がない面の仕上げと、航空写真から補った屋根が含まれます。</p>
  <p id="source"></p>
  <details id="validation" hidden><summary>機械順位の検証と目視記録</summary></details>
  <p><a href="review-queue.csv">優先確認リスト</a> · <a href="candidates.csv">全棟の数値</a> · <a href="views.jsonl">各方位の数値</a> · <a href="report.json">検査条件</a></p>
</header>
<main>
  <section id="player" hidden aria-labelledby="view-title">
    <h2 id="view-title"></h2>
    <div id="view-frame"><img id="turntable" alt="選択した棟の回転画像"></div>
    <nav aria-label="方位の操作">
      <button id="play" type="button">再生</button>
      <button id="west" type="button">前の方位</button>
      <button id="east" type="button">次の方位</button>
      <button id="above" type="button">俯瞰</button>
      <output id="bearing" aria-live="polite"></output>
    </nav>
  </section>
  <nav aria-label="絞り込みとページ">
    <label>棟ID・名称 <input id="query" type="search" placeholder="13101-bldg-…" spellcheck="false"></label>
    <label>区 <select id="ward"><option value="">全区</option><option value="chiyoda">千代田区</option><option value="chuo">中央区</option><option value="minato">港区</option></select></label>
    <label>確認対象 <select id="scope"><option value="queue">優先確認</option><option value="all">全棟</option><option value="errors">描画エラー</option></select></label>
    <button id="prev" type="button">前へ</button><button id="next" type="button">次へ</button>
    <output id="count" aria-live="polite"></output>
  </nav>
  <section id="list" aria-label="建物ごとの比較"></section>
</main>
<script src="review-index-data.js"></script>
<script>
const size = 24;
const query = document.getElementById("query");
const ward = document.getElementById("ward");
const scope = document.getElementById("scope");
const count = document.getElementById("count");
const listing = document.getElementById("list");
const previous = document.getElementById("prev");
const next = document.getElementById("next");
let page = 0;
const heading = document.getElementById("summary");
heading.textContent = `${report.buildings_rendered.toLocaleString()} 棟、${report.views_rendered.toLocaleString()} 視点を描画。確認候補をスコア順に表示します。`;
const requested = new URLSearchParams(location.search).get("id");
if (requested) query.value = requested;
const proof = document.getElementById("source");
proof.textContent = `${report.build || "入力"} · SHA-256 ${report.source_sha256}。確認スコアは候補の優先順です。`;
if (report.blender_sha256) {
  proof.append(document.createElement("br"), `BlenderのSHA-256 ${report.blender_sha256}`);
}
if (report.supersampling) {
  const groups = Object.entries(report.supersampling.buildings_by_factor)
    .map(([factor, buildings]) => `${factor}倍 ${buildings.toLocaleString()}棟`).join(" · ");
  proof.append(document.createElement("br"), `各方位${report.size_per_view_px}px。描画倍率は各軸 ${groups}。反復する細い外装は近接資料でも確認してください。`);
}
if (report.screening_validation) {
  const check = report.screening_validation;
  const validation = document.getElementById("validation");
  const calibration = document.createElement("a");
  calibration.href = "screening-validation.json";
  calibration.textContent = "各棟の目視ラベルと旧版の順位（JSON）";
  if (check.groups) {
    const list = element("ul");
    for (const group of check.groups) {
      const counts = group.counts;
      const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
      list.append(element("li", `${group.name} ${total}棟、目視確証${counts.confirmed}棟、保留${counts.uncertain}棟、明確な流入を確認できず${counts.not_seen}棟、元写真なし${counts.no_source_photo}棟。確証のうち旧版の上位10%に${group.hits["10"]}棟、上位50%に${group.hits["50"]}棟。${group.scope}`));
    }
    const visual = element("a", "Astraの目視査読記録");
    visual.href = check.visual_review.url;
    validation.append(element("p", check.rank_basis.description), list,
      element("p", check.visual_review.description), calibration, " · ", visual);
    for (const [label, url] of [
      ["第1群の判定表", check.visual_review.first_group_csv],
      ["第2～4群の判定表", check.visual_review.additional_groups_csv],
    ]) {
      if (!url) continue;
      const decisions = element("a", label);
      decisions.href = url;
      validation.append(" · ", decisions);
    }
  } else {
    validation.append(element("p", `旧版の低層${check.total.toLocaleString()}棟で、独立標本の既知${check.confirmed}件のうちスコア上位10%に入ったのは${check.top_ten}件でした。`), calibration);
  }
  validation.hidden = false;
}
const selected = requested && rows.find(row => row.id === requested && row.image);
if (selected) {
  const player = document.getElementById("player");
  const turntable = document.getElementById("turntable");
  const play = document.getElementById("play");
  const bearing = document.getElementById("bearing");
  let frame = 0;
  let clock = null;
  turntable.src = selected.image;
  turntable.alt = `${selected.id}の選択方位`;
  turntable.width = turntable.height = report.pixels;
  turntable.style.width = `${100 * report.pixels / report.size_per_view_px}%`;
  document.getElementById("view-title").textContent = [selected.id, selected.name].filter(Boolean).join("　");
  player.hidden = false;
  function show(index) {
    frame = index;
    const fraction = 100 * report.cell_stride / report.pixels;
    turntable.style.transform = `translate(${-fraction * (frame % 3)}%,${-fraction * Math.floor(frame / 3)}%)`;
    bearing.textContent = frame === 8 ? "俯瞰" : `${45 * frame}°`;
  }
  function stop() {
    clearInterval(clock);
    clock = null;
    play.textContent = "再生";
    bearing.setAttribute("aria-live", "polite");
  }
  for (const [id, move] of [["west", -1], ["east", 1], ["above", 0]]) {
    document.getElementById(id).addEventListener("click", () => {
      stop();
      show(move ? (frame === 8 ? (move > 0 ? 0 : 7) : (frame + move + 8) % 8) : 8);
    });
  }
  play.addEventListener("click", () => {
    if (clock) {
      stop();
      return;
    }
    play.textContent = "一時停止";
    bearing.setAttribute("aria-live", "off");
    if (frame === 8) show(0);
    clock = setInterval(() => show((frame + 1) % 8), 390);
  });
  show(0);
}
function element(tag, content) {
  const node = document.createElement(tag);
  if (content) node.textContent = content;
  return node;
}
function screenshot(row, file, pixels, title, lazy) {
  const figure = element("figure");
  const link = element("a");
  link.href = file;
  const image = element("img");
  image.src = file;
  image.alt = `${row.id} ${title}の9視点`;
  image.width = image.height = pixels;
  image.loading = lazy ? "lazy" : "eager";
  image.decoding = "async";
  link.append(image);
  figure.append(link, element("figcaption", title));
  return figure;
}
function card(row, index) {
  const article = element("article");
  article.id = row.id;
  const title = element("a", row.name ? `${row.id}　${row.name}` : row.id);
  title.href = "?id=" + encodeURIComponent(row.id);
  const header = element("h2");
  header.append(title);
  const height = row.height_m === null ? "" : ` · 高さ ${row.height_m.toFixed(1)} m`;
  article.append(header, element("p", `${row.ward_label} · LOD${row.lod} · ${row.tile}${height}`));
  if (row.error) {
    article.append(element("p", row.error));
    return article;
  }
  const hint = row.bearing === null ? "" : ` · ${row.bearing}${typeof row.bearing === "number" ? "°" : ""}付近`;
  article.append(element("p", `確認スコア ${row.score.toFixed(3)}${hint}`));
  const kinds = row.paint_kinds || {};
  const inferred = (kinds[1] || 0) + (kinds[2] || 0) + (kinds[3] || 0);
  article.append(element("p", `写真由来（既存補修含む）${kinds[0] || 0} 面 · 今回の補修 ${kinds[4] || 0} 面 · 推定・補完 ${inferred} 面`));
  if (row.reasons.length) article.append(element("p", row.reasons.join(" · ")));
  const images = element("div");
  if (row.before) {
    images.className = "paired";
    images.append(screenshot(row, row.before, report.before_pixels, "修正前", index >= 3));
  }
  images.append(screenshot(row, row.image, report.pixels, row.before ? "修正後" : "9視点", index >= 3));
  article.append(images);
  return article;
}
function update() {
  const term = query.value.trim().toLowerCase();
  const exact = term && rows.find(row => row.id === term);
  const matches = (exact ? [exact] : rows).filter(row =>
    (!term || row.id.includes(term) || row.name.toLowerCase().includes(term)) &&
    (!ward.value || row.ward === ward.value) &&
    (scope.value === "all" || (scope.value === "errors" ? row.error : (term || row.queue))));
  const pages = Math.max(1, Math.ceil(matches.length / size));
  page = Math.min(page, pages - 1);
  count.textContent = `${matches.length.toLocaleString()} 棟　${page + 1} / ${pages}`;
  previous.disabled = page === 0;
  next.disabled = page + 1 >= pages;
  listing.replaceChildren(...matches.slice(page * size, (page + 1) * size).map(card));
}
for (const input of [query, ward, scope]) input.addEventListener("input", () => { page = 0; update(); });
previous.addEventListener("click", () => { page -= 1; update(); });
next.addEventListener("click", () => { page += 1; update(); });
update();
</script>
</html>
"""


def read_measurements(folder):
    with (folder / "views.jsonl").open(encoding="utf-8") as stream:
        records = {record["id"]: record for record in map(json.loads, stream)}
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    if len(records) != report["buildings_expected"]:
        raise ValueError(f"{folder}: 集計と棟数が一致しません")
    return records, report


def shortlist(rows, maximum):
    selected = {}
    for record in rows[:maximum]:
        selected[record["id"]] = "全体の優先確認"
    layers = defaultdict(list)
    for record in rows:
        if record.get("error"):
            selected[record["id"]] = "描画エラー"
            continue
        kinds = record["paint_kinds"]
        coverage = (
            "photo-only"
            if list(kinds) == ["0"]
            else "mixed"
            if "0" in kinds
            else "inferred"
        )
        height = record.get("height_m", 0)
        height_group = (
            "60m以上" if height >= 60 else "20–60m" if height >= 20 else "20m未満"
        )
        layers[(record["ward"], record["lod"], coverage, height_group)].append(record)
    for records in layers.values():
        for position in (
            0,
            len(records) // 4,
            len(records) // 2,
            len(records) * 3 // 4,
            len(records) - 1,
        ):
            selected.setdefault(
                records[position]["id"], "区・高さ・LOD・面由来ごとの抽出"
            )
    return selected


def javascript(value):
    # Official names are data even if a name contains HTML syntax.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )


def write_queue(folder, rows, selected):
    fields = (
        "id",
        "name",
        "ward_label",
        "tile",
        "lod",
        "score",
        "bearing",
        "reasons",
        "selection",
        "image",
        "before",
        "error",
    )
    with (folder / "review-queue.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        table = csv.DictWriter(stream, fields, extrasaction="ignore")
        table.writeheader()
        for row in rows:
            if row["id"] in selected:
                table.writerow(
                    dict(
                        row,
                        selection=selected[row["id"]],
                        reasons=" / ".join(row["reasons"]),
                    )
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--queue", type=int, default=180, help="優先スコア順の確認数")
    args = parser.parse_args()
    current = args.current.resolve()
    records, report = read_measurements(current)
    older, previous = read_measurements(args.before) if args.before else ({}, {})
    if args.before and not records.keys() <= older.keys():
        raise ValueError("修正前の記録に対応する棟IDがありません")
    rows = sorted(records.values(), key=lambda row: (-row["score"], row["id"]))
    for row in rows:
        row["ward"] = row["tile"].split("-", 1)[0]
        row["ward_label"] = WARDS[row["ward"]]
        earlier = older.get(row["id"], {}).get("image")
        if earlier:
            row["before"] = quote(os.path.relpath(args.before / earlier, current))
    selected = shortlist(rows, args.queue)
    write_queue(current, rows, selected)
    gutter = report.get("gutter_px", 2)
    report["pixels"] = report["size_per_view_px"] * 3 + gutter * 2
    report["cell_stride"] = report["size_per_view_px"] + gutter
    report["before_pixels"] = (
        previous.get("size_per_view_px", 96) * 3 + previous.get("gutter_px", 2) * 2
    )
    public_fields = (
        "id",
        "name",
        "ward",
        "ward_label",
        "lod",
        "tile",
        "score",
        "bearing",
        "reasons",
        "image",
        "before",
        "error",
        "paint_kinds",
        "height_m",
    )
    items = [
        dict(
            {field: row.get(field) for field in public_fields},
            queue=row["id"] in selected,
        )
        for row in rows
    ]
    (current / "review-index-data.js").write_text(
        f"const report = {javascript(report)};\nconst rows = {javascript(items)};\n",
        encoding="utf-8",
    )
    (current / "index.html").write_text(PAGE, encoding="utf-8")
    print(f"{len(rows):,}棟 · 確認候補 {len(selected):,}棟 · {current / 'index.html'}")


if __name__ == "__main__":
    main()
