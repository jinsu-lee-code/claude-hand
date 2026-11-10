"""A picture of what a window shows now, taken through claude-hand's own ways in and never through Claude: the
chat panels' result previews.
"""

from __future__ import annotations

import json
from pathlib import Path

from handlib import link


# Runs inside Blender through MCP for Blender's execute_code: draws the largest 3D viewport off-screen (what the user
# sees there, overlays included) into a PNG. Off-screen so it neither needs Blender in front nor replaces the user's
# Render Result. __PATH__ and __SIZE__ are filled in with repr() / int().
BLENDER_CAPTURE_CODE = r'''
import json
import bpy, gpu
areas = [(w, a) for w in bpy.context.window_manager.windows for a in w.screen.areas if a.type == "VIEW_3D"]
if not areas:
    print(json.dumps({"error": "no 3D viewport is open"}))
else:
    window, area = max(areas, key=lambda wa: wa[1].width * wa[1].height)
    region = next(r for r in area.regions if r.type == "WINDOW")
    space, path = area.spaces.active, __PATH__
    scale = min(1.0, __SIZE__ / max(region.width, region.height))
    width, height = max(1, int(region.width * scale)), max(1, int(region.height * scale))
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        offscreen.draw_view3d(window.scene, window.view_layer, space, region, space.region_3d.view_matrix,
                              space.region_3d.window_matrix, do_color_management=True)
        pixels = offscreen.texture_color.read()
    finally:
        offscreen.free()
    pixels.dimensions = width * height * 4
    image = bpy.data.images.new("Claude Hand capture", width, height, alpha=False)
    try:
        try:
            import numpy
            image.pixels.foreach_set(numpy.asarray(pixels, dtype=numpy.float32).ravel() / 255.0)
        except ImportError:
            image.pixels.foreach_set([v / 255.0 for v in pixels])
        image.filepath_raw, image.file_format = path, "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)
    print(json.dumps({"path": path, "width": width, "height": height}))
'''


def capture_window(app: str, pin: str, path: str, max_size: int = 640, unity_request=None, blender=None) -> str | None:
    """A PNG of what a window shows now (Unity's Scene view, Blender's largest 3D viewport) at `path`, for the chat
    panels' result previews. Through claude-hand's own ways in, never through Claude. None when it cannot."""
    try:
        if app == "unity":
            import unity_bridge
            entry = unity_bridge.find_project(hash_=pin.rpartition("@")[2])
            if not entry:
                return None
            result = (unity_request or unity_bridge.request)(entry, "ch_capture", {"path": path, "max_size": max_size}, timeout=20.0)
            return path if isinstance(result, dict) and result.get("success") is not False and Path(path).exists() else None
        if app == "blender":
            code = BLENDER_CAPTURE_CODE.replace("__PATH__", repr(path)).replace("__SIZE__", str(int(max_size)))
            res = (blender or link.blender_request)(int(pin.rpartition("@")[2]), {"type": "execute_code", "params": {"code": code}}, 20.0)
            printed = str(((res or {}).get("result") or {}).get("result", "")).strip().splitlines()
            return path if printed and "path" in json.loads(printed[-1]) and Path(path).exists() else None
    except Exception:  # a preview is a nicety: a closed window or a busy editor just means no picture
        return None
    return None
