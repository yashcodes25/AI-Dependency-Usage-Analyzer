import ast


class UsageVisitor(ast.NodeVisitor):

    def __init__(self):
        self.usages = []

    def visit_Attribute(self, node):

        if isinstance(node.value, ast.Name):

            self.usages.append({
                "name": node.value.id,
                "attribute": node.attr,
                "line": node.lineno
            })

        self.generic_visit(node)


def track_usage(file_path):

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()

    try:
       tree = ast.parse(code)
    except Exception:
       return []

    visitor = UsageVisitor()

    visitor.visit(tree)

    return visitor.usages