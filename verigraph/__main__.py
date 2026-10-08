"""VeriGraph command-line interface.

Subcommands:

* ``build <path> [--out DIR]`` - extract a graph and grade it, writing
  ``graph.json`` (and optionally ``report.md`` / ``graph.html``).
* ``grade <graph.json>``        - (re)grade an existing graph, print the summary.
* ``report <graph.json>``       - print a Markdown verification report.
* ``explain <graph.json> <id>`` - explain the graded edges touching one node.

Author: Nathaniel Timmis. License: MIT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from verigraph import __version__
from verigraph.extract import build_graph
from verigraph.report import html_report, markdown_report
from verigraph.veracity import explain_node, grade_graph


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _print_summary(graph: dict) -> None:
    s = graph["veracity_summary"]
    print(f"  overall : {s['overall_status'].upper()}")
    print(f"  edges   : {s['total_edges']}")
    print(f"  TRUE    : {s['true']}")
    print(f"  UNKNOWN : {s['unknown']}")
    print(f"  FALSE   : {s['false']}")
    print(f"  verified: {s['verified_pct']}%")
    print(f"  contradictions: {s['contradictions']}")
    print(f"  content hash  : {s['content_hash']}")


def cmd_build(args: argparse.Namespace) -> int:
    if not os.path.exists(args.path):
        print(f"error: path not found: {args.path}", file=sys.stderr)
        return 2
    graph = build_graph(args.path)
    grade_graph(graph)
    out_dir = args.out or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    graph_path = os.path.join(out_dir, "graph.json")
    with open(graph_path, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=2)
    print(f"wrote {graph_path}")
    print(
        f"  nodes={len(graph['nodes'])} edges={len(graph['edges'])} "
        f"files={graph['meta']['files_parsed']}"
    )
    _print_summary(graph)
    if args.report:
        rp = os.path.join(out_dir, "report.md")
        with open(rp, "w", encoding="utf-8") as fh:
            fh.write(markdown_report(graph))
        print(f"wrote {rp}")
    if args.html:
        hp = os.path.join(out_dir, "graph.html")
        with open(hp, "w", encoding="utf-8") as fh:
            fh.write(html_report(graph))
        print(f"wrote {hp}")
    return 0


def cmd_grade(args: argparse.Namespace) -> int:
    graph = _load(args.graph)
    grade_graph(graph)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(graph, fh, indent=2)
        print(f"wrote {args.out}")
    _print_summary(graph)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    graph = _load(args.graph)
    grade_graph(graph)
    if args.html:
        out = html_report(graph)
    else:
        out = markdown_report(graph)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"wrote {args.out}")
    else:
        print(out)
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    graph = _load(args.graph)
    grade_graph(graph)
    try:
        info = explain_node(graph, args.node_id)
    except KeyError:
        print(f"error: node not found: {args.node_id}", file=sys.stderr)
        matches = [n["id"] for n in graph.get("nodes", []) if args.node_id in n["id"]][:10]
        if matches:
            print("did you mean:", file=sys.stderr)
            for m in matches:
                print(f"  {m}", file=sys.stderr)
        return 2
    n = info["node"]
    print(f"Node: {n['id']}")
    print(f"  type={n['type']} name={n['name']} path={n['path']} span={n['span']}")
    print(f"\nOutgoing edges ({len(info['outgoing'])}):")
    for e in info["outgoing"]:
        print(f"  [{e.get('veracity', '?').upper():7}] {e['kind']:10} -> {e.get('target_name', e['dst'])}")
        print(f"            {e.get('evidence', '')}")
    print(f"\nIncoming edges ({len(info['incoming'])}):")
    for e in info["incoming"]:
        print(f"  [{e.get('veracity', '?').upper():7}] {e['kind']:10} <- {e['src']}")
        print(f"            {e.get('evidence', '')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verigraph",
        description="Extract a code knowledge graph and grade every edge TRUE/UNKNOWN/FALSE.",
    )
    p.add_argument("--version", action="version", version=f"verigraph {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="extract + grade a graph from a source tree")
    b.add_argument("path", help="directory or .py file to analyze")
    b.add_argument("--out", help="output directory (default: current dir)")
    b.add_argument("--report", action="store_true", help="also write report.md")
    b.add_argument("--html", action="store_true", help="also write self-contained graph.html")
    b.set_defaults(func=cmd_build)

    g = sub.add_parser("grade", help="(re)grade an existing graph.json")
    g.add_argument("graph", help="path to graph.json")
    g.add_argument("--out", help="write the graded graph to this path")
    g.set_defaults(func=cmd_grade)

    r = sub.add_parser("report", help="print a verification report")
    r.add_argument("graph", help="path to graph.json")
    r.add_argument("--html", action="store_true", help="emit self-contained HTML instead of Markdown")
    r.add_argument("--out", help="write report to this path instead of stdout")
    r.set_defaults(func=cmd_report)

    e = sub.add_parser("explain", help="explain the graded edges touching a node")
    e.add_argument("graph", help="path to graph.json")
    e.add_argument("node_id", help="node id (e.g. func:pkg.module.foo)")
    e.set_defaults(func=cmd_explain)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
