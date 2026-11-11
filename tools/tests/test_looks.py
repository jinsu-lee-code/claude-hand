"""Tests for tools/handlib/looks.py: Blender code that can only look. Run: python -m unittest discover -s tools/tests -v"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handlib.looks import python_only_looks as looks  # noqa: E402

# What the Blender subagent really ran to look (from a supervisor session's records), and the like.
LOOKING = {
    "the file and whether it is saved": 'import bpy\nprint(bpy.data.filepath, bpy.app.version_string, "dirty", bpy.data.is_dirty)',
    "the whole scene as JSON": """
import bpy, json
print(json.dumps({
    "file": bpy.data.filepath, "dirty": bpy.data.is_dirty,
    "objects": [{"name": o.name, "type": o.type, "loc": [round(v, 2) for v in o.location], "dims": [round(v, 2) for v in o.dimensions],
                 "verts": len(o.data.vertices) if o.type == "MESH" else None, "selected": o.select_get(),
                 "materials": [s.material.name for s in o.material_slots if s.material]} for o in bpy.context.scene.objects],
    "active": getattr(bpy.context.view_layer.objects.active, "name", None),
}, ensure_ascii=False))
""",
    "a loop that fills its own list and dict": """
import bpy
rows, by_type = [], {}
count = 0
for obj in bpy.data.objects:
    count += 1
    by_type[obj.type] = by_type.get(obj.type, 0) + 1
    rows.append(f"{obj.name}: {tuple(round(c, 3) for c in obj.location)}")
rows.sort()
print(count, by_type)
print("\\n".join(rows))
""",
    "world space corners": """
import bpy
from mathutils import Vector
cube = bpy.data.objects.get("Cube")
if cube is not None:
    corners = [cube.matrix_world @ Vector(c) for c in cube.bound_box]
    low = min(c.z for c in corners)
    print(type(cube.data).__name__, low, cube.matrix_world.to_translation().to_tuple(3))
""",
    "modifiers and material colours": """
import bpy, math
for o in sorted(bpy.context.selected_objects, key=lambda o: o.name):
    mods = [(m.name, m.type) for m in o.modifiers]
    cols = [tuple(s.material.diffuse_color) for s in o.material_slots if s.material]
    print(o.name, mods, cols, math.degrees(o.rotation_euler.z), os_name := o.name.lower())
""",
    "a helper of its own": """
import bpy, os
def describe(o):
    return {"name": o.name, "parent": o.parent.name if o.parent else None}
print([describe(o) for o in bpy.data.objects], os.path.basename(bpy.data.filepath), bpy.path.abspath("//"))
""",
    "counting with a defaultdict": """
import bpy
from collections import defaultdict
seen = defaultdict(list)
for o in bpy.data.objects:
    seen[o.type].append(o.name)
print(dict(seen))
""",
    "nothing but a comment": "# just checking",
}

CHANGING = {
    "a property set": "import bpy\nbpy.data.objects['Cube'].location.x = 2",
    "a whole value set": "import bpy\nbpy.data.objects['Cube'].location = (2, 0, 0)",
    "one component set": "import bpy\nbpy.data.objects['Cube'].location[0] = 2",
    "added to in place": "import bpy\nbpy.data.objects['Cube'].location.x += 2",
    "a custom property": "import bpy\nbpy.context.object['made_by'] = 'claude'",
    "through a name for it": "import bpy\nc = bpy.data.objects['Cube']\nloc = c.location\nloc[0] = 2",
    "a wrapped vector added to": "import bpy\nfrom mathutils import Vector\nloc = bpy.data.objects['Cube'].location\nloc += Vector((2, 0, 0))",
    "an operator": "import bpy\nbpy.ops.mesh.primitive_cube_add()",
    "an operator named like a string method": "import bpy\nbpy.ops.object.join()",
    "an operator reached by its name": "import bpy\ngetattr(bpy, 'ops').object.join()",
    "an operator under another name": "from bpy import ops\nops.object.delete()",
    "a selection": "import bpy\nbpy.data.objects['Cube'].select_set(True)",
    "a selection inside a comprehension": "import bpy\nprint([o.select_set(True) for o in bpy.data.objects])",
    "the active object": "import bpy\nbpy.context.view_layer.objects.active = bpy.data.objects['Cube']",
    "a new datablock": "import bpy\nbpy.data.materials.new('Red')",
    "one removed": "import bpy\nbpy.data.objects.remove(bpy.data.objects['Cube'])",
    "a material slot added": "import bpy\nbpy.data.objects['Cube'].data.materials.append(bpy.data.materials['Red'])",
    "Blender's own list, cleared through a name": "import bpy\nmods = bpy.data.objects['Cube'].modifiers\nmods.clear()",
    "a name that is the code's own list only at first": "import bpy\nrows = []\nrows = bpy.data.objects['Cube'].modifiers\nrows.clear()",
    "a deletion": "import bpy\ndel bpy.data.objects['Cube']['made_by']",
    "setattr": "import bpy\nsetattr(bpy.data.objects['Cube'], 'name', 'Box')",
    "a method fetched and called": "import bpy\ngetattr(bpy.data.objects['Cube'], 'select_set')(True)",
    "a method handed to sorted": "import bpy\nc = bpy.data.objects['Cube']\nprint(sorted([True], key=c.select_set))",
    "the frame": "import bpy\nbpy.context.scene.frame_set(10)",
    "a copy of an object": "import bpy\nbpy.data.objects['Cube'].copy()",
    "a mesh update": "import bpy\nbpy.data.objects['Cube'].data.update()",
    "the undo of a request": "import claude_hand_chat\nprint(claude_hand_chat.undo_last_request())",
    "an export by the add-on": "import claude_hand_chat\nprint(claude_hand_chat.export_fbx(['Cube'], 'cube'))",
    "exec": "exec('import bpy; bpy.ops.object.delete()')",
    "a file written": "open('C:/tmp/x.txt', 'w').write('x')",
    "another module": "import subprocess\nprint(subprocess.run(['whoami']))",
    "a context override": "import bpy\nwith bpy.context.temp_override(window=bpy.context.window):\n    print(1)",
    "a lambda called at once": "import bpy\n(lambda o: o.hide_set(True))(bpy.data.objects['Cube'])",
    "a class": "import bpy\nclass X:\n    pass",
    "a dunder": "import bpy\nbpy.data.objects['Cube'].__setattr__('name', 'Box')",
    "code that does not parse": "import bpy\nprint(",
}


class LooksTests(unittest.TestCase):
    def test_code_that_only_reads_is_told(self):
        for what, code in LOOKING.items():
            with self.subTest(what):
                self.assertTrue(looks(code))

    def test_anything_that_may_change_the_scene_is_not(self):
        for what, code in CHANGING.items():
            with self.subTest(what):
                self.assertFalse(looks(code))

    def test_a_container_is_the_codes_own_only_if_it_always_is(self):
        self.assertTrue(looks("rows = []\nrows.append(1)\nrows += [2]\nrows[0] = 3\nprint(rows)"))
        self.assertTrue(looks("import collections\nn = collections.Counter()\nn['a'] += 1\nprint(n.get('a'))"))
        self.assertTrue(looks("import bpy\nseen = {}\nfor o in bpy.data.objects:\n    seen.setdefault(o.type, []).append(o.name)\nprint(seen)"))
        self.assertFalse(looks("import bpy\nfor rows in [bpy.data.objects['Cube'].modifiers]:\n    rows.clear()"))  # a loop variable
        self.assertFalse(looks("import bpy\ndef clear(rows=[]):\n    rows.clear()\nclear(bpy.data.objects['Cube'].modifiers)"))  # an argument
        # What a container holds is its own only when everything put into it is new.
        cube = "import bpy\nfrom mathutils import Vector\ncube = bpy.data.objects['Cube']\n"
        self.assertFalse(looks(cube + "rows = {}\nrows['a'] = cube.location\nrows['a'][0] = 2"))
        self.assertFalse(looks(cube + "rows = {}\nrows['a'] = cube.location\nrows['a'] += Vector((2, 0, 0))"))
        self.assertFalse(looks(cube + "rows = [o.location for o in bpy.data.objects]\nrows[0] += Vector((2, 0, 0))"))
        self.assertFalse(looks(cube + "rows = {}\nrows['m'] = cube.modifiers\nrows['m'].clear()"))
        self.assertFalse(looks(cube + "rows = []\nrows.append(cube.modifiers)\nrows[0].clear()"))
        self.assertFalse(looks(cube + "rows = {}\nrows.setdefault('m', cube.modifiers).clear()"))


if __name__ == "__main__":
    unittest.main()
