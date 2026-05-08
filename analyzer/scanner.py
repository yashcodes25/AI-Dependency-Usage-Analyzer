from pathlib import Path

IGNORED_DIRS = {
    "venv",
    ".venv",
    "__pycache__",
    "node_modules",
    ".git",
    "dist",
    "build"
}


def get_python_files(project_path: str):
    project = Path(project_path)

    python_files = []

    for file in project.rglob("*.py"):

        if any(part in IGNORED_DIRS for part in file.parts):
            continue

        python_files.append(str(file))

    return python_files