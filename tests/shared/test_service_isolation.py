"""
Test service boundary isolation invariant.
Hard rule: services/ must never import from each other.
Only services/* -> shared/* is allowed.
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest

def _find_repo_root() -> Path:
    cur = Path(__file__).resolve()
    for parent in cur.parents:
        if (parent / "services").is_dir() and (parent / "shared").is_dir():
            return parent
    return cur.parent.parent


SERVICES_DIR = _find_repo_root() / "services"
SERVICE_NAMES = ["scanner", "paper_bot", "live_bot"]


def get_imported_modules(file_path: Path) -> set[str]:
    """Parse a python file and return all imported module root names."""
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
    except Exception:
        return set()

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


@pytest.mark.parametrize("service_name", SERVICE_NAMES)
def test_service_does_not_import_other_services(service_name: str):
    """Verify that each service strictly does not import any other service."""
    service_path = SERVICES_DIR / service_name
    if not service_path.exists():
        pytest.skip(f"Service path {service_path} does not exist")

    other_services = [s for s in SERVICE_NAMES if s != service_name]
    forbidden_prefixes = []
    for other in other_services:
        forbidden_prefixes.append(f"services.{other}")
        forbidden_prefixes.append(other)

    violations = []
    for py_file in service_path.rglob("*.py"):
        # Ignore __pycache__
        if "__pycache__" in py_file.parts:
            continue
        imports = get_imported_modules(py_file)
        for imp in imports:
            for forbidden in forbidden_prefixes:
                if imp == forbidden or imp.startswith(f"{forbidden}."):
                    violations.append(f"{py_file.name} imports forbidden service '{imp}'")

    assert not violations, (
        f"Service '{service_name}' violates service boundary isolation:\n"
        + "\n".join(violations)
    )
