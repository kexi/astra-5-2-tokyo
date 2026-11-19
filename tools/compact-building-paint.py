# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
# ///
"""Repack building photographs losslessly, then reopen the .blend and verify every RGBA sample.

Run with Blender's Python: blender -b source.blend -P tools/compact-building-paint.py
-- output.blend --cache /tmp/tokyo-lossless. Both jpegtran and cwebp must be on PATH.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def save_bytes(path, data):
    fd, temp = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
    os.replace(temp, path)


def encode(source, cache):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    folder = cache / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    key = folder / f"{digest}.json"
    if key.is_file():
        suffix = json.loads(key.read_text())["extension"]
        dest = folder / (digest + suffix)
        if dest.is_file():
            return dest
    with tempfile.TemporaryDirectory(prefix=".encoding-", dir=folder) as temp:
        paths = [source]
        if source.suffix == ".jpg":
            for options, name in (
                ([], "sequential.jpg"),
                (["-progressive"], "progressive.jpg"),
            ):
                output = Path(temp) / name
                subprocess.run(
                    [
                        "jpegtran",
                        "-copy",
                        "comments",
                        "-arithmetic",
                        *options,
                        "-outfile",
                        output,
                        source,
                    ],
                    check=True,
                    capture_output=True,
                )
                paths.append(output)
        elif source.suffix == ".png":
            output = Path(temp) / "lossless.webp"
            subprocess.run(
                [
                    "cwebp",
                    "-quiet",
                    "-lossless",
                    "-m",
                    "5",
                    "-exact",
                    source,
                    "-o",
                    output,
                ],
                check=True,
                capture_output=True,
            )
            paths.append(output)
        best = min(paths, key=lambda p: p.stat().st_size)
        dest = folder / (digest + best.suffix)
        save_bytes(dest, best.read_bytes())
        save_bytes(key, json.dumps({"extension": best.suffix}).encode())
        return dest


def pixel_digest(image):
    import numpy as np

    width, height = image.size
    if min(width, height) < 1:
        raise ValueError(f"Source image is unreadable: {image.name}")
    pixels = np.empty(len(image.pixels), "f4")
    image.pixels.foreach_get(pixels)
    # Blender bundles its own OpenSSL. Hashing large float RGBA buffers
    # with BLAKE2b avoids depending on its platform SHA acceleration.
    digest = hashlib.blake2b(pixels.view("u1"), digest_size=32).hexdigest()
    image.buffers_free()
    return width, height, digest


def decoded_rgb(source):
    import OpenImageIO as oiio

    reader = oiio.ImageInput.open(str(source))
    if reader is None:
        return None
    try:
        return reader.read_image(oiio.UINT8)
    finally:
        reader.close()


def candidate_for(source, cache):
    import numpy as np

    candidate = encode(source, cache)
    # Comparing the candidate here lets the original packed bytes survive a
    # decoder rejection. The complete saved model is also decoded in Blender.
    original = decoded_rgb(source)
    replacement = decoded_rgb(candidate)
    if (
        original is None
        or replacement is None
        or not np.array_equal(original, replacement)
    ):
        return source
    return candidate


def run(args):
    import bpy

    dest = args.output.resolve()
    if not bpy.data.filepath or Path(bpy.data.filepath).resolve() == dest:
        raise ValueError("Specify a distinct, saved input .blend and output .blend")
    for tool in ("jpegtran", "cwebp"):
        if not shutil.which(tool):
            raise FileNotFoundError(tool)
    dest.parent.mkdir(exist_ok=True, parents=True)
    group = bpy.data.collections["01  PLATEAU・建築物"]
    ids = sorted({o["id"] for o in group.all_objects if o.get("textureVersion")})
    if not ids:
        raise ValueError("No individually painted buildings found")
    args.cache.mkdir(exist_ok=True, parents=True)
    formats = Counter()
    with tempfile.TemporaryDirectory(dir=args.cache, prefix="packed-source-") as work:
        sources = []
        for identifier in ids:
            im = bpy.data.images[identifier]
            if not im.packed_file:
                raise ValueError(f"Unpacked building image {identifier}")
            ext = Path(im.filepath).suffix.lower()
            path = Path(work) / (identifier + ext)
            path.write_bytes(im.packed_file.data)
            sources.append(path)
        print(json.dumps({"extracted": len(sources)}), flush=True)
        checks = {}
        totals = Counter()
        with ThreadPoolExecutor(args.workers) as workers:
            results = workers.map(lambda p: candidate_for(p, args.cache), sources)
            for n, (identifier, source, encoded) in enumerate(
                zip(ids, sources, results, strict=True), 1
            ):
                im = bpy.data.images[identifier]
                prior = pixel_digest(im)
                if encoded == source:
                    totals["keptOriginal"] += 1
                else:
                    data = encoded.read_bytes()
                    label = str(Path(im.filepath).with_suffix(encoded.suffix))
                    # Source atlases were originally loaded from ephemeral paths.
                    # Supply the real file during the repack, then store the ID
                    # in both editable and embedded path fields.
                    im.packed_files[0].filepath = str(encoded)
                    im.filepath_raw = label
                    im.pack(data=data, data_len=len(data))
                    im.packed_files[0].filepath = label
                    if len(im.packed_files) != 1 or im.packed_file.data != data:
                        raise ValueError(f"Blender did not pack {identifier}")
                formats[Path(im.filepath).suffix] += 1
                checks[identifier] = prior
                totals["originalBytes"] += source.stat().st_size
                totals["packedBytes"] += im.packed_file.size
                if n % 500 == 0:
                    print(json.dumps({"verified": n, **totals}), flush=True)
    with tempfile.TemporaryDirectory(prefix=".compact-", dir=dest.parent) as work:
        # Keep linked path resolution in the output directory while validating.
        partial = dest.parent / (Path(work).name + ".blend")
        try:
            bpy.context.preferences.filepaths.save_version = 0
            bpy.ops.wm.save_as_mainfile(
                filepath=str(partial), compress=True, relative_remap=False
            )
            bpy.ops.wm.open_mainfile(filepath=str(partial))
            for n, (identifier, expected) in enumerate(checks.items(), 1):
                image = bpy.data.images[identifier]
                if not image.packed_file or pixel_digest(image) != expected:
                    raise ValueError(f"Saved RGBA mismatch for {identifier}")
                if n % 2000 == 0:
                    print(json.dumps({"reopened": n}), flush=True)
            partial.replace(dest)
        finally:
            partial.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "output": str(dest),
                "verifiedAfterReopen": len(checks),
                "packedFormats": dict(formats),
                "bytes": dest.stat().st_size,
                **totals,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=5)
    run(parser.parse_args(sys.argv[sys.argv.index("--") + 1 :]))
