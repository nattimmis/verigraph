"""VeriGraph: a correctness-graded code knowledge graph.

VeriGraph extracts a knowledge graph from Python source using the standard
library ``ast`` module, then grades the veracity of every edge as
TRUE / UNKNOWN / FALSE by verifying it against the parsed source.

Author: Nathaniel Timmis. License: MIT.
"""

__version__ = "0.1.0"
__author__ = "Nathaniel Timmis"

from verigraph.veracity import Ternary

__all__ = ["Ternary", "__version__", "__author__"]
