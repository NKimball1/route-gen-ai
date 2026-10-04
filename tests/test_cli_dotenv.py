"""Entry points must load .env before importing route modules.

Some route modules read settings once, at import time (ROUTEGEN_TOTAL_KG
in routes/power.py, the Overpass cache directory). The code review pass
moved load_dotenv() into main(), after those imports, so .env values for
them were silently ignored on the command line. This pins the order
statically: no API keys, no subprocess, and any new entry point that uses
dotenv is checked automatically.
"""
import ast
import glob
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tree(path: str) -> ast.Module:
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _uses_dotenv(tree: ast.Module) -> bool:
    return any(isinstance(n, ast.ImportFrom) and n.module == "dotenv" for n in tree.body)


ENTRY_POINTS = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "*.py"))
                      if _uses_dotenv(_tree(p)))


def _is_load(node: ast.stmt) -> bool:
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name) and node.value.func.id == "load_dotenv")


def _imports_routes(node: ast.stmt) -> bool:
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] == "routes"
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == "routes" for alias in node.names)
    return False


def test_the_entry_points_are_found():
    # the discovery itself must not silently find nothing
    assert {"api.py", "compose_route.py", "find_spot.py"} <= set(ENTRY_POINTS)


@pytest.mark.parametrize("script", ENTRY_POINTS)
def test_dotenv_loads_before_route_imports(script):
    body = _tree(os.path.join(ROOT, script)).body
    route_imports = [n.lineno for n in body if _imports_routes(n)]
    if not route_imports:
        return  # imports routes lazily, after main() has loaded .env (ask.py)
    loads = [n.lineno for n in body if _is_load(n)]
    assert loads and loads[0] < route_imports[0], (
        f"{script}: call load_dotenv() at module level before the first "
        f"'routes' import (line {route_imports[0]})")
