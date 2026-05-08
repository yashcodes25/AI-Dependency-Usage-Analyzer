import ast


def parse_imports(file_path):

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()

    try:
        tree = ast.parse(code)

    except Exception:
        return []

    imports = []

    for node in ast.walk(tree):

        if isinstance(node, ast.Import):

            for alias in node.names:

                imports.append({
                    "type": "import",
                    "module": alias.name,
                    "alias": alias.asname,
                    "line": node.lineno
                })

        elif isinstance(node, ast.ImportFrom):

            for imported_name in node.names:

                imports.append({
                    "type": "from_import",
                    "module": node.module,
                    "name": imported_name.name,
                    "alias": imported_name.asname,
                    "line": node.lineno
                })

    return imports