# /// script
# requires-python = ">=3.11"
# dependencies = ["Pillow==12.0.0"]
# ///
"""Package checked raster views as a browsable, offline WebP ZIP."""

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from PIL import Image


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def convert(row, source, destination, side, gutter, quality):
    relative_png = Path(row["image"])
    if relative_png.is_absolute() or ".." in relative_png.parts:
        raise ValueError(f"{row['id']}: 画像の相対パスが無効です")
    raw = source / relative_png
    actual_hash = file_digest(raw)
    if row.get("render_sha256", actual_hash) != actual_hash:
        raise ValueError(f"{row['id']}: 原寸画像のSHA-256が一致しません")
    image = Image.new("RGB", (3 * side, 3 * side))
    with Image.open(raw) as montage:
        total = 3 * side + 2 * gutter
        if montage.size != (total, total):
            raise ValueError(f"{row['id']}: 9コマの画像サイズが一致しません")
        for number in range(9):
            y, x = divmod(number, 3)
            left, top = x * (side + gutter), y * (side + gutter)
            frame = montage.crop((left, top, left + side, top + side))
            image.paste(frame, (x * side, y * side))
    relative = relative_png.with_suffix(".webp")
    webp = destination / relative
    webp.parent.mkdir(parents=True, exist_ok=True)
    image.save(webp, format="WEBP", quality=quality, method=4, exact=True)
    return dict(
        row,
        image=relative.as_posix(),
        render_sha256=actual_hash,
        archive_image_sha256=file_digest(webp),
    )


def build_zip(directory, output):
    archive_path = directory.parent / (output.name + ".partial")
    with ZipFile(archive_path, "w", allowZip64=False, compresslevel=6) as archive:
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            encoding = ZIP_STORED if path.suffix == ".webp" else ZIP_DEFLATED
            archive.write(
                path, path.relative_to(directory).as_posix(), compress_type=encoding
            )
    if archive_path.stat().st_size >= 2 * 1024**3:
        raise ValueError("ZIPが2 GiB以上です")
    os.replace(archive_path, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--build", required=True, help="レビュー対象の制作ビルド名")
    parser.add_argument(
        "--model", type=Path, help="対応する最終BlenderファイルのSHAを記録"
    )
    parser.add_argument(
        "--calibration", type=Path, help="元版での独立陽性標本と自動スコアの回収集計"
    )
    parser.add_argument("--quality", type=int, default=88)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.quality <= 100 or args.workers < 1:
        parser.error("qualityは1～100、workersは1以上を指定します")
    source = args.review.resolve()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = json.loads((source / "report.json").read_text(encoding="utf-8"))
    with (source / "views.jsonl").open(encoding="utf-8") as stream:
        rows = list(map(json.loads, stream))
    if (
        report["errors"]
        or not len(rows) == report["buildings_expected"] == report["buildings_rendered"]
    ):
        raise ValueError("全対象の描画と集計を完了してからZIP化します")
    if any(
        len(row["views"]) != 9
        or any(view["visible_pixels"] <= 0 for view in row["views"])
        for row in rows
    ):
        raise ValueError("全棟に建物が映る9視点が必要です")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("棟IDが重複しています")
    with tempfile.TemporaryDirectory(prefix="turntables-", dir=output.parent) as temp:
        directory = Path(temp) / "contents"
        directory.mkdir()
        side, gutter = report["size_per_view_px"], report.get("gutter_px", 2)
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            published = list(
                executor.map(
                    lambda row: convert(
                        row, source, directory, side, gutter, args.quality
                    ),
                    rows,
                )
            )
        report["source"] = args.build
        report["build"] = args.build
        report["gutter_px"] = 0
        report["archive_format"] = "WebP"
        report["archive_quality"] = args.quality
        report["original_preview_px"] = 3 * side + 2 * gutter
        if "supersampling" in report:
            report["supersampling"]["method"] = (
                "Area average of evenly spaced, depth-tested RGB samples. "
                "One visible sample supplies the surface classification for each pixel."
            )
        report["review_priority"] = (
            "自動スコアは形状・色・UVとテクスチャの類似から得た優先確認順。最終の目視判定は別途記録。"
        )
        if args.model:
            model_hash = file_digest(args.model)
            if report.get("blender_sha256", model_hash) != model_hash:
                raise ValueError("画像生成時に照合したBlenderファイルと一致しません")
            report["blender_sha256"] = model_hash
        if args.calibration:
            calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
            groups = calibration.get("groups")
            if groups:
                report["screening_validation"] = {
                    "total": calibration["population"],
                    "rank_basis": calibration["rank_basis"],
                    "visual_review": calibration["visual_review"],
                    "groups": [
                        {key: group[key] for key in ("name", "scope", "counts", "hits")}
                        for group in groups
                    ],
                }
            else:
                sample = calibration["sets"]["独立標本の陽性"]
                report["screening_validation"] = {
                    "total": calibration["population"],
                    "confirmed": sample["count"],
                    "top_ten": sample["hits"]["10"],
                }
            (directory / "screening-validation.json").write_text(
                json.dumps(calibration, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        (directory / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with (directory / "views.jsonl").open("w", encoding="utf-8") as stream:
            for row in published:
                stream.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
        with (directory / "candidates.csv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            fields = (
                "id",
                "name",
                "tile",
                "lod",
                "score",
                "bearing",
                "roof_wall_uv_overlap",
                "reasons",
                "render_sha256",
                "archive_image_sha256",
                "image",
            )
            table = csv.DictWriter(stream, fields, extrasaction="ignore")
            table.writeheader()
            for row in sorted(published, key=lambda item: (-item["score"], item["id"])):
                table.writerow(dict(row, reasons=" / ".join(row["reasons"])))
        index = Path(__file__).with_name("index-building-reviews.py")
        subprocess.run([sys.executable, str(index), str(directory)], check=True)
        build_zip(directory, output)
    print(
        f"{output.stat().st_size:,} B  SHA-256 {file_digest(output)}  {output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
