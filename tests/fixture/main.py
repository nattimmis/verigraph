"""Fixture entry point exercising every veracity grade.

Expected grades after `verigraph build`:

* `import helper`                 -> TRUE    (real sibling module)
* `import definitely_missing_...` -> FALSE   (nonexistent module)
* `import os`                     -> UNKNOWN (real external/stdlib module)
* call `foo()`                    -> TRUE    (resolves to in-graph def below)
* call `totally_undefined_symbol` -> FALSE   (symbol out of scope / nonexistent)
* call `helper.helper_func()`     -> UNKNOWN (attribute access, dynamic)
"""

import os

import definitely_missing_module_xyz
import helper


def foo():
    return 42


def run():
    x = foo()  # TRUE: in-graph definition
    y = helper.helper_func()  # UNKNOWN: attribute access, not statically resolvable
    z = totally_undefined_symbol()  # FALSE: not defined anywhere reachable
    base = os.getcwd()  # UNKNOWN: external/stdlib
    _ = definitely_missing_module_xyz  # reference keeps the import "used"
    return x, y, z, base
