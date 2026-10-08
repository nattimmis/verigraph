# VeriGraph

**A code knowledge graph where every edge is correctness-graded, not just extracted.**

Most code-graph tools stop at extraction: they emit nodes and edges and trust
that each edge is real. VeriGraph adds a second step. After building the graph
from your source, it grades the *veracity* of every edge as one of three values:

| Grade | Meaning |
|-------|---------|
| **TRUE** | The edge's target resolves to a real definition that exists in the graph (verified against the parsed source). |
| **UNKNOWN** | The target is external, dynamic, or not resolvable by name alone (a third-party/stdlib import, a builtin, an attribute call, a locally-bound name). Honestly reported as "not statically verifiable" rather than guessed. |
| **FALSE** | The edge contradicts the source: it references a symbol that is out of scope or does not exist anywhere reachable, or an import that cannot be found on the import path. |

The point is to make a graph you can *trust by inspection*: you can see which
relationships are backed by evidence, which are external and unverifiable, and
which look wrong. UNKNOWN is a first-class answer, never silently coerced to
pass or fail.

VeriGraph is pure Python and uses the standard library `ast` module, so Python
extraction has no third-party dependencies and works out of the box.

## Install

```bash
pip install verigraph
```

Requires Python 3.10+. Optional JavaScript/TypeScript extraction (via
`tree-sitter`) installs with `pip install "verigraph[js]"`; if it is not
installed, VeriGraph simply skips those files and records them as ungraphed.

## Quickstart

```bash
# Build a graph from a source tree, grade every edge, write graph.json
verigraph build path/to/your/project --out ./vg

# Also emit a Markdown report and a self-contained HTML view (no CDNs)
verigraph build path/to/your/project --out ./vg --report --html

# Re-grade an existing graph.json
verigraph grade ./vg/graph.json

# Print a verification report (Markdown to stdout, or --html)
verigraph report ./vg/graph.json

# Explain every graded edge touching one node
verigraph explain ./vg/graph.json func:mymodule.my_function
```

## What it extracts

* **Nodes** `{id, type, name, path, span}` where `type` is one of
  `file | module | class | function`.
* **Edges** `{src, dst, kind, target_name, path, line, resolution}` where
  `kind` is one of `imports | calls | defines | references`. After grading,
  each edge also carries `veracity`, `confidence`, `evidence`, and
  `contradiction`.

The output `graph.json` is graphify-compatible: `{ "directed": true,
"nodes": [...], "edges": [...] }`, so it drops into tools that read that shape.

## How grading works

VeriGraph builds a symbol table of every definition it parses, then resolves
each call/import/reference target against it:

* A call to a function defined in the graph &rarr; **TRUE**.
* An import of a sibling module that exists in the tree &rarr; **TRUE**.
* An import of a real stdlib/third-party module (found on the import path) or a
  call to a builtin / attribute &rarr; **UNKNOWN**.
* A call to a name that is neither defined, imported, builtin, nor locally
  bound, or an import of a module not found anywhere &rarr; **FALSE**.

It then runs an invariant check: any edge that *claims* to resolve to an
in-graph definition but points at a node that is absent is flagged as a
**contradiction** and forced to FALSE. Confidence in `[0, 1]` comes from
ternary weighting, with a small multi-attestation boost when the same
definition is reached from several call sites. Evidence is recorded as
`path:line`.

This grading logic is a port of the ternary-logic core (TRUE/FALSE/UNKNOWN with
`and`/`or`/`not`, content-hash idempotency, and invariant/contradiction checks)
from the OverCaml veracity engine.

### Note on import grading

Import resolution checks whether a module is importable *in the Python
environment where VeriGraph runs*. A third-party dependency that is not
installed in that environment will grade **FALSE** ("module not found on import
path"), not UNKNOWN. For the most accurate import grades, run VeriGraph inside
your project's own virtual environment where its dependencies are installed.

## Example output

Running `verigraph build` on the bundled test fixture (`tests/fixture`, two
tiny modules written to exercise every grade):

```
wrote ./vg/graph.json
  nodes=9 edges=14 files=2
  overall : FALSE
  edges   : 14
  TRUE    : 9
  UNKNOWN : 3
  FALSE   : 2
  verified: 64.29%
  contradictions: 0
  content hash  : a64449140bfb2483688b
```

The individual relationships graded as expected:

```
TRUE     imports  helper                          (real sibling module)
FALSE    imports  definitely_missing_module_xyz   (nonexistent module)
UNKNOWN  imports  os                              (real stdlib module)
TRUE     calls    foo                             (resolves to in-graph def)
UNKNOWN  calls    helper_func                     (attribute access, dynamic)
FALSE    calls    totally_undefined_symbol        (out of scope / nonexistent)
```

### A real-world run

On a 45-file Python project (petromap) VeriGraph produced:

```
nodes=243 edges=2203 files=45
TRUE    : 438
UNKNOWN : 1735
FALSE   : 30
verified: 19.88%
```

The large UNKNOWN share is expected and honest: most edges are calls into
third-party libraries and attribute accesses that cannot be resolved by name
alone. The 30 FALSE edges were all imports of third-party packages not
installed in the analysis environment (e.g. `shapely`, `PIL`) plus genuinely
undefined names, which is exactly the signal the grade is meant to surface.

## Credits

Inspired by [Graphify](https://github.com/Graphify-Labs/graphify) (`graphifyy`,
Apache-2.0), which pioneered the lightweight code-knowledge-graph approach and
the `graph.json` shape VeriGraph emits. VeriGraph's extraction is an original,
independent implementation.

Verification powered by the OverCaml veracity engine by Nathaniel Timmis.

## License

MIT &copy; 2026 Nathaniel Timmis. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
