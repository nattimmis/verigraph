"""Reporting for graded knowledge graphs.

Produces a Markdown report with the grade distribution (counts and percent
verified) and a ranked "verification-risk" list (FALSE edges first, then
UNKNOWN). Can also emit a self-contained, dependency-free HTML view of the
graph (no external CDNs, no network access required).

Author: Nathaniel Timmis. License: MIT.
"""

from __future__ import annotations

import html
import json

_BADGE = {"true": "TRUE", "unknown": "UNKNOWN", "false": "FALSE"}


def _ensure_graded(graph: dict) -> dict:
    if "veracity_summary" not in graph:
        from verigraph.veracity import grade_graph

        grade_graph(graph)
    return graph


def risk_ranked_edges(graph: dict) -> list[dict]:
    """Return edges ranked by verification risk: FALSE, then UNKNOWN."""
    order = {"false": 0, "unknown": 1, "true": 2}
    risky = [e for e in graph.get("edges", []) if e.get("veracity") in ("false", "unknown")]
    risky.sort(key=lambda e: (order.get(e.get("veracity"), 3), e.get("confidence", 0.0)))
    return risky


def markdown_report(graph: dict) -> str:
    """Render a Markdown verification report for a graded graph."""
    _ensure_graded(graph)
    summary = graph["veracity_summary"]
    meta = graph.get("meta", {})
    nodes = graph.get("nodes", [])
    total = summary["total_edges"]

    lines: list[str] = []
    lines.append("# VeriGraph verification report")
    lines.append("")
    lines.append(f"- Tool: `verigraph` ({meta.get('language', 'python')})")
    lines.append(f"- Root: `{meta.get('root', '?')}`")
    lines.append(f"- Files parsed: {meta.get('files_parsed', '?')}")
    lines.append(f"- Nodes: {len(nodes)}  |  Edges: {total}")
    lines.append(f"- Content hash: `{summary.get('content_hash', '?')}`")
    lines.append(f"- Overall status: **{_BADGE.get(summary['overall_status'], '?')}**")
    lines.append("")

    lines.append("## Grade distribution")
    lines.append("")
    lines.append("| Grade | Count | Share |")
    lines.append("|-------|------:|------:|")
    for g in ("true", "unknown", "false"):
        c = summary[g]
        pct = (100.0 * c / total) if total else 0.0
        lines.append(f"| {_BADGE[g]} | {c} | {pct:.1f}% |")
    lines.append("")
    lines.append(f"**Verified (TRUE): {summary['verified_pct']}% of {total} edges.**")
    if summary.get("contradictions"):
        lines.append("")
        lines.append(
            f"> {summary['contradictions']} edge(s) flagged as **contradictions** "
            "(claimed an in-graph target that the parsed source does not contain)."
        )
    lines.append("")

    # Node-type breakdown
    type_counts: dict[str, int] = {}
    for n in nodes:
        type_counts[n["type"]] = type_counts.get(n["type"], 0) + 1
    lines.append("## Nodes by type")
    lines.append("")
    lines.append("| Type | Count |")
    lines.append("|------|------:|")
    for t in ("file", "module", "class", "function"):
        if t in type_counts:
            lines.append(f"| {t} | {type_counts[t]} |")
    lines.append("")

    # Edge-kind breakdown
    kind_counts: dict[str, int] = {}
    for e in graph.get("edges", []):
        kind_counts[e["kind"]] = kind_counts.get(e["kind"], 0) + 1
    lines.append("## Edges by kind")
    lines.append("")
    lines.append("| Kind | Count |")
    lines.append("|------|------:|")
    for k in ("defines", "imports", "calls", "references"):
        if k in kind_counts:
            lines.append(f"| {k} | {kind_counts[k]} |")
    lines.append("")

    risky = risk_ranked_edges(graph)
    lines.append("## Verification-risk list (FALSE first, then UNKNOWN)")
    lines.append("")
    if not risky:
        lines.append("_No FALSE or UNKNOWN edges: every edge verified against the source._")
    else:
        lines.append("| Grade | Kind | Edge | Evidence |")
        lines.append("|-------|------|------|----------|")
        for e in risky[:200]:
            edge_desc = f"`{e['src']}` -> `{e.get('target_name', e['dst'])}`"
            lines.append(
                f"| {_BADGE.get(e['veracity'], '?')} | {e['kind']} | {edge_desc} | {e.get('evidence', '')} |"
            )
        if len(risky) > 200:
            lines.append("")
            lines.append(f"_... and {len(risky) - 200} more._")
    lines.append("")

    if meta.get("parse_errors"):
        lines.append("## Parse errors")
        lines.append("")
        for pe in meta["parse_errors"]:
            lines.append(f"- `{pe['path']}`: {pe['error']}")
        lines.append("")

    return "\n".join(lines)


def html_report(graph: dict) -> str:
    """Render a self-contained HTML page (no external resources)."""
    _ensure_graded(graph)
    summary = graph["veracity_summary"]
    meta = graph.get("meta", {})

    color = {"true": "#1a7f37", "unknown": "#9a6700", "false": "#cf222e"}
    nodes_json = json.dumps(graph.get("nodes", []))
    edges_json = json.dumps(graph.get("edges", []))

    rows = []
    for e in risk_ranked_edges(graph)[:500]:
        v = e.get("veracity", "unknown")
        rows.append(
            f"<tr><td><span class='badge' style='background:{color.get(v, '#555')}'>{html.escape(_BADGE.get(v, '?'))}</span></td>"
            f"<td>{html.escape(e['kind'])}</td>"
            f"<td><code>{html.escape(str(e['src']))}</code> &rarr; <code>{html.escape(str(e.get('target_name', e['dst'])))}</code></td>"
            f"<td>{html.escape(str(e.get('evidence', '')))}</td></tr>"
        )
    risk_rows = "\n".join(rows) or "<tr><td colspan='4'>No FALSE or UNKNOWN edges.</td></tr>"

    total = summary["total_edges"] or 1
    bar = "".join(
        f"<div class='seg' style='width:{100.0 * summary[g] / total:.4f}%;background:{color[g]}' title='{_BADGE[g]}: {summary[g]}'></div>"
        for g in ("true", "unknown", "false")
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VeriGraph report - {html.escape(str(meta.get('root', '')))}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 2rem; line-height: 1.5; }}
  h1 {{ margin-top: 0; }}
  .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; }}
  .card {{ border: 1px solid #8884; border-radius: 10px; padding: 1rem 1.25rem; min-width: 120px; }}
  .card .n {{ font-size: 1.8rem; font-weight: 700; }}
  .bar {{ display: flex; height: 22px; border-radius: 6px; overflow: hidden; margin: 1rem 0; }}
  .seg {{ height: 100%; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #8883; font-size: 0.9rem; vertical-align: top; }}
  code {{ background: #8882; padding: 1px 4px; border-radius: 4px; font-size: 0.85em; word-break: break-all; }}
  .badge {{ color: #fff; padding: 2px 8px; border-radius: 10px; font-size: 0.75rem; font-weight: 700; }}
  .wrap {{ overflow-x: auto; }}
  footer {{ margin-top: 2rem; color: #8888; font-size: 0.8rem; }}
</style></head>
<body>
<h1>VeriGraph verification report</h1>
<p>Root <code>{html.escape(str(meta.get('root', '?')))}</code> &middot;
   {meta.get('files_parsed', '?')} files &middot;
   {len(graph.get('nodes', []))} nodes &middot; {summary['total_edges']} edges &middot;
   overall <strong>{html.escape(_BADGE.get(summary['overall_status'], '?'))}</strong> &middot;
   hash <code>{html.escape(str(summary.get('content_hash', '')))}</code></p>
<div class="cards">
  <div class="card"><div class="n" style="color:{color['true']}">{summary['true']}</div>TRUE</div>
  <div class="card"><div class="n" style="color:{color['unknown']}">{summary['unknown']}</div>UNKNOWN</div>
  <div class="card"><div class="n" style="color:{color['false']}">{summary['false']}</div>FALSE</div>
  <div class="card"><div class="n">{summary['verified_pct']}%</div>verified</div>
  <div class="card"><div class="n">{summary.get('contradictions', 0)}</div>contradictions</div>
</div>
<div class="bar">{bar}</div>
<h2>Verification-risk list</h2>
<div class="wrap"><table>
<thead><tr><th>Grade</th><th>Kind</th><th>Edge</th><th>Evidence</th></tr></thead>
<tbody>
{risk_rows}
</tbody></table></div>
<footer>Generated by VeriGraph. Graph data embedded below for reuse.</footer>
<script type="application/json" id="verigraph-nodes">{nodes_json}</script>
<script type="application/json" id="verigraph-edges">{edges_json}</script>
</body></html>
"""
