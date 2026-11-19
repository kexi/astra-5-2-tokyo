# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0"]
# ///
"""Build the project in a private Blender process using Blender Lab's MCP server."""

import argparse
import asyncio
import json
import os
import shutil
import socket
import tempfile
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def blender_client(blender, source):
    server = shutil.which("blender-mcp")
    if not server:
        raise SystemExit(
            "Install Blender Lab's blender-mcp server and Blender MCP extension"
        )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    startup = (
        [str(source)]
        if source
        else [
            "--python-expr",
            "import bpy; bpy.ops.wm.read_factory_settings(use_empty=True)",
        ]
    )
    bridge = await asyncio.create_subprocess_exec(
        blender,
        "--background",
        "--factory-startup",
        "--online-mode",
        *startup,
        "--addons",
        "bl_ext.user_default.mcp",
        "--command",
        "blender_mcp",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        cwd=ROOT,
    )
    try:
        # Loading thousands of packed images can take longer than an empty startup.
        for _ in range(720):
            if bridge.returncode is not None:
                raise RuntimeError(f"Blender MCP bridge exited ({bridge.returncode})")
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                await asyncio.sleep(0.25)
                continue
            writer.close()
            await writer.wait_closed()
            break
        else:
            raise TimeoutError("Blender did not start the MCP bridge")
        params = StdioServerParameters(
            command=server,
            args=["--transport", "stdio"],
            env={"BLENDER_MCP_HOST": "127.0.0.1", "BLENDER_MCP_PORT": str(port)},
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(
                reader, writer, read_timeout_seconds=timedelta(minutes=5)
            ) as client,
        ):
            await client.initialize()
            yield client
    finally:
        if bridge.returncode is None:
            bridge.terminate()
        await bridge.wait()


async def execute(client, statements):
    code = (
        "import bpy, sys, runpy\nfrom pathlib import Path\n"
        f"tool_dir = {str(ROOT / 'tools')!r}\n"
        "if tool_dir not in sys.path: sys.path.insert(0, tool_dir)\n"
        "with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):\n"
        + "\n".join("    " + line for line in statements.splitlines())
    )
    response = await client.call_tool("execute_blender_code", {"code": code})
    if response.isError:
        raise RuntimeError(response.content[0].text)
    payload = response.structuredContent or json.loads(response.content[0].text)
    if payload["status"] != "ok":
        raise RuntimeError(payload["message"])
    return payload["result"]


async def build(blender, args):
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    export = str(args.texture_export.resolve()) if args.texture_export else None
    prepared = (
        str(args.prepared_repairs.resolve(strict=True))
        if args.prepared_repairs
        else None
    )
    source = args.texture_from.resolve(strict=True) if args.texture_from else None
    manifest = json.loads((ROOT / "assets/manifest.json").read_text())
    tiles = [tile["id"] for tile in manifest["tiles"] if tile["kind"] == "building"]
    if args.only_tiles:
        if (
            not source
            or output == source
            or output == ROOT / "output/tokyo-station.blend"
        ):
            raise ValueError("A partial review requires a separate source and output")
        unknown = set(args.only_tiles) - set(tiles)
        if unknown:
            raise ValueError(f"Unknown PLATEAU tiles: {sorted(unknown)}")
        tiles = list(dict.fromkeys(args.only_tiles))
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=".texture-build-"
    ) as temp:
        staged = Path(temp) / output.name
        async with blender_client(blender, source) as client:
            if not source:
                arguments = [
                    "--flight",
                    str(ROOT / "output/.flight-timeline.json"),
                    "--output",
                    str(staged),
                    "--export-buildings",
                    str(Path(temp) / "omy-buildings.glb"),
                ]
                await execute(
                    client,
                    f"create = runpy.run_path({str(ROOT / 'tools/create-blender.py')!r})\n"
                    f"create['main']({arguments!r}, empty_scene=True)\n"
                    "result = {'cuts': len(bpy.context.scene.timeline_markers)}",
                )
            for index in range(0, len(tiles), args.chunk_size):
                group = tiles[index : index + args.chunk_size]
                await execute(
                    client,
                    "from blender_building_textures import texture_tiles\n"
                    f"texture_tiles({group!r}, {export!r}, {prepared!r})\n"
                    f"result = {{'tiles': {len(group)}}}",
                )
                print(
                    json.dumps(
                        {
                            "event": "textured",
                            "done": index + len(group),
                            "total": len(tiles),
                        }
                    ),
                    flush=True,
                )
            if not args.only_tiles:
                review = await execute(
                    client,
                    "from blender_texture_reviews import PLANS, recipe_hash\n"
                    "objects = bpy.context.scene.collection.children['01  PLATEAU・建築物'].all_objects\n"
                    "reviewed = {o['id']: o for o in objects if o.get('id') in PLANS}\n"
                    "if reviewed.keys() != PLANS.keys() or any(o.get('textureReviewFingerprint') != recipe_hash(k) for k, o in reviewed.items()):\n"
                    "    raise ValueError('Missing or outdated wall repair in the saved city')\n"
                    "result = {'planned': len(reviewed), 'faces': sum(o.get('textureReviewFaces', 0) for o in reviewed.values())}",
                )
                print(
                    json.dumps({"event": "reviewed-walls", "result": review}),
                    flush=True,
                )
                await execute(
                    client,
                    "from blender_omy import export_buildings, save_study\n"
                    "scene = bpy.context.scene\n"
                    "credits = bpy.data.texts['出典と座標']\n"
                    f"paint_notes = Path({str(ROOT / 'assets/BUILDING-TEXTURES.md')!r}).read_text()\n"
                    "base_notes = credits.as_string().partition(paint_notes.splitlines()[0])[0].rstrip()\n"
                    "credits.from_string(base_notes + '\\n\\n' + paint_notes)\n"
                    "groups = [scene.collection.children[name] for name in "
                    "('10  大丸有・建物別の外装写真', '10  丸の内二丁目ビル・測量による外装', '11  東京駅周辺地下街・LOD4')]\n"
                    f"export_buildings(groups[0], Path({str(Path(temp) / 'omy-buildings.glb')!r}))\n"
                    f"save_study(scene, groups, bpy.data.texts['出典と座標'], Path({str(Path(temp) / 'omy-architecture.blend')!r}))\n"
                    "result = {'landmarks': len(groups[0].objects)}",
                )
            result = await execute(
                client,
                "objects = bpy.context.scene.collection.children['01  PLATEAU・建築物'].all_objects\n"
                f"wanted = set({tiles!r})\n"
                "selected = [o for o in objects if o.get('tile') in wanted]\n"
                "if not selected or any(o.get('textureBuildingId') != o.get('id') for o in selected):\n"
                "    raise ValueError('One or more buildings lack an ID-specific texture')\n"
                "bpy.context.preferences.filepaths.save_version = 0\n"
                f"bpy.ops.wm.save_as_mainfile(filepath={str(staged)!r}, compress=True, relative_remap=False)\n"
                "result = {'buildings': len(selected), 'version': bpy.app.version_string, 'cuts': len(bpy.context.scene.timeline_markers)}",
            )
        staged.replace(output)
        if not args.only_tiles:
            (Path(temp) / "omy-architecture.blend").replace(
                output.with_name("omy-architecture.blend")
            )
            args.web_asset.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(Path(temp) / "omy-buildings.glb"), args.web_asset)
    print(json.dumps({**result, "file": str(output)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "output/tokyo-station.blend"
    )
    parser.add_argument(
        "--web-asset", type=Path, default=ROOT / "assets/omy-buildings.glb"
    )
    parser.add_argument(
        "--texture-from",
        type=Path,
        help="Open an existing city with individual building meshes",
    )
    parser.add_argument(
        "--texture-export",
        type=Path,
        help="Write the per-building textures and triangles for the viewer and turntable audit",
    )
    parser.add_argument(
        "--prepared-repairs",
        type=Path,
        help="Reuse paint results prepared from the exact first-draft atlas, geometry, UV and recipe",
    )
    parser.add_argument(
        "--only-tiles",
        nargs="+",
        help="Texture only named tiles in a separate review copy",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=3, help="Tiles per Blender Lab MCP request"
    )
    args = parser.parse_args()
    if args.chunk_size < 1:
        parser.error("--chunk-size must be positive")
    executable = (
        os.getenv("BLENDER_BIN")
        or shutil.which("blender")
        or "/Applications/Blender.app/Contents/MacOS/Blender"
    )
    asyncio.run(build(executable, args))
