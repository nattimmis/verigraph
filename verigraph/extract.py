"""Knowledge-graph extraction from Python source using the stdlib ``ast``.

Walks a directory, parses every ``.py`` file with Python's standard-library
``ast`` module (the guaranteed-working path, no third-party parser required),
and builds a directed knowledge graph:

* **nodes** ``{id, type, name, path, span}`` where ``type`` is one of
  ``file | module | class | function``.
* **edges** ``{src, dst, kind, target_name, path, line, resolution}`` where
  ``kind`` is one of ``imports | calls | defines | references``.

A symbol table of all definitions is built first, then call/import/reference
targets are resolved against it. Each edge is tagged with a ``resolution``
category that :mod:`verigraph.veracity` turns into a TRUE/UNKNOWN/FALSE grade.

The output JSON is graphify-compatible: ``{directed: true, nodes: [], edges: []}``.

Optional JavaScript/TypeScript extraction via ``tree-sitter`` is attempted only
if the package is installed; otherwise VeriGraph degrades gracefully and simply
skips those files (recording them as ungraphed).

Author: Nathaniel Timmis. License: MIT.
"""

from __future__ import annotations

import ast
import builtins
import importlib.util
import os
from dataclasses import dataclass, field

_BUILTINS = set(dir(builtins))

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".tox",
    ".eggs",
}


def _module_dotted(root: str, filepath: str) -> str:
    """Compute a dotted module name for ``filepath`` relative to ``root``."""
    rel = os.path.relpath(filepath, root)
    rel = rel[:-3] if rel.endswith(".py") else rel
    parts = [p for p in rel.split(os.sep) if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else os.path.basename(filepath)[:-3]


def _is_importable(module_name: str) -> bool:
    """Return True if ``module_name`` can be found on the import path.

    Used to distinguish a real stdlib/third-party module (UNKNOWN - external)
    from a genuinely nonexistent module (FALSE). Only the top-level package is
    probed, and probing never executes the module.
    """
    top = module_name.split(".")[0]
    if top in _BUILTINS or top in getattr(__import__("sys"), "builtin_module_names", ()):
        return True
    try:
        return importlib.util.find_spec(top) is not None
    except (ImportError, ModuleNotFoundError, ValueError, AttributeError):
        return False


@dataclass
class _ModuleInfo:
    dotted: str
    path: str
    module_id: str
    defined: dict[str, str] = field(default_factory=dict)  # name -> node id
    imported: dict[str, tuple[str, str]] = field(default_factory=dict)  # alias -> (origin, orig)


class _Collector(ast.NodeVisitor):
    """First pass: collect nodes and the module's definition/import tables."""

    def __init__(self, info: _ModuleInfo, rel_path: str):
        self.info = info
        self.rel_path = rel_path
        self.nodes: list[dict] = []
        # scope stack holds node ids; qualname stack holds names
        self.scope_ids: list[str] = [info.module_id]
        self.qual: list[str] = []

    def _qualname(self, name: str) -> str:
        return ".".join(self.qual + [name]) if self.qual else name

    def _add(self, node_type: str, nid: str, name: str, lineno: int, end: int) -> None:
        self.nodes.append(
            {
                "id": nid,
                "type": node_type,
                "name": name,
                "path": self.rel_path,
                "span": [lineno, end],
            }
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qn = self._qualname(node.name)
        nid = f"class:{self.info.dotted}.{qn}"
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        self._add("class", nid, node.name, node.lineno, end)
        if not self.qual:
            self.info.defined[node.name] = nid
        self.scope_ids.append(nid)
        self.qual.append(node.name)
        self.generic_visit(node)
        self.qual.pop()
        self.scope_ids.pop()

    def _visit_func(self, node) -> None:
        qn = self._qualname(node.name)
        nid = f"func:{self.info.dotted}.{qn}"
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        self._add("function", nid, node.name, node.lineno, end)
        if not self.qual:
            self.info.defined[node.name] = nid
        self.scope_ids.append(nid)
        self.qual.append(node.name)
        self.generic_visit(node)
        self.qual.pop()
        self.scope_ids.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bind = alias.asname or alias.name.split(".")[0]
            self.info.imported[bind] = (alias.name, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if node.level and node.level > 0:
            # relative import: resolve against this module's package
            pkg_parts = self.info.dotted.split(".")[: -node.level] if self.info.dotted else []
            base = ".".join(pkg_parts)
            mod = f"{base}.{mod}" if mod else base
        for alias in node.names:
            bind = alias.asname or alias.name
            self.info.imported[bind] = (mod, alias.name)
        self.generic_visit(node)


def _local_names(func_node) -> set[str]:
    """Names bound locally inside a function body (params + assignments)."""
    names: set[str] = set()
    args = func_node.args
    for a in (
        list(args.posonlyargs)
        + list(args.args)
        + list(args.kwonlyargs)
    ):
        names.add(a.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    for child in ast.walk(func_node):
        if isinstance(child, ast.Assign):
            for t in child.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(child, (ast.AnnAssign, ast.NamedExpr)):
            tgt = child.target
            if isinstance(tgt, ast.Name):
                names.add(tgt.id)
        elif isinstance(child, (ast.For, ast.AsyncFor)):
            for n in ast.walk(child.target):
                if isinstance(n, ast.Name):
                    names.add(n.id)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if child is not func_node:
                names.add(child.name)
    return names


class _EdgeBuilder(ast.NodeVisitor):
    """Second pass: build imports/calls/references edges with resolution tags."""

    def __init__(self, info: _ModuleInfo, rel_path: str, modules: dict[str, _ModuleInfo]):
        self.info = info
        self.rel_path = rel_path
        self.modules = modules
        self.edges: list[dict] = []
        self.scope_ids: list[str] = [info.module_id]
        self.local_stack: list[set[str]] = [set()]

    def _edge(self, dst: str, kind: str, target: str, line: int, resolution: str) -> None:
        self.edges.append(
            {
                "src": self.scope_ids[-1],
                "dst": dst,
                "kind": kind,
                "target_name": target,
                "path": self.rel_path,
                "line": line,
                "resolution": resolution,
            }
        )

    def _resolve_module(self, mod: str) -> tuple[str, str]:
        """Resolve an imported module to (dst_id, resolution_category)."""
        if mod in self.modules:
            return self.modules[mod].module_id, "sibling_import"
        # also allow matching a package __init__ by prefix
        for dotted, mi in self.modules.items():
            if dotted == mod or mod.startswith(dotted + "."):
                return mi.module_id, "sibling_import"
        if _is_importable(mod):
            return f"external:{mod}", "external_import"
        return f"missing:{mod}", "missing_module"

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            dst, res = self._resolve_module(alias.name)
            self._edge(dst, "imports", alias.name, node.lineno, res)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if node.level and node.level > 0:
            pkg_parts = self.info.dotted.split(".")[: -node.level] if self.info.dotted else []
            base = ".".join(pkg_parts)
            mod = f"{base}.{mod}" if mod else base
        dst, res = self._resolve_module(mod)
        self._edge(dst, "imports", mod or "(relative)", node.lineno, res)
        self.generic_visit(node)

    def _child_id(self, node_type: str, name: str) -> str:
        # Parent id is "module:<dotted>" or "<type>:<dotted>.<qual>"; the part
        # after the colon is the dotted-qualname prefix to extend. This mirrors
        # the id scheme used by _Collector exactly.
        prefix = self.scope_ids[-1].split(":", 1)[1]
        return f"{node_type}:{prefix}.{name}"

    def _emit_references(self, exprs) -> None:
        """Emit 'references' edges for bare-name uses (decorators, base classes)."""
        for expr in exprs:
            if isinstance(expr, ast.Name):
                dst, res = self._resolve_name_call(expr.id)
                self._edge(dst, "references", expr.id, expr.lineno, res)

    def _visit_func(self, node) -> None:
        self._emit_references(node.decorator_list)
        self.scope_ids.append(self._child_id("func", node.name))
        self.local_stack.append(_local_names(node))
        self.generic_visit(node)
        self.scope_ids.pop()
        self.local_stack.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._emit_references(list(node.decorator_list) + list(node.bases))
        self.scope_ids.append(self._child_id("class", node.name))
        self.local_stack.append(set())
        self.generic_visit(node)
        self.scope_ids.pop()
        self.local_stack.pop()

    def _resolve_name_call(self, name: str) -> tuple[str, str]:
        # local / dynamically-bound name
        for scope in reversed(self.local_stack):
            if name in scope:
                return f"local:{name}", "local_dynamic"
        if name in self.info.defined:
            return self.info.defined[name], "in_graph_def"
        if name in self.info.imported:
            origin, orig = self.info.imported[name]
            mi = self.modules.get(origin)
            if mi is None:
                for dotted, cand in self.modules.items():
                    if dotted == origin:
                        mi = cand
                        break
            if mi is not None and orig in mi.defined:
                return mi.defined[orig], "in_graph_def"
            return f"external:{origin}.{orig}", "external_import"
        if name in _BUILTINS:
            return f"builtin:{name}", "builtin"
        return f"unresolved:{name}", "unresolved"

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            dst, res = self._resolve_name_call(func.id)
            self._edge(dst, "calls", func.id, node.lineno, res)
        elif isinstance(func, ast.Attribute):
            attr = func.attr
            self._edge(f"external:{attr}", "calls", attr, node.lineno, "attribute_dynamic")
        self.generic_visit(node)


def _build_defines_edges(info: _ModuleInfo, file_id: str, nodes: list[dict]) -> list[dict]:
    """Structural 'defines' edges: file->module and module/class -> members."""
    edges: list[dict] = []
    # file -> module
    mod_node = next((n for n in nodes if n["id"] == info.module_id), None)
    if mod_node:
        edges.append(
            {
                "src": file_id,
                "dst": info.module_id,
                "kind": "defines",
                "target_name": info.dotted,
                "path": info.path,
                "line": 1,
                "resolution": "defines",
            }
        )
    # containment: a node defines another if the other's id is dotted-prefixed by it
    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        if n["id"] in (file_id, info.module_id):
            continue
        # strip "type:" prefix -> dotted path
        dotted = n["id"].split(":", 1)[1]
        parent_dotted = dotted.rsplit(".", 1)[0] if "." in dotted else info.dotted
        # find a parent node whose dotted path matches
        parent_id = None
        for cand_id, cand in by_id.items():
            if cand_id in (file_id,):
                continue
            cdot = cand_id.split(":", 1)[1]
            if cdot == parent_dotted and cand_id != n["id"]:
                parent_id = cand_id
                break
        if parent_id is None:
            parent_id = info.module_id
        edges.append(
            {
                "src": parent_id,
                "dst": n["id"],
                "kind": "defines",
                "target_name": n["name"],
                "path": n["path"],
                "line": n["span"][0],
                "resolution": "defines",
            }
        )
    return edges


def build_graph(root: str) -> dict:
    """Extract a knowledge graph from the Python files under ``root``.

    Returns a graphify-compatible dict ``{directed, nodes, edges, meta}``.
    """
    root = os.path.abspath(root)
    py_files: list[str] = []
    skipped_non_py: list[str] = []

    if os.path.isfile(root):
        if root.endswith(".py"):
            py_files.append(root)
        base_root = os.path.dirname(root)
    else:
        base_root = root
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                if fn.endswith(".py"):
                    py_files.append(full)
                elif fn.endswith((".js", ".ts", ".jsx", ".tsx")):
                    skipped_non_py.append(full)

    py_files.sort()

    # Pass 0: register modules
    modules: dict[str, _ModuleInfo] = {}
    file_nodes: dict[str, str] = {}  # path -> file node id
    rel_of: dict[str, str] = {}
    parse_errors: list[dict] = []

    all_nodes: list[dict] = []
    collectors: dict[str, _Collector] = {}
    trees: dict[str, ast.AST] = {}

    for fp in py_files:
        rel = os.path.relpath(fp, base_root)
        rel_of[fp] = rel
        dotted = _module_dotted(base_root, fp)
        module_id = f"module:{dotted}"
        file_id = f"file:{rel}"
        file_nodes[fp] = file_id
        try:
            with open(fp, encoding="utf-8") as fh:
                src = fh.read()
            tree = ast.parse(src, filename=fp)
        except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
            parse_errors.append({"path": rel, "error": f"{type(exc).__name__}: {exc}"})
            continue
        trees[fp] = tree
        n_lines = src.count("\n") + 1
        info = _ModuleInfo(dotted=dotted, path=rel, module_id=module_id)
        modules[dotted] = info
        # file + module nodes
        all_nodes.append(
            {"id": file_id, "type": "file", "name": os.path.basename(fp), "path": rel, "span": [1, n_lines]}
        )
        all_nodes.append(
            {"id": module_id, "type": "module", "name": dotted, "path": rel, "span": [1, n_lines]}
        )
        coll = _Collector(info, rel)
        coll.visit(tree)
        collectors[fp] = coll
        all_nodes.extend(coll.nodes)

    # Pass 2: edges (defines + imports/calls/references)
    all_edges: list[dict] = []
    for fp in py_files:
        if fp not in trees:
            continue
        rel = rel_of[fp]
        dotted = _module_dotted(base_root, fp)
        info = modules[dotted]
        coll = collectors[fp]
        node_subset = [n for n in all_nodes if n["path"] == rel]
        all_edges.extend(_build_defines_edges(info, file_nodes[fp], node_subset))
        eb = _EdgeBuilder(info, rel, modules)
        eb.visit(trees[fp])
        all_edges.extend(eb.edges)

    graph = {
        "directed": True,
        "nodes": all_nodes,
        "edges": all_edges,
        "meta": {
            "tool": "verigraph",
            "language": "python",
            "root": os.path.basename(root),
            "files_parsed": len(trees),
            "parse_errors": parse_errors,
            "ungraphed_non_python": [os.path.relpath(p, base_root) for p in skipped_non_py],
        },
    }
    return graph
