"""Whether code a subagent is about to run in Blender can only look.

A request gets its undo point just before the first action that may change the window. MCP for Blender's
execute_code runs any Python, so it counted as such an action whatever the code did, and a request that only asked
"where is the Cube?" left an undo point with nothing to undo: the panel's 되돌리기 then did nothing the first time.

This reads the code instead of going by the tool's name. It says yes only to code made of nothing but things known
to leave Blender's data alone: reading attributes, a short list of functions and methods that change nothing
whatever they are called on, and filling containers the code made itself. Anything else (an assignment to an
attribute, an operator, a method it does not know) is "may change", which is what every execute_code was before. So
a wrong answer errs towards an undo point too many, never one too few. It is not a safeguard against code that
tries to hide what it does: the subagent may change the scene anyway, this only tells whether it is about to.
"""

from __future__ import annotations

import ast

# Imports that run nothing of the user's and do nothing by being imported.
MODULES = {"bpy", "bmesh", "mathutils", "math", "json", "re", "os", "os.path", "sys", "time", "datetime", "collections",
           "itertools", "textwrap", "statistics"}
# Every function of these only computes.
PURE_MODULES = {"math", "json", "re", "os.path", "time", "datetime", "collections", "itertools", "textwrap", "statistics",
                "mathutils", "mathutils.geometry", "bpy.path"}
FROM_BPY = {"data", "context", "app", "path"}

FUNCS = {"print", "len", "str", "repr", "int", "float", "bool", "round", "abs", "min", "max", "sum", "sorted", "reversed",
         "enumerate", "zip", "range", "list", "tuple", "dict", "set", "frozenset", "isinstance", "issubclass", "hasattr",
         "getattr", "type", "id", "any", "all", "format", "hex", "ord", "chr", "divmod", "pow", "iter", "next", "dir",
         "callable", "vars"}
# Methods that change nothing whatever they are called on (a string, a dict, a Vector, a Blender object).
METHODS = {
    "join", "split", "rsplit", "splitlines", "strip", "lstrip", "rstrip", "startswith", "endswith", "replace", "format",
    "lower", "upper", "title", "capitalize", "ljust", "rjust", "center", "zfill", "find", "rfind", "index", "count",
    "encode", "decode", "isdigit", "isalpha", "isnumeric", "partition", "rpartition", "casefold",
    "get", "keys", "values", "items",
    "to_tuple", "to_list", "to_euler", "to_quaternion", "to_matrix", "to_translation", "to_scale", "to_3x3", "to_4x4",
    "to_2d", "to_3d", "to_4d", "to_track_quat", "decompose", "normalized", "inverted", "inverted_safe", "transposed",
    "cross", "dot", "angle", "lerp", "slerp",
    "select_get", "visible_get", "hide_get", "evaluated_get", "evaluated_depsgraph_get", "path_from_id", "is_property_set",
    "dumps", "loads", "now", "strftime", "isoformat", "total_seconds",
}
# These change what they are called on: fine on a list or dict the code made itself, not on Blender's own
# (mesh.materials.append, obj.modifiers.clear).
OWN_METHODS = {"append", "extend", "insert", "add", "update", "setdefault", "sort", "reverse", "pop", "remove", "discard",
               "clear", "appendleft"}
PUTTING = {"append", "extend", "insert", "add", "update", "appendleft"}  # ... and these put in whatever they are given
CONTAINERS = {"list", "dict", "set", "sorted", "defaultdict", "Counter", "OrderedDict", "deque"}
SCALARS = {"int", "float", "str", "bool", "len", "sum", "min", "max", "round", "abs"}
ITEMS = {"list", "dict", "set", "int", "float", "str"}  # what a defaultdict of the code's own may make for its items
DUNDERS = {"__name__", "__class__", "__doc__", "__module__"}

NODES = (
    ast.Module, ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For, ast.While, ast.If, ast.With, ast.withitem,
    ast.Try, ast.ExceptHandler, ast.Raise, ast.Assert, ast.Pass, ast.Break, ast.Continue, ast.Return, ast.Import,
    ast.ImportFrom, ast.alias, ast.FunctionDef, ast.Lambda, ast.arguments, ast.arg, ast.keyword,
    ast.Name, ast.Constant, ast.Attribute, ast.Subscript, ast.Slice, ast.Starred, ast.Call, ast.BinOp, ast.UnaryOp,
    ast.BoolOp, ast.Compare, ast.IfExp, ast.NamedExpr, ast.JoinedStr, ast.FormattedValue, ast.List, ast.Tuple, ast.Dict,
    ast.Set, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    ast.Load, ast.Store, ast.operator, ast.unaryop, ast.boolop, ast.cmpop,
)


def dotted(node) -> str:
    """`a.b.c` for a chain of plain names, else ""."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join(reversed(parts + [node.id])) if isinstance(node, ast.Name) else ""


def pairs(target, value):
    """What a `... = ...` sets, with the value each gets: one target, or side by side as in `rows, seen = [], {}`."""
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts) \
            and not any(isinstance(elt, ast.Starred) for elt in target.elts + value.elts):
        for inner, part in zip(target.elts, value.elts):
            yield from pairs(inner, part)
    else:
        yield target, value


class Code:
    """What the names in one piece of code stand for, as far as it shows without running it. Scopes are not told
    apart: a name counts as the code's own only if that holds wherever it is set."""

    def __init__(self, tree: ast.AST):
        self.modules: dict[str, str] = {}  # a name -> the module it stands for: import math as m
        self.pure_names: set[str] = set()  # a function taken from a module that only computes: from math import degrees
        self.defs: set[str] = set()        # functions the code defines (their bodies are checked like the rest)
        values: dict[str, list] = {}       # a name -> what `name = ...` sets it to; None for any other way it is set
        puts: dict[str, list] = {}         # a name -> what is put into it as an item: rows[key] = value; None when it does not show
        known = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.partition(".")[0]
                    self.modules[alias.asname or root] = alias.name if alias.asname else root
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name, full = alias.asname or alias.name, f"{node.module}.{alias.name}"
                    if full in PURE_MODULES:
                        self.modules[name] = full       # from os import path
                    elif node.module in PURE_MODULES:
                        self.pure_names.add(name)       # from math import degrees
                    else:
                        values.setdefault(name, []).append(None)
            elif isinstance(node, ast.FunctionDef):
                self.defs.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    for one, value in pairs(target, node.value):
                        if isinstance(one, ast.Name):
                            values.setdefault(one.id, []).append(value)
                            known.add(id(one))
                        elif isinstance(one, ast.Subscript) and isinstance(one.value, ast.Name):
                            puts.setdefault(one.value.id, []).append(value)
                            known.add(id(one))
            elif isinstance(node, ast.AugAssign):
                known.add(id(node.target))  # `n += 1` sets nothing new; whether it may is decided in allows
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.attr == "setdefault" and len(node.args) == 2 and not node.keywords:
                    puts.setdefault(node.func.value.id, []).append(node.args[1])
                elif node.func.attr in PUTTING | {"setdefault"}:
                    puts.setdefault(node.func.value.id, []).append(None)
        for node in ast.walk(tree):
            if id(node) in known:
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                values.setdefault(node.id, []).append(None)  # a loop variable, an unpacked value, `with ... as`
            elif isinstance(node, ast.arg):
                values.setdefault(node.arg, []).append(None)
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and isinstance(node.value, ast.Name):
                puts.setdefault(node.value.id, []).append(None)
        # The code's own containers and plain values: every `name = ...` of the name makes a new one.
        self.containers = {name for name, found in values.items() if all(self.makes_container(v) for v in found)}
        self.scalars = {name for name, found in values.items() if all(self.makes_scalar(v) for v in found)}
        # ... and the containers that hold nothing but what the code made itself: what is in rows[key] is its own too.
        self.all_own = {name for name in self.containers
                        if all(self.starts_own(v) for v in values[name])
                        and all(self.makes_container(p) or self.makes_scalar(p) for p in puts.get(name, []))}

    def maker(self, value) -> str:
        """The name of what a call makes a container with (list, defaultdict, collections.Counter), else ""."""
        if not isinstance(value, ast.Call):
            return ""
        if isinstance(value.func, ast.Name):
            return value.func.id if value.func.id in CONTAINERS else ""
        return value.func.attr if self.module_of(value.func) == "collections" and value.func.attr in CONTAINERS else ""

    def makes_container(self, value) -> bool:
        return isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)) or bool(self.maker(value))

    def makes_scalar(self, value) -> bool:
        if isinstance(value, (ast.Constant, ast.JoinedStr)):
            return True
        if isinstance(value, ast.UnaryOp):
            return self.makes_scalar(value.operand)
        return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in SCALARS

    def starts_own(self, value) -> bool:
        """A new container that starts out empty, or makes its items itself: {}, [], dict(), defaultdict(list), Counter()."""
        if isinstance(value, ast.Dict):
            return not value.keys
        if isinstance(value, (ast.List, ast.Set)):
            return not value.elts
        made = self.maker(value)
        if made == "defaultdict":
            return len(value.args) == 1 and not value.keywords and isinstance(value.args[0], ast.Name) and value.args[0].id in ITEMS
        return made in ("list", "dict", "set", "Counter", "OrderedDict", "deque") and not value.args and not value.keywords

    def module_of(self, func) -> str:
        """The module that only computes which `m.sqrt` or `os.path.join` belongs to, else ""."""
        root, _, rest = dotted(func).partition(".")
        module = self.modules.get(root)
        if not module or not rest:
            return ""
        path = f"{module}.{rest}".rpartition(".")[0]  # without the function's own name
        while path and path not in PURE_MODULES:
            path = path.rpartition(".")[0]
        return path

    def own(self, node) -> bool:
        """A container the code made itself, or an item of one that holds nothing else: rows, seen[key],
        seen.setdefault(key, [])."""
        if isinstance(node, ast.Name):
            return node.id in self.containers
        if isinstance(node, ast.Subscript):
            return isinstance(node.value, ast.Name) and node.value.id in self.all_own
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault" and \
            isinstance(node.func.value, ast.Name) and node.func.value.id in self.all_own

    def may_be_called(self, node) -> bool:
        """Something handed over to be called later (sorted's key): a lambda or a function known to only compute."""
        if isinstance(node, ast.Name):
            return node.id in FUNCS | self.pure_names | self.defs
        return isinstance(node, ast.Lambda) or (isinstance(node, ast.Attribute) and
                                                (dotted(node).startswith("str.") or bool(self.module_of(node))))

    def allows(self, node) -> bool:
        if not isinstance(node, NODES):
            return False  # del, global, class, async, match, ...: not looked into
        if isinstance(node, ast.Import):
            return all(alias.name in MODULES for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            return node.level == 0 and node.module in MODULES and (node.module != "bpy" or all(a.name in FROM_BPY for a in node.names))
        if isinstance(node, ast.FunctionDef):
            return not node.decorator_list
        if isinstance(node, ast.Constant):  # the way to an operator spelled as text: getattr(bpy, "ops")
            return not (isinstance(node.value, str) and (node.value == "ops" or ".ops" in node.value))
        if isinstance(node, ast.Attribute):
            return isinstance(node.ctx, ast.Load) and node.attr != "ops" and (not node.attr.startswith("__") or node.attr in DUNDERS)
        if isinstance(node, ast.Subscript):  # rows[key] = ... puts something into the code's own dict, whatever it is
            return isinstance(node.ctx, ast.Load) or (isinstance(node.value, ast.Name) and node.value.id in self.containers)
        if isinstance(node, ast.AugAssign):
            # In place: a Vector that came from an object would write through to it, so only what is the code's own.
            target = node.target
            if isinstance(target, ast.Name):
                return target.id in self.containers | self.scalars
            return isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id in self.all_own
        if isinstance(node, ast.Call):
            return self.allows_call(node)
        return True

    def allows_call(self, node: ast.Call) -> bool:
        if any(keyword.arg == "key" and not self.may_be_called(keyword.value) for keyword in node.keywords):
            return False
        func = node.func
        if isinstance(func, ast.Name):
            if func.id == "iter" and len(node.args) != 1:
                return False  # iter(callable, sentinel) calls it
            if func.id in ("getattr", "vars", "dir") and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in self.modules:
                return False  # into a module by name: getattr(bpy, name)
            if func.id == "defaultdict" and not all(self.may_be_called(arg) for arg in node.args[:1]):
                return False
            return func.id in FUNCS | self.pure_names | self.defs
        if isinstance(func, ast.Attribute):
            return bool(self.module_of(func)) or func.attr in METHODS or (func.attr in OWN_METHODS and self.own(func.value))
        return False  # the result of a call, called: getattr(obj, "select_set")(True)


def python_only_looks(code: str) -> bool:
    """True when running `code` in Blender cannot change its data, as far as reading the code can tell. False for
    anything it is not sure of, and for code that does not parse."""
    try:
        tree = ast.parse(code)
        found = Code(tree)
        return all(found.allows(node) for node in ast.walk(tree))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False
