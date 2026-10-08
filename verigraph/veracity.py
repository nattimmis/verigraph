"""Veracity grading for knowledge-graph edges.

This module ports the ternary-logic core of the OverCaml veracity engine
(three-valued TRUE/FALSE/UNKNOWN logic with ``__and__`` / ``__or__`` /
``__invert__``, content-hash idempotency, and invariant / contradiction
checks) and applies it to grade the edges produced by :mod:`verigraph.extract`.

Grading rule for each edge:

* ``TRUE``    - the destination resolves to a real, in-graph definition.
* ``UNKNOWN`` - the destination is external, dynamic, or unresolvable by name
                (e.g. a stdlib/third-party import, a builtin, an attribute
                call, or a local/dynamic binding).
* ``FALSE``   - the edge contradicts the parsed source: it references a symbol
                that is out of scope or does not exist anywhere reachable.

Each graded edge carries a confidence in ``[0, 1]`` derived from ternary
weighting, plus human-readable evidence of the form ``path:line``.

Author: Nathaniel Timmis. License: MIT.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum

# === TERNARY LOGIC (ported from the OverCaml veracity engine) ===


class Ternary(Enum):
    """Three-valued logic: TRUE / FALSE / UNKNOWN.

    Overcomes the Boolean limitation of forcing a binary answer when the
    honest answer is "not enough evidence". ``UNKNOWN`` is a first-class
    outcome, never silently coerced to pass or fail.
    """

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"

    def __and__(self, other: "Ternary") -> "Ternary":
        if self == Ternary.FALSE or other == Ternary.FALSE:
            return Ternary.FALSE
        if self == Ternary.TRUE and other == Ternary.TRUE:
            return Ternary.TRUE
        return Ternary.UNKNOWN

    def __or__(self, other: "Ternary") -> "Ternary":
        if self == Ternary.TRUE or other == Ternary.TRUE:
            return Ternary.TRUE
        if self == Ternary.FALSE and other == Ternary.FALSE:
            return Ternary.FALSE
        return Ternary.UNKNOWN

    def __invert__(self) -> "Ternary":
        if self == Ternary.TRUE:
            return Ternary.FALSE
        if self == Ternary.FALSE:
            return Ternary.TRUE
        return Ternary.UNKNOWN

    @staticmethod
    def from_confidence(
        score: float, threshold_true: float = 0.8, threshold_false: float = 0.3
    ) -> "Ternary":
        """Map a confidence score to a ternary value."""
        if score >= threshold_true:
            return Ternary.TRUE
        if score <= threshold_false:
            return Ternary.FALSE
        return Ternary.UNKNOWN

    @staticmethod
    def consensus(values: list["Ternary"]) -> "Ternary":
        """Compute a multi-attestation consensus from several ternary values."""
        if not values:
            return Ternary.UNKNOWN
        total = len(values)
        trues = sum(1 for v in values if v == Ternary.TRUE)
        falses = sum(1 for v in values if v == Ternary.FALSE)
        if trues > total * 0.6:
            return Ternary.TRUE
        if falses > total * 0.6:
            return Ternary.FALSE
        return Ternary.UNKNOWN


@dataclass(frozen=True)
class VerificationRecord:
    """Immutable verification record (an OverCaml-style algebraic value)."""

    property_name: str
    status: Ternary
    confidence: float
    evidence: str
    timestamp: float = field(default_factory=time.time)
    stage: str = ""

    def to_dict(self) -> dict:
        return {
            "property": self.property_name,
            "status": self.status.value,
            "confidence": round(self.confidence, 4),
            "evidence": self.evidence,
            "stage": self.stage,
        }


# === IDEMPOTENCY: content-hash of a graph so grading is replay-safe ===


def graph_content_hash(graph: dict) -> str:
    """Deterministic content hash of a graph's nodes and edges.

    Grading the same graph twice yields the same hash, which lets callers
    dedup or cache results safely (idempotency).
    """
    skeleton = {
        "nodes": sorted(n.get("id", "") for n in graph.get("nodes", [])),
        "edges": sorted(
            f"{e.get('src', '')}->{e.get('dst', '')}:{e.get('kind', '')}:{e.get('target_name', '')}"
            for e in graph.get("edges", [])
        ),
    }
    content = json.dumps(skeleton, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:20]


# === RESOLUTION CATEGORY -> TERNARY MAPPING ===

# How the extractor classified each edge's target, and the base confidence
# that classification warrants. The ternary engine cross-checks these against
# the actual node set (the "invariant"); a mismatch is a contradiction.
_CATEGORY_GRADE: dict[str, tuple[Ternary, float]] = {
    # Resolved to a real definition that must exist in the graph.
    "in_graph_def": (Ternary.TRUE, 0.95),
    "sibling_import": (Ternary.TRUE, 0.9),
    "defines": (Ternary.TRUE, 1.0),
    # External / dynamic / unverifiable-by-name -> honest UNKNOWN.
    "external_import": (Ternary.UNKNOWN, 0.5),
    "builtin": (Ternary.UNKNOWN, 0.5),
    "local_dynamic": (Ternary.UNKNOWN, 0.45),
    "attribute_dynamic": (Ternary.UNKNOWN, 0.4),
    # Contradiction: references something nonexistent / out of scope.
    "unresolved": (Ternary.FALSE, 0.85),
    "missing_module": (Ternary.FALSE, 0.85),
}


@dataclass
class EdgeGrade:
    """The veracity verdict for a single edge."""

    status: Ternary
    confidence: float
    evidence: str
    contradiction: bool = False

    def to_dict(self) -> dict:
        return {
            "veracity": self.status.value,
            "confidence": round(self.confidence, 4),
            "evidence": self.evidence,
            "contradiction": self.contradiction,
        }


def grade_edge(edge: dict, node_ids: set[str], attestations: int = 1) -> EdgeGrade:
    """Grade one edge's veracity against the parsed source (the node set).

    The extractor's resolution category gives a prior; the ternary engine then
    *verifies* it against the invariant "a resolved destination must exist as a
    real node". A violation of that invariant is a contradiction and forces
    the grade to FALSE regardless of the prior.
    """
    category = edge.get("resolution", "unresolved")
    base_status, base_conf = _CATEGORY_GRADE.get(category, (Ternary.FALSE, 0.8))

    path = edge.get("path", "?")
    line = edge.get("line", 0)
    target = edge.get("target_name", edge.get("dst", "?"))
    where = f"{path}:{line}"

    # Invariant: an edge claimed to resolve in-graph MUST point at a real node.
    claims_in_graph = category in ("in_graph_def", "sibling_import", "defines")
    dst = edge.get("dst", "")
    dst_present = dst in node_ids

    if claims_in_graph and not dst_present:
        # Contradiction detected: the parsed graph does not contain the
        # definition this edge claims to reach. This is exactly the kind of
        # "hallucinated" relationship a knowledge graph can silently emit.
        return EdgeGrade(
            status=Ternary.FALSE,
            confidence=0.9,
            evidence=f"{where}: contradiction - target '{target}' claimed in-graph but node '{dst}' is absent",
            contradiction=True,
        )

    status = base_status
    confidence = base_conf

    if status == Ternary.TRUE:
        # Multi-attestation boost (cf. the OCaml veracity engine's idea that a
        # fact attested across multiple sites earns higher confidence).
        if attestations > 1:
            confidence = min(1.0, base_conf + 0.01 * min(attestations - 1, 5))
        evidence = f"{where}: '{target}' resolves to in-graph definition '{dst}'"
        if attestations > 1:
            evidence += f" ({attestations} attestations)"
    elif status == Ternary.UNKNOWN:
        reason = {
            "external_import": "external/third-party or stdlib import",
            "builtin": "Python builtin",
            "local_dynamic": "local or dynamically bound name",
            "attribute_dynamic": "attribute access not statically resolvable",
        }.get(category, "unresolvable by name")
        evidence = f"{where}: '{target}' is {reason}; cannot verify statically"
    else:  # FALSE
        reason = (
            "module not found on import path"
            if category == "missing_module"
            else "symbol out of scope / not defined anywhere reachable"
        )
        evidence = f"{where}: '{target}' {reason}"

    return EdgeGrade(status=status, confidence=confidence, evidence=evidence)


def grade_graph(graph: dict) -> dict:
    """Grade every edge in a graph in place and attach a summary.

    Returns the same graph dict, mutated so each edge gains ``veracity``,
    ``confidence``, ``evidence`` and ``contradiction`` fields, plus a top-level
    ``veracity_summary`` block. Idempotent: re-grading an already-graded graph
    produces the same result and the same ``content_hash``.
    """
    node_ids = {n["id"] for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    # Count attestations: how many edges resolve to the same destination.
    attest: dict[str, int] = {}
    for e in edges:
        if e.get("resolution") in ("in_graph_def", "sibling_import", "defines"):
            attest[e.get("dst", "")] = attest.get(e.get("dst", ""), 0) + 1

    counts = {"true": 0, "unknown": 0, "false": 0}
    contradictions = 0

    for e in edges:
        n_attest = attest.get(e.get("dst", ""), 1)
        grade = grade_edge(e, node_ids, attestations=n_attest)
        e["veracity"] = grade.status.value
        e["confidence"] = round(grade.confidence, 4)
        e["evidence"] = grade.evidence
        e["contradiction"] = grade.contradiction
        counts[grade.status.value] += 1
        if grade.contradiction:
            contradictions += 1

    total = len(edges)
    verified_pct = round(100.0 * counts["true"] / total, 2) if total else 0.0

    # Overall status via ternary AND-reduction: TRUE only if nothing is FALSE
    # and nothing is UNKNOWN; FALSE if any edge is FALSE.
    if counts["false"] > 0:
        overall = Ternary.FALSE
    elif counts["unknown"] == 0 and total > 0:
        overall = Ternary.TRUE
    else:
        overall = Ternary.UNKNOWN

    graph["veracity_summary"] = {
        "overall_status": overall.value,
        "total_edges": total,
        "true": counts["true"],
        "unknown": counts["unknown"],
        "false": counts["false"],
        "verified_pct": verified_pct,
        "contradictions": contradictions,
        "content_hash": graph_content_hash(graph),
    }
    return graph


def explain_node(graph: dict, node_id: str) -> dict:
    """Return a node plus all graded edges touching it, for the CLI explainer."""
    node = next((n for n in graph.get("nodes", []) if n["id"] == node_id), None)
    if node is None:
        raise KeyError(node_id)
    outgoing = [e for e in graph.get("edges", []) if e.get("src") == node_id]
    incoming = [e for e in graph.get("edges", []) if e.get("dst") == node_id]
    return {"node": node, "outgoing": outgoing, "incoming": incoming}
