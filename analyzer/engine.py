from analyzer.scanner import get_python_files
from analyzer.parser import parse_imports
from analyzer.usage_tracker import track_usage
from analyzer.classifier import classify_module


def analyze_project(project_path):

    python_files = get_python_files(project_path)

    results = []

    for file in python_files:

        imports = parse_imports(file)

        usages = track_usage(file)

        file_result = {
            "file": file,
            "imports": [],
        }

        for imp in imports:

            module = imp.get("module")

            if not module:
                continue

            alias = imp.get("alias")

            imported_name = imp.get("name")

            dependency_data = {
                "module": module,
                "alias": alias,
                "unused": False,
                "type": classify_module(module),
                "import_line": imp["line"],
                "usages": []
            }

            for usage in usages:

                if (
                    usage["name"] == alias
                    or usage["name"] == module.split(".")[0]
                    or usage["name"] == imported_name
                ):

                    dependency_data["usages"].append(usage)

            dependency_data["unused"] = (
                len(dependency_data["usages"]) == 0
            )

            file_result["imports"].append(dependency_data)

        results.append(file_result)

    return results