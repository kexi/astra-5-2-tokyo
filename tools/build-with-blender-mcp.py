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
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def build(blender, output, web_asset):
    server = shutil.which("blender-mcp")
    if not server:
        raise SystemExit(
            "Install Blender Lab's blender-mcp server and Blender MCP extension"
        )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    bridge = await asyncio.create_subprocess_exec(
        blender,
        "--background",
        "--factory-startup",
        "--online-mode",
        "--python-expr",
        "import bpy; bpy.ops.wm.read_factory_settings(use_empty=True)",
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
        for _ in range(120):
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
        arguments = [
            "--flight",
            str(ROOT / "output/.flight-timeline.json"),
            "--output",
            str(output),
            "--export-buildings",
            str(web_asset),
        ]
        code = (
            "import bpy, runpy\n"
            "window = bpy.context.window_manager.windows[0]\n"
            "with bpy.context.temp_override(window=window):\n"
            f"    create = runpy.run_path({str(ROOT / 'tools/create-blender.py')!r})\n"
            f"    create['main']({arguments!r}, empty_scene=True)\n"
            "    result = {'file': bpy.data.filepath, 'version': bpy.app.version_string, "
            "'cuts': len(bpy.context.scene.timeline_markers)}"
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(
                reader, writer, read_timeout_seconds=timedelta(minutes=5)
            ) as client,
        ):
            await client.initialize()
            response = await client.call_tool("execute_blender_code", {"code": code})
            if response.isError:
                raise RuntimeError(response.content[0].text)
            payload = response.structuredContent or json.loads(response.content[0].text)
            if payload["status"] != "ok":
                raise RuntimeError(payload["message"])
            print(json.dumps(payload["result"], ensure_ascii=False), flush=True)
    finally:
        if bridge.returncode is None:
            bridge.terminate()
        await bridge.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "output/tokyo-station.blend"
    )
    parser.add_argument(
        "--web-asset", type=Path, default=ROOT / "assets/omy-buildings.glb"
    )
    args = parser.parse_args()
    executable = (
        os.getenv("BLENDER_BIN")
        or shutil.which("blender")
        or "/Applications/Blender.app/Contents/MacOS/Blender"
    )
    asyncio.run(build(executable, args.output.resolve(), args.web_asset.resolve()))
