"""End-to-end and unit tests for VeriGraph."""

import os

import pytest

from verigraph.extract import build_graph
from verigraph.report import html_report, markdown_report, risk_ranked_edges
from verigraph.veracity import (
    Ternary,
    explain_node,
    grade_graph,
    graph_content_hash,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixture")


@pytest.fixture(scope="module")
def graded():
    graph = build_graph(FIXTURE)
    grade_graph(graph)
    return graph


def _edges(graph, kind=None, target=None):
    out = []
    for e in graph["edges"]:
        if kind is not None and e["kind"] != kind:
            continue
        if target is not None and e.get("target_name") != target:
            continue
        out.append(e)
    return out


# --- Ternary logic (ported from the OverCaml veracity engine) ---


def test_ternary_and():
    assert Ternary.TRUE & Ternary.TRUE == Ternary.TRUE
    assert Ternary.TRUE & Ternary.FALSE == Ternary.FALSE
    assert Ternary.TRUE & Ternary.UNKNOWN == Ternary.UNKNOWN
    assert Ternary.FALSE & Ternary.UNKNOWN == Ternary.FALSE


def test_ternary_or():
    assert Ternary.TRUE | Ternary.FALSE == Ternary.TRUE
    assert Ternary.FALSE | Ternary.FALSE == Ternary.FALSE
    assert Ternary.FALSE | Ternary.UNKNOWN == Ternary.UNKNOWN


def test_ternary_invert():
    assert ~Ternary.TRUE == Ternary.FALSE
    assert ~Ternary.FALSE == Ternary.TRUE
    assert ~Ternary.UNKNOWN == Ternary.UNKNOWN


def test_ternary_from_confidence_and_consensus():
    assert Ternary.from_confidence(0.95) == Ternary.TRUE
    assert Ternary.from_confidence(0.1) == Ternary.FALSE
    assert Ternary.from_confidence(0.5) == Ternary.UNKNOWN
    assert Ternary.consensus([Ternary.TRUE, Ternary.TRUE, Ternary.FALSE]) == Ternary.TRUE
    assert Ternary.consensus([]) == Ternary.UNKNOWN


# --- Extraction ---


def test_nodes_have_required_shape(graded):
    assert graded["directed"] is True
    valid_types = {"file", "module", "class", "function"}
    for n in graded["nodes"]:
        assert set(n) >= {"id", "type", "name", "path", "span"}
        assert n["type"] in valid_types
        assert len(n["span"]) == 2
    # foo must be extracted as a function node
    assert any(n["type"] == "function" and n["name"] == "foo" for n in graded["nodes"])
    assert any(n["type"] == "module" and n["name"] == "helper" for n in graded["nodes"])
    assert any(n["type"] == "class" and n["name"] == "Widget" for n in graded["nodes"])


def test_edge_kinds_present(graded):
    kinds = {e["kind"] for e in graded["edges"]}
    assert {"defines", "imports", "calls"} <= kinds


# --- The required veracity grades ---


def test_call_to_defined_function_is_true(graded):
    foo_calls = _edges(graded, kind="calls", target="foo")
    assert foo_calls, "expected a call edge to foo()"
    assert all(e["veracity"] == "true" for e in foo_calls)


def test_import_real_sibling_is_true(graded):
    imp = _edges(graded, kind="imports", target="helper")
    assert imp, "expected an import edge for sibling module helper"
    assert all(e["veracity"] == "true" for e in imp)


def test_import_nonexistent_module_is_false(graded):
    imp = _edges(graded, kind="imports", target="definitely_missing_module_xyz")
    assert imp, "expected an import edge for the nonexistent module"
    assert all(e["veracity"] == "false" for e in imp)


def test_import_real_external_module_is_unknown(graded):
    imp = _edges(graded, kind="imports", target="os")
    assert imp, "expected an import edge for stdlib os"
    assert all(e["veracity"] == "unknown" for e in imp)


def test_call_to_undefined_symbol_is_false_or_unknown(graded):
    calls = _edges(graded, kind="calls", target="totally_undefined_symbol")
    assert calls, "expected a call edge to the undefined symbol"
    assert all(e["veracity"] in ("false", "unknown") for e in calls)
    # specifically, a bare undefined name is a contradiction -> FALSE
    assert all(e["veracity"] == "false" for e in calls)


def test_attribute_call_is_unknown(graded):
    calls = _edges(graded, kind="calls", target="helper_func")
    assert calls, "expected an attribute call edge"
    assert all(e["veracity"] == "unknown" for e in calls)


def test_every_edge_graded(graded):
    for e in graded["edges"]:
        assert e["veracity"] in ("true", "unknown", "false")
        assert 0.0 <= e["confidence"] <= 1.0
        assert ":" in e["evidence"]  # path:line evidence


def test_summary_consistent(graded):
    s = graded["veracity_summary"]
    assert s["true"] + s["unknown"] + s["false"] == s["total_edges"]
    assert s["total_edges"] == len(graded["edges"])


# --- Idempotency ---


def test_grading_is_idempotent():
    g1 = build_graph(FIXTURE)
    g2 = build_graph(FIXTURE)
    h1 = graph_content_hash(g1)
    h2 = graph_content_hash(g2)
    assert h1 == h2
    grade_graph(g1)
    grade_graph(g1)  # re-grade must not change the hash
    assert g1["veracity_summary"]["content_hash"] == h1


# --- Reporting + explain ---


def test_markdown_report(graded):
    md = markdown_report(graded)
    assert "# VeriGraph verification report" in md
    assert "Grade distribution" in md
    assert "verification-risk" in md.lower()


def test_html_report_self_contained(graded):
    html = html_report(graded)
    assert "<!doctype html>" in html
    # no external resource references
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn" not in html.lower()


def test_risk_ranked_order(graded):
    risky = risk_ranked_edges(graded)
    seen_unknown = False
    for e in risky:
        if e["veracity"] == "unknown":
            seen_unknown = True
        if e["veracity"] == "false":
            assert not seen_unknown, "FALSE edges must rank before UNKNOWN"


def test_explain_node(graded):
    foo = next(n for n in graded["nodes"] if n["type"] == "function" and n["name"] == "foo")
    info = explain_node(graded, foo["id"])
    assert info["node"]["id"] == foo["id"]
    assert "incoming" in info and "outgoing" in info
    with pytest.raises(KeyError):
        explain_node(graded, "func:does.not.exist")
