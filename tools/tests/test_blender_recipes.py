"""Runs the Python recipes from the Blender subagent prompt inside real Blender.

Needs Blender's Python module (`pip install bpy`, which pins a specific Python version);
skipped when it is not importable. Run:
  python -m unittest tools/tests/test_blender_recipes.py -v
"""

import ast
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handlib.agents  # noqa: E402
import handlib.install  # noqa: E402

try:
    import bpy  # noqa: F401
except ImportError:  # pragma: no cover - depends on the machine
    bpy = None

RECIPES = re.findall(r"```python\n(.*?)```", handlib.agents.prompt_template("blender-subagent"), re.S)
SEND_BLEND, RECEIVE_BLEND, SEND_FBX = RECIPES


def fill(code: str, **values) -> str:
    """Put real values into a recipe the way the subagent is told to (ROOTS / NAME / MANIFEST lines)."""
    for name, value in values.items():
        literal = f"r{json.dumps(value)}" if name == "MANIFEST" else json.dumps(value)
        code, count = re.subn(rf"^{name} = .*$", f"{name} = {literal}", code, count=1, flags=re.M)
        assert count == 1, name
    return code


def run(code: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<recipe>", "exec"), {"__name__": "__recipe__"})
    return out.getvalue().strip()


@unittest.skipIf(bpy is None, "Blender's bpy module is not installed")
class BlenderRecipeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exchange = Path(self.tmp.name) / "exchange"
        os.environ["CLAUDE_HAND_EXCHANGE_DIR"] = str(self.exchange)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mesh = bpy.data.meshes.new("RockMesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        material = bpy.data.materials.new("RockMat")
        mesh.materials.append(material)
        rock = bpy.data.objects.new("Rock", mesh)
        pebble = bpy.data.objects.new("Pebble", mesh)
        pebble.parent = rock
        bystander = bpy.data.objects.new("Bystander", mesh)
        for obj in (rock, pebble, bystander):
            bpy.context.scene.collection.objects.link(obj)
        bystander.select_set(True)

    def tearDown(self):
        os.environ.pop("CLAUDE_HAND_EXCHANGE_DIR", None)
        self.tmp.cleanup()

    def test_blend_round_trip(self):
        manifest_path = run(fill(SEND_BLEND, ROOTS=["Rock"], NAME="rock")).splitlines()[-1]
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], "claude-hand/blend@1")
        self.assertEqual(manifest["objects"], ["Pebble", "Rock"])
        self.assertTrue((self.exchange / manifest["blend_file"]).exists())

        bpy.ops.wm.read_factory_settings(use_empty=True)  # "another Blender": an empty scene
        added = run(fill(RECEIVE_BLEND, MANIFEST=manifest_path)).splitlines()[-1]
        self.assertEqual(sorted(ast.literal_eval(added)), ["Pebble", "Rock"])
        scene_objects = {o.name for o in bpy.context.scene.objects}
        self.assertEqual(scene_objects, {"Pebble", "Rock"})
        self.assertEqual(bpy.data.objects["Pebble"].parent.name, "Rock")
        self.assertEqual(bpy.data.objects["Rock"].data.materials[0].name, "RockMat")

    def test_receive_renames_on_name_clash(self):
        manifest_path = run(fill(SEND_BLEND, ROOTS=["Rock"], NAME="rock")).splitlines()[-1]
        added = ast.literal_eval(run(fill(RECEIVE_BLEND, MANIFEST=manifest_path)).splitlines()[-1])
        self.assertTrue(all(name.endswith(".001") for name in added), added)

    def test_fbx_export_restores_selection(self):
        manifest_path = run(fill(SEND_FBX, ROOTS=["Rock"], NAME="rock")).splitlines()[-1]
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], "claude-hand/model@1")
        fbx = self.exchange / manifest["model_file"]
        self.assertTrue(fbx.exists() and fbx.stat().st_size > 0)
        self.assertEqual({o.name for o in bpy.context.view_layer.objects if o.select_get()}, {"Bystander"})

    def test_port_expression_without_addon_is_harmless(self):
        output = run(handlib.install.blender_port_expr(9877))
        self.assertIn("not enabled", output)


if __name__ == "__main__":
    unittest.main()
