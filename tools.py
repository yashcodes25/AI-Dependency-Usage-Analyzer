# tools.py
"""
Built-in local tools for AgentKit Local.

Requirements:
    pip install pandas

Optional:
    pip install pypdf openpyxl matplotlib

Design goals:
- Local-first
- Beginner-friendly
- Safe by default
- Useful for real automations, not chatbots
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentkit import tool


# ---------------------------------------------------------------------
# Safety helpers
# ---------------------------------------------------------------------


PROJECT_ROOT = Path.cwd().resolve()
DEFAULT_ALLOWED_DIRS = [
    PROJECT_ROOT,
]


def _resolve_path(path: str) -> Path:
    if not path:
        raise ValueError("Path cannot be empty.")

    resolved = Path(path).expanduser().resolve()
    return resolved


def _is_inside(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _ensure_safe_path(path: str, *, must_exist: bool = False) -> Path:
    resolved = _resolve_path(path)

    if not any(_is_inside(allowed, resolved) for allowed in DEFAULT_ALLOWED_DIRS):
        raise PermissionError(
            f"Unsafe path blocked: {resolved}. Tools can only access files inside the project folder: {PROJECT_ROOT}"
        )

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")

    return resolved


def _ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def _shorten(text: str, max_chars: int = 8000) -> str:
    text = str(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [truncated {len(text) - max_chars} chars]"


def _safe_text_read(path: Path, max_chars: int = 12000) -> str:
    encodings = ["utf-8", "utf-8-sig", "latin-1"]

    last_error = None
    for encoding in encodings:
        try:
            content = path.read_text(encoding=encoding, errors="replace")
            return _shorten(content, max_chars=max_chars)
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Could not read text file {path}: {last_error}")


def _json_default(value: Any):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return list(value)
    return str(value)


# ---------------------------------------------------------------------
# File and folder tools
# ---------------------------------------------------------------------


@tool
def list_files(folder: str = ".", recursive: bool = False, max_files: int = 200) -> str:
    """
    List files and folders inside a directory.
    Use this before reading or organizing files.
    """
    folder_path = _ensure_safe_path(folder, must_exist=True)

    if not folder_path.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    pattern = "**/*" if recursive else "*"
    items = list(folder_path.glob(pattern))
    items = sorted(items, key=lambda p: (not p.is_dir(), str(p).lower()))

    output = []
    count = 0

    for item in items:
        if count >= max_files:
            output.append(f"... stopped after {max_files} items")
            break

        rel = item.relative_to(PROJECT_ROOT) if _is_inside(PROJECT_ROOT, item) else item
        kind = "DIR " if item.is_dir() else "FILE"
        size = "" if item.is_dir() else f" | {item.stat().st_size} bytes"
        output.append(f"{kind} | {rel}{size}")
        count += 1

    if not output:
        return f"No files found in {folder}"

    return "\n".join(output)


@tool
def read_file(path: str, max_chars: int = 12000) -> str:
    """
    Read a text file.
    Works best for .txt, .md, .py, .csv, .json, .html, .css, .js files.
    """
    file_path = _ensure_safe_path(path, must_exist=True)

    if not file_path.is_file():
        raise IsADirectoryError(f"Not a file: {path}")

    return _safe_text_read(file_path, max_chars=max_chars)


@tool
def write_file(path: str, content: str) -> str:
    """
    Write content to a file.
    Creates parent folders automatically.
    """
    file_path = _ensure_safe_path(path, must_exist=False)
    _ensure_parent(file_path)
    file_path.write_text(str(content), encoding="utf-8")
    return f"Wrote {len(str(content))} characters to {file_path.relative_to(PROJECT_ROOT)}"


@tool
def append_file(path: str, content: str) -> str:
    """
    Append content to a file.
    Creates parent folders automatically.
    """
    file_path = _ensure_safe_path(path, must_exist=False)
    _ensure_parent(file_path)

    with file_path.open("a", encoding="utf-8") as f:
        f.write(str(content))

    return f"Appended {len(str(content))} characters to {file_path.relative_to(PROJECT_ROOT)}"


@tool
def create_folder(path: str) -> str:
    """
    Create a folder if it does not exist.
    """
    folder_path = _ensure_safe_path(path, must_exist=False)
    folder_path.mkdir(parents=True, exist_ok=True)
    return f"Folder ready: {folder_path.relative_to(PROJECT_ROOT)}"


@tool
def move_file(source: str, destination: str) -> str:
    """
    Move a file from source to destination.
    Creates destination parent folders automatically.
    """
    source_path = _ensure_safe_path(source, must_exist=True)
    destination_path = _ensure_safe_path(destination, must_exist=False)

    if not source_path.is_file():
        raise IsADirectoryError(f"Source is not a file: {source}")

    _ensure_parent(destination_path)

    if destination_path.exists():
        raise FileExistsError(f"Destination already exists: {destination}")

    shutil.move(str(source_path), str(destination_path))

    return f"Moved {source_path.relative_to(PROJECT_ROOT)} to {destination_path.relative_to(PROJECT_ROOT)}"


@tool
def copy_file(source: str, destination: str) -> str:
    """
    Copy a file from source to destination.
    Creates destination parent folders automatically.
    """
    source_path = _ensure_safe_path(source, must_exist=True)
    destination_path = _ensure_safe_path(destination, must_exist=False)

    if not source_path.is_file():
        raise IsADirectoryError(f"Source is not a file: {source}")

    _ensure_parent(destination_path)

    if destination_path.exists():
        raise FileExistsError(f"Destination already exists: {destination}")

    shutil.copy2(str(source_path), str(destination_path))

    return f"Copied {source_path.relative_to(PROJECT_ROOT)} to {destination_path.relative_to(PROJECT_ROOT)}"


@tool
def rename_file(source: str, new_name: str) -> str:
    """
    Rename a file while keeping it in the same folder.
    """
    source_path = _ensure_safe_path(source, must_exist=True)

    if not source_path.is_file():
        raise IsADirectoryError(f"Source is not a file: {source}")

    if "/" in new_name or "\\" in new_name:
        raise ValueError("new_name must be a file name only, not a path.")

    destination_path = source_path.parent / new_name
    destination_path = _ensure_safe_path(str(destination_path), must_exist=False)

    if destination_path.exists():
        raise FileExistsError(f"Destination already exists: {destination_path}")

    source_path.rename(destination_path)

    return f"Renamed {source_path.name} to {destination_path.name}"


@tool
def file_info(path: str) -> str:
    """
    Get metadata about a file or folder.
    """
    target = _ensure_safe_path(path, must_exist=True)
    stat = target.stat()

    info = {
        "path": str(target.relative_to(PROJECT_ROOT)) if _is_inside(PROJECT_ROOT, target) else str(target),
        "type": "folder" if target.is_dir() else "file",
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(timespec="seconds"),
    }

    if target.is_file():
        info["extension"] = target.suffix.lower()

    if target.is_dir():
        info["items"] = len(list(target.iterdir()))

    return json.dumps(info, indent=2, ensure_ascii=False)


@tool
def search_text_in_files(folder: str, query: str, file_pattern: str = "*.txt", max_matches: int = 50) -> str:
    """
    Search for text inside files in a folder.
    Useful for local research, notes, and code search.
    """
    folder_path = _ensure_safe_path(folder, must_exist=True)

    if not folder_path.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    query_lower = query.lower()
    matches = []

    for path in folder_path.rglob(file_pattern):
        if len(matches) >= max_matches:
            break

        if not path.is_file():
            continue

        try:
            content = _safe_text_read(path, max_chars=50000)
        except Exception:
            continue

        lines = content.splitlines()

        for line_number, line in enumerate(lines, start=1):
            if query_lower in line.lower():
                rel = path.relative_to(PROJECT_ROOT) if _is_inside(PROJECT_ROOT, path) else path
                matches.append(f"{rel}:{line_number}: {line.strip()}")
                if len(matches) >= max_matches:
                    break

    if not matches:
        return f"No matches found for query: {query}"

    return "\n".join(matches)


# ---------------------------------------------------------------------
# CSV and data tools
# ---------------------------------------------------------------------


def _load_pandas():
    try:
        import pandas as pd

        return pd
    except Exception as exc:
        raise ImportError("This tool requires pandas. Install with: pip install pandas") from exc


@tool
def read_csv(path: str, max_rows: int = 20) -> str:
    """
    Read a CSV file and return a preview.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)

    df = pd.read_csv(csv_path)

    info = {
        "path": str(csv_path.relative_to(PROJECT_ROOT)),
        "rows": int(len(df)),
        "columns": list(df.columns),
        "preview": df.head(max_rows).fillna("").to_dict(orient="records"),
    }

    return json.dumps(info, indent=2, ensure_ascii=False, default=_json_default)


@tool
def summarize_csv(path: str) -> str:
    """
    Summarize a CSV file with column types, missing values, and numeric statistics.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)

    df = pd.read_csv(csv_path)

    summary: Dict[str, Any] = {
        "path": str(csv_path.relative_to(PROJECT_ROOT)),
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "column_names": list(df.columns),
        "missing_values": df.isna().sum().astype(int).to_dict(),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
    }

    numeric_df = df.select_dtypes(include="number")

    if not numeric_df.empty:
        summary["numeric_summary"] = json.loads(
            numeric_df.describe().fillna("").to_json()
        )

    categorical_columns = [
        col for col in df.columns if str(df[col].dtype) in ["object", "category", "bool"]
    ]

    top_values = {}
    for col in categorical_columns[:10]:
        top_values[col] = df[col].fillna("").astype(str).value_counts().head(10).to_dict()

    summary["top_values"] = top_values

    return json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default)


@tool
def filter_csv(path: str, column: str, operator: str, value: str, output_path: str = "./output/filtered.csv") -> str:
    """
    Filter a CSV file and save the result.
    Supported operators: ==, !=, >, >=, <, <=, contains
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    df = pd.read_csv(csv_path)

    if column not in df.columns:
        raise KeyError(f"Column not found: {column}. Available columns: {list(df.columns)}")

    series = df[column]

    if operator == "contains":
        mask = series.astype(str).str.contains(str(value), case=False, na=False)
    elif operator in [">", ">=", "<", "<="]:
        numeric_series = pd.to_numeric(series, errors="coerce")
        numeric_value = float(value)

        if operator == ">":
            mask = numeric_series > numeric_value
        elif operator == ">=":
            mask = numeric_series >= numeric_value
        elif operator == "<":
            mask = numeric_series < numeric_value
        else:
            mask = numeric_series <= numeric_value
    elif operator == "==":
        mask = series.astype(str) == str(value)
    elif operator == "!=":
        mask = series.astype(str) != str(value)
    else:
        raise ValueError("Unsupported operator. Use ==, !=, >, >=, <, <=, contains")

    filtered = df[mask]
    _ensure_parent(out_path)
    filtered.to_csv(out_path, index=False)

    return f"Filtered {len(filtered)} rows from {len(df)} rows and saved to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def write_csv(path: str, rows_json: str) -> str:
    """
    Write rows to a CSV file.
    rows_json must be a JSON list of objects.
    Example: [{"name":"Asha","score":92},{"name":"Ravi","score":85}]
    """
    output_path = _ensure_safe_path(path, must_exist=False)
    _ensure_parent(output_path)

    rows = json.loads(rows_json)

    if not isinstance(rows, list):
        raise ValueError("rows_json must be a JSON list.")

    if not rows:
        output_path.write_text("", encoding="utf-8")
        return f"Wrote empty CSV to {output_path.relative_to(PROJECT_ROOT)}"

    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Every row must be a JSON object.")

    fieldnames = sorted({key for row in rows for key in row.keys()})

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return f"Wrote {len(rows)} rows to {output_path.relative_to(PROJECT_ROOT)}"


@tool
def convert_csv_to_json(path: str, output_path: str = "./output/data.json") -> str:
    """
    Convert a CSV file to JSON records.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    df = pd.read_csv(csv_path)
    records = df.fillna("").to_dict(orient="records")

    _ensure_parent(out_path)
    out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    return f"Converted {len(records)} CSV rows to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def create_chart_from_csv(
    path: str,
    x_column: str,
    y_column: str,
    chart_type: str = "bar",
    output_path: str = "./output/chart.png",
) -> str:
    """
    Create a simple chart from a CSV file.
    chart_type can be: bar, line, scatter
    """
    pd = _load_pandas()

    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        raise ImportError("This tool requires matplotlib. Install with: pip install matplotlib") from exc

    csv_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    df = pd.read_csv(csv_path)

    if x_column not in df.columns:
        raise KeyError(f"x_column not found: {x_column}")

    if y_column not in df.columns:
        raise KeyError(f"y_column not found: {y_column}")

    _ensure_parent(out_path)

    plt.figure(figsize=(10, 6))

    if chart_type == "bar":
        plt.bar(df[x_column].astype(str), df[y_column])
    elif chart_type == "line":
        plt.plot(df[x_column], df[y_column], marker="o")
    elif chart_type == "scatter":
        plt.scatter(df[x_column], df[y_column])
    else:
        raise ValueError("chart_type must be one of: bar, line, scatter")

    plt.xlabel(x_column)
    plt.ylabel(y_column)
    plt.title(f"{y_column} by {x_column}")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()

    return f"Chart saved to {out_path.relative_to(PROJECT_ROOT)}"


# ---------------------------------------------------------------------
# PDF tools
# ---------------------------------------------------------------------


@tool
def read_pdf(path: str, max_chars: int = 15000) -> str:
    """
    Read text from a PDF file.
    Requires pypdf: pip install pypdf
    """
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise ImportError("This tool requires pypdf. Install with: pip install pypdf") from exc

    pdf_path = _ensure_safe_path(path, must_exist=True)

    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {path}")

    reader = PdfReader(str(pdf_path))

    chunks = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        chunks.append(f"\n--- Page {index} ---\n{text}")

    return _shorten("\n".join(chunks), max_chars=max_chars)


@tool
def pdf_info(path: str) -> str:
    """
    Get basic PDF metadata and page count.
    Requires pypdf: pip install pypdf
    """
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise ImportError("This tool requires pypdf. Install with: pip install pypdf") from exc

    pdf_path = _ensure_safe_path(path, must_exist=True)
    reader = PdfReader(str(pdf_path))

    metadata = reader.metadata or {}

    info = {
        "path": str(pdf_path.relative_to(PROJECT_ROOT)),
        "pages": len(reader.pages),
        "metadata": {str(k): str(v) for k, v in metadata.items()},
    }

    return json.dumps(info, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------
# Text analysis tools
# ---------------------------------------------------------------------


@tool
def extract_keywords(text: str, top_n: int = 15) -> str:
    """
    Extract simple keywords from text using local frequency analysis.
    """
    try:
        top_n = int(top_n)
    except Exception:
        top_n = 15

    top_n = max(1, min(top_n, 100))

    stopwords = {
        "the", "and", "for", "are", "but", "not", "you", "your", "with", "this",
        "that", "from", "have", "has", "had", "was", "were", "will", "would",
        "there", "their", "they", "them", "his", "her", "she", "him", "our",
        "out", "about", "can", "could", "should", "into", "than", "then",
        "its", "it's", "also", "these", "those", "a", "an", "in", "on", "of",
        "to", "is", "as", "at", "by", "be", "or", "if", "we", "it", "he",
    }

    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(text).lower())
    filtered = [word for word in words if word not in stopwords]

    counts = Counter(filtered).most_common(top_n)

    if not counts:
        return "No keywords found."

    return "\n".join([f"{word}: {count}" for word, count in counts])

@tool
def count_words(text: str) -> str:
    """
    Count words, characters, sentences, and paragraphs in text.
    """
    words = re.findall(r"\b\w+\b", text)
    sentences = re.split(r"[.!?]+", text)
    paragraphs = [p for p in text.split("\n\n") if p.strip()]

    result = {
        "characters": len(text),
        "words": len(words),
        "sentences": len([s for s in sentences if s.strip()]),
        "paragraphs": len(paragraphs),
    }

    return json.dumps(result, indent=2)


@tool
def compare_texts(text_a: str, text_b: str) -> str:
    """
    Compare two texts using simple word overlap.
    Useful for basic similarity checks.
    """
    words_a = set(re.findall(r"\b\w+\b", text_a.lower()))
    words_b = set(re.findall(r"\b\w+\b", text_b.lower()))

    if not words_a and not words_b:
        similarity = 1.0
    elif not words_a or not words_b:
        similarity = 0.0
    else:
        similarity = len(words_a & words_b) / len(words_a | words_b)

    result = {
        "unique_words_a": len(words_a),
        "unique_words_b": len(words_b),
        "shared_words": len(words_a & words_b),
        "jaccard_similarity": round(similarity, 4),
        "shared_keywords": sorted(list(words_a & words_b))[:50],
    }

    return json.dumps(result, indent=2, ensure_ascii=False)


@tool
def clean_text(text: str) -> str:
    """
    Clean text by removing extra spaces and blank lines.
    """
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------
# Markdown and report tools
# ---------------------------------------------------------------------


@tool
def create_markdown_report(
    title: str,
    content: str,
    output_path: str = "./output/report.md",
) -> str:
    """
    Create a clean markdown report.
    """
    out_path = _ensure_safe_path(output_path, must_exist=False)
    _ensure_parent(out_path)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = f"""# {title}

Generated on: {now}

---

{content.strip()}

---
Generated locally with AgentKit.
"""

    out_path.write_text(report, encoding="utf-8")

    return f"Markdown report created: {out_path.relative_to(PROJECT_ROOT)}"


@tool
def create_todo_file(items_json: str, output_path: str = "./output/todo.md") -> str:
    """
    Create a markdown todo checklist.
    items_json must be a JSON list of strings.
    """
    items = json.loads(items_json)

    if not isinstance(items, list):
        raise ValueError("items_json must be a JSON list of strings.")

    out_path = _ensure_safe_path(output_path, must_exist=False)
    _ensure_parent(out_path)

    lines = ["# Todo List", ""]
    for item in items:
        lines.append(f"- [ ] {item}")

    out_path.write_text("\n".join(lines), encoding="utf-8")

    return f"Todo file created: {out_path.relative_to(PROJECT_ROOT)}"


@tool
def create_table_markdown(rows_json: str, output_path: str = "./output/table.md") -> str:
    """
    Create a markdown table from JSON rows.
    rows_json must be a JSON list of objects.
    """
    rows = json.loads(rows_json)

    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("rows_json must be a JSON list of objects.")

    out_path = _ensure_safe_path(output_path, must_exist=False)
    _ensure_parent(out_path)

    if not rows:
        out_path.write_text("No data.", encoding="utf-8")
        return f"Empty table file created: {out_path.relative_to(PROJECT_ROOT)}"

    columns = sorted({key for row in rows for key in row.keys()})

    lines = []
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join(["---"] * len(columns)) + " |")

    for row in rows:
        values = [str(row.get(col, "")).replace("\n", " ") for col in columns]
        lines.append("| " + " | ".join(values) + " |")

    out_path.write_text("\n".join(lines), encoding="utf-8")

    return f"Markdown table created: {out_path.relative_to(PROJECT_ROOT)}"


# ---------------------------------------------------------------------
# JSON tools
# ---------------------------------------------------------------------


@tool
def read_json(path: str) -> str:
    """
    Read and pretty-print a JSON file.
    """
    json_path = _ensure_safe_path(path, must_exist=True)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return json.dumps(data, indent=2, ensure_ascii=False, default=_json_default)


@tool
def write_json(path: str, data_json: str) -> str:
    """
    Write JSON data to a file.
    data_json must be valid JSON.
    """
    out_path = _ensure_safe_path(path, must_exist=False)
    _ensure_parent(out_path)

    data = json.loads(data_json)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    return f"JSON written to {out_path.relative_to(PROJECT_ROOT)}"


# ---------------------------------------------------------------------
# Local memory tools
# ---------------------------------------------------------------------


MEMORY_PATH = PROJECT_ROOT / "memory.json"


def _load_memory() -> Dict[str, Any]:
    if not MEMORY_PATH.exists():
        return {}

    try:
        return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_memory(memory: Dict[str, Any]):
    MEMORY_PATH.write_text(json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8")


@tool
def memory_set(key: str, value: str) -> str:
    """
    Save a value to local memory.json.
    """
    memory = _load_memory()
    memory[key] = value
    _save_memory(memory)
    return f"Saved memory key: {key}"


@tool
def memory_get(key: str) -> str:
    """
    Get a value from local memory.json.
    """
    memory = _load_memory()

    if key not in memory:
        return f"No memory found for key: {key}"

    return str(memory[key])


@tool
def memory_list() -> str:
    """
    List all memory keys.
    """
    memory = _load_memory()

    if not memory:
        return "Memory is empty."

    return "\n".join(memory.keys())


# ---------------------------------------------------------------------
# Lightweight math and statistics tools
# ---------------------------------------------------------------------


@tool
def calculate(expression: str) -> str:
    """
    Safely evaluate a basic math expression.
    Supports numbers and math functions like sqrt, sin, cos, log.
    """
    allowed_names = {
        name: getattr(math, name)
        for name in dir(math)
        if not name.startswith("_")
    }

    allowed_names.update(
        {
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "sum": sum,
        }
    )

    if "__" in expression or "import" in expression or "open(" in expression:
        raise PermissionError("Unsafe expression blocked.")

    result = eval(expression, {"__builtins__": {}}, allowed_names)
    return str(result)


@tool
def basic_stats(numbers_json: str) -> str:
    """
    Calculate basic statistics from a JSON list of numbers.
    """
    numbers = json.loads(numbers_json)

    if not isinstance(numbers, list) or not all(isinstance(x, (int, float)) for x in numbers):
        raise ValueError("numbers_json must be a JSON list of numbers.")

    if not numbers:
        raise ValueError("numbers_json cannot be empty.")

    result = {
        "count": len(numbers),
        "sum": sum(numbers),
        "min": min(numbers),
        "max": max(numbers),
        "mean": statistics.mean(numbers),
        "median": statistics.median(numbers),
    }

    if len(numbers) > 1:
        result["stdev"] = statistics.stdev(numbers)

    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------
# Project setup tool
# ---------------------------------------------------------------------


@tool
def ensure_project_folders() -> str:
    """
    Create common project folders: input, output, data, reports.
    """
    folders = ["input", "output", "data", "reports"]

    for folder in folders:
        path = _ensure_safe_path(folder, must_exist=False)
        path.mkdir(parents=True, exist_ok=True)

    return "Created project folders: input, output, data, reports"


# agentkit_extra_tools_patch.py
#
# Copy/paste instructions:
# 1) Paste the ADDITIONAL TOOLS section above your existing "# Tool bundles" section in tools.py.
# 2) Replace your existing "# Tool bundles" section with the REPLACEMENT TOOL BUNDLES section below.
#
# This patch assumes your existing tools.py already has these imports/helpers:
# csv, json, math, os, re, shutil, statistics, Counter, datetime, Path, Any, Dict, List, Optional
# _ensure_safe_path, _ensure_parent, _is_inside, _safe_text_read, _shorten, _json_default, _load_pandas, PROJECT_ROOT

# =====================================================================
# ADDITIONAL TOOLS
# Paste this above your existing "# Tool bundles" section.
# =====================================================================


# ---------------------------------------------------------------------
# Additional file, folder, and workspace tools
# ---------------------------------------------------------------------


@tool
def list_files_by_extension(folder: str = ".", extension: str = ".txt", recursive: bool = True, max_files: int = 200) -> str:
    """
    List files by extension inside a folder.
    Example: extension='.pdf' or 'pdf'
    """
    folder_path = _ensure_safe_path(folder, must_exist=True)

    if not folder_path.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    extension = extension.strip()
    if not extension.startswith("."):
        extension = "." + extension

    pattern = "**/*" if recursive else "*"
    matches = []

    for path in folder_path.glob(pattern):
        if len(matches) >= max_files:
            break
        if path.is_file() and path.suffix.lower() == extension.lower():
            rel = path.relative_to(PROJECT_ROOT) if _is_inside(PROJECT_ROOT, path) else path
            matches.append(str(rel))

    if not matches:
        return f"No {extension} files found in {folder}"

    return "\n".join(matches)


@tool
def list_recent_files(folder: str = ".", max_files: int = 20, recursive: bool = True) -> str:
    """
    List recently modified files in a folder.
    """
    folder_path = _ensure_safe_path(folder, must_exist=True)

    if not folder_path.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    pattern = "**/*" if recursive else "*"
    files = [p for p in folder_path.glob(pattern) if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    output = []
    for path in files[:max_files]:
        rel = path.relative_to(PROJECT_ROOT) if _is_inside(PROJECT_ROOT, path) else path
        modified = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        output.append(f"{rel} | {path.stat().st_size} bytes | modified {modified}")

    if not output:
        return f"No files found in {folder}"

    return "\n".join(output)


@tool
def find_duplicate_files(folder: str = ".", recursive: bool = True, max_files: int = 1000) -> str:
    """
    Find duplicate files by SHA256 hash.
    """
    import hashlib

    folder_path = _ensure_safe_path(folder, must_exist=True)

    if not folder_path.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    pattern = "**/*" if recursive else "*"
    hashes: Dict[str, List[str]] = {}
    count = 0

    for path in folder_path.glob(pattern):
        if count >= max_files:
            break
        if not path.is_file():
            continue

        count += 1
        hasher = hashlib.sha256()

        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                hasher.update(chunk)

        digest = hasher.hexdigest()
        rel = path.relative_to(PROJECT_ROOT) if _is_inside(PROJECT_ROOT, path) else path
        hashes.setdefault(digest, []).append(str(rel))

    duplicates = {digest: files for digest, files in hashes.items() if len(files) > 1}

    if not duplicates:
        return "No duplicate files found."

    return json.dumps(duplicates, indent=2, ensure_ascii=False)


@tool
def get_folder_tree(folder: str = ".", max_depth: int = 3, max_items: int = 300) -> str:
    """
    Create a simple text tree of a folder.
    """
    root = _ensure_safe_path(folder, must_exist=True)

    if not root.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    max_depth = max(1, min(int(max_depth), 10))
    max_items = max(1, min(int(max_items), 2000))
    lines = []
    count = 0

    def walk(path: Path, prefix: str = "", depth: int = 0):
        nonlocal count

        if count >= max_items or depth > max_depth:
            return

        items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

        for index, item in enumerate(items):
            if count >= max_items:
                lines.append(prefix + "... stopped after max_items")
                return

            connector = "└── " if index == len(items) - 1 else "├── "
            suffix = "/" if item.is_dir() else ""
            lines.append(prefix + connector + item.name + suffix)
            count += 1

            if item.is_dir() and depth < max_depth:
                extension = "    " if index == len(items) - 1 else "│   "
                walk(item, prefix + extension, depth + 1)

    lines.append(str(root.relative_to(PROJECT_ROOT)) + "/")
    walk(root)

    return "\n".join(lines)


@tool
def make_file_manifest(folder: str = ".", output_path: str = "./output/file_manifest.json", recursive: bool = True) -> str:
    """
    Create a JSON manifest of files with path, size, extension, and modified time.
    """
    folder_path = _ensure_safe_path(folder, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    if not folder_path.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    pattern = "**/*" if recursive else "*"
    manifest = []

    for path in folder_path.glob(pattern):
        if not path.is_file():
            continue

        rel = path.relative_to(PROJECT_ROOT) if _is_inside(PROJECT_ROOT, path) else path
        stat = path.stat()

        manifest.append(
            {
                "path": str(rel),
                "name": path.name,
                "extension": path.suffix.lower(),
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
        )

    _ensure_parent(out_path)
    out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return f"File manifest with {len(manifest)} files written to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def batch_rename_files(folder: str, prefix: str = "", suffix: str = "", extension_filter: str = "", dry_run: bool = True) -> str:
    """
    Batch rename files by adding a prefix and/or suffix.
    Uses dry_run=True by default.
    """
    folder_path = _ensure_safe_path(folder, must_exist=True)

    if not folder_path.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    extension_filter = extension_filter.strip()
    if extension_filter and not extension_filter.startswith("."):
        extension_filter = "." + extension_filter

    changes = []

    for path in sorted(folder_path.iterdir()):
        if not path.is_file():
            continue

        if extension_filter and path.suffix.lower() != extension_filter.lower():
            continue

        new_name = f"{prefix}{path.stem}{suffix}{path.suffix}"
        new_path = path.parent / new_name

        if new_path == path:
            continue

        if new_path.exists():
            changes.append({"old": path.name, "new": new_name, "status": "skipped_exists"})
            continue

        changes.append({"old": path.name, "new": new_name, "status": "planned" if dry_run else "renamed"})

        if not dry_run:
            path.rename(new_path)

    return json.dumps(
        {
            "dry_run": dry_run,
            "changes": changes,
        },
        indent=2,
        ensure_ascii=False,
    )


@tool
def zip_folder(folder: str, output_path: str = "./output/archive.zip") -> str:
    """
    Zip a folder into an archive.
    """
    import zipfile

    folder_path = _ensure_safe_path(folder, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    if not folder_path.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    _ensure_parent(out_path)

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in folder_path.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(folder_path))

    return f"Zipped folder {folder_path.relative_to(PROJECT_ROOT)} to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def unzip_file(path: str, output_folder: str = "./output/unzipped") -> str:
    """
    Unzip a .zip file into a folder.
    """
    import zipfile

    zip_path = _ensure_safe_path(path, must_exist=True)
    out_folder = _ensure_safe_path(output_folder, must_exist=False)

    if zip_path.suffix.lower() != ".zip":
        raise ValueError(f"Not a zip file: {path}")

    out_folder.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            target = (out_folder / member).resolve()
            if not _is_inside(out_folder.resolve(), target):
                raise PermissionError(f"Unsafe zip member blocked: {member}")
        zf.extractall(out_folder)

    return f"Unzipped {zip_path.relative_to(PROJECT_ROOT)} to {out_folder.relative_to(PROJECT_ROOT)}"


@tool
def delete_file_safe(path: str, confirm: bool = False) -> str:
    """
    Delete a file only when confirm=True.
    """
    file_path = _ensure_safe_path(path, must_exist=True)

    if not file_path.is_file():
        raise IsADirectoryError(f"Not a file: {path}")

    if not confirm:
        return f"Dry run only. To delete {file_path.relative_to(PROJECT_ROOT)}, call with confirm=True."

    file_path.unlink()
    return f"Deleted file: {file_path.relative_to(PROJECT_ROOT)}"


# ---------------------------------------------------------------------
# Additional CSV and spreadsheet tools
# ---------------------------------------------------------------------


@tool
def csv_column_names(path: str) -> str:
    """
    Return CSV column names.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    df = pd.read_csv(csv_path, nrows=1)
    return json.dumps(list(df.columns), indent=2, ensure_ascii=False)


@tool
def csv_shape(path: str) -> str:
    """
    Return CSV row and column count.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    df = pd.read_csv(csv_path)
    return json.dumps({"rows": int(df.shape[0]), "columns": int(df.shape[1])}, indent=2)


@tool
def csv_missing_report(path: str) -> str:
    """
    Report missing values in a CSV.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    df = pd.read_csv(csv_path)

    result = {}
    for col in df.columns:
        missing = int(df[col].isna().sum())
        result[col] = {
            "missing_count": missing,
            "missing_percent": round((missing / len(df)) * 100, 2) if len(df) else 0,
        }

    return json.dumps(result, indent=2, ensure_ascii=False)


@tool
def csv_value_counts(path: str, column: str, top_n: int = 20) -> str:
    """
    Count top values in a CSV column.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    df = pd.read_csv(csv_path)

    if column not in df.columns:
        raise KeyError(f"Column not found: {column}")

    top_n = max(1, min(int(top_n), 100))
    counts = df[column].fillna("").astype(str).value_counts().head(top_n).to_dict()

    return json.dumps(counts, indent=2, ensure_ascii=False)


@tool
def csv_groupby_summary(path: str, group_column: str, value_column: str, operation: str = "mean") -> str:
    """
    Group a CSV by one column and summarize another column.
    operation can be: mean, sum, count, min, max, median
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    df = pd.read_csv(csv_path)

    if group_column not in df.columns:
        raise KeyError(f"group_column not found: {group_column}")
    if value_column not in df.columns:
        raise KeyError(f"value_column not found: {value_column}")

    operation = operation.lower().strip()

    if operation == "mean":
        result = df.groupby(group_column)[value_column].mean()
    elif operation == "sum":
        result = df.groupby(group_column)[value_column].sum()
    elif operation == "count":
        result = df.groupby(group_column)[value_column].count()
    elif operation == "min":
        result = df.groupby(group_column)[value_column].min()
    elif operation == "max":
        result = df.groupby(group_column)[value_column].max()
    elif operation == "median":
        result = df.groupby(group_column)[value_column].median()
    else:
        raise ValueError("operation must be one of: mean, sum, count, min, max, median")

    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=_json_default)


@tool
def sort_csv(path: str, column: str, ascending: bool = True, output_path: str = "./output/sorted.csv") -> str:
    """
    Sort a CSV by a column and save the result.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    df = pd.read_csv(csv_path)

    if column not in df.columns:
        raise KeyError(f"Column not found: {column}")

    sorted_df = df.sort_values(by=column, ascending=ascending)
    _ensure_parent(out_path)
    sorted_df.to_csv(out_path, index=False)

    return f"Sorted {len(sorted_df)} rows by {column} and saved to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def select_csv_columns(path: str, columns_json: str, output_path: str = "./output/selected_columns.csv") -> str:
    """
    Select specific CSV columns and save the result.
    columns_json must be a JSON list of column names.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    columns = json.loads(columns_json)
    if not isinstance(columns, list):
        raise ValueError("columns_json must be a JSON list.")

    df = pd.read_csv(csv_path)
    missing = [col for col in columns if col not in df.columns]

    if missing:
        raise KeyError(f"Missing columns: {missing}")

    selected = df[columns]
    _ensure_parent(out_path)
    selected.to_csv(out_path, index=False)

    return f"Saved selected columns to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def merge_csv_files(left_path: str, right_path: str, on_column: str, output_path: str = "./output/merged.csv", how: str = "inner") -> str:
    """
    Merge two CSV files on a shared column.
    how can be: inner, left, right, outer
    """
    pd = _load_pandas()

    left = _ensure_safe_path(left_path, must_exist=True)
    right = _ensure_safe_path(right_path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    if how not in ["inner", "left", "right", "outer"]:
        raise ValueError("how must be one of: inner, left, right, outer")

    left_df = pd.read_csv(left)
    right_df = pd.read_csv(right)

    if on_column not in left_df.columns:
        raise KeyError(f"{on_column} not found in left CSV")
    if on_column not in right_df.columns:
        raise KeyError(f"{on_column} not found in right CSV")

    merged = left_df.merge(right_df, on=on_column, how=how)
    _ensure_parent(out_path)
    merged.to_csv(out_path, index=False)

    return f"Merged CSV saved to {out_path.relative_to(PROJECT_ROOT)} with {len(merged)} rows"


@tool
def deduplicate_csv(path: str, output_path: str = "./output/deduplicated.csv", subset_json: str = "") -> str:
    """
    Remove duplicate rows from a CSV.
    subset_json can be a JSON list of columns to consider.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    df = pd.read_csv(csv_path)
    subset = None

    if subset_json.strip():
        subset = json.loads(subset_json)
        if not isinstance(subset, list):
            raise ValueError("subset_json must be a JSON list.")
        missing = [col for col in subset if col not in df.columns]
        if missing:
            raise KeyError(f"Missing columns: {missing}")

    deduped = df.drop_duplicates(subset=subset)
    _ensure_parent(out_path)
    deduped.to_csv(out_path, index=False)

    return f"Removed {len(df) - len(deduped)} duplicate rows and saved to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def sample_csv(path: str, n: int = 10, output_path: str = "./output/sample.csv", random_state: int = 42) -> str:
    """
    Sample n rows from a CSV and save the result.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    df = pd.read_csv(csv_path)
    n = max(1, min(int(n), len(df)))

    sampled = df.sample(n=n, random_state=random_state)
    _ensure_parent(out_path)
    sampled.to_csv(out_path, index=False)

    return f"Sampled {n} rows and saved to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def csv_to_markdown_table(path: str, output_path: str = "./output/table_from_csv.md", max_rows: int = 30) -> str:
    """
    Convert a CSV preview to a markdown table.
    """
    pd = _load_pandas()
    csv_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    df = pd.read_csv(csv_path).head(max_rows).fillna("")
    try:
        table = df.to_markdown(index=False)
    except Exception:
        table = df.to_csv(index=False)

    _ensure_parent(out_path)
    out_path.write_text(table, encoding="utf-8")

    return f"Markdown table saved to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def read_excel(path: str, sheet_name: str = "", max_rows: int = 20) -> str:
    """
    Read an Excel file and return a preview.
    Requires openpyxl for .xlsx files.
    """
    pd = _load_pandas()
    excel_path = _ensure_safe_path(path, must_exist=True)

    if sheet_name.strip():
        df = pd.read_excel(excel_path, sheet_name=sheet_name)
    else:
        df = pd.read_excel(excel_path)

    info = {
        "path": str(excel_path.relative_to(PROJECT_ROOT)),
        "rows": int(len(df)),
        "columns": list(df.columns),
        "preview": df.head(max_rows).fillna("").to_dict(orient="records"),
    }

    return json.dumps(info, indent=2, ensure_ascii=False, default=_json_default)


@tool
def excel_sheet_names(path: str) -> str:
    """
    List sheet names in an Excel file.
    """
    pd = _load_pandas()
    excel_path = _ensure_safe_path(path, must_exist=True)
    xls = pd.ExcelFile(excel_path)
    return json.dumps(xls.sheet_names, indent=2, ensure_ascii=False)


@tool
def excel_to_csv(path: str, output_path: str = "./output/excel_converted.csv", sheet_name: str = "") -> str:
    """
    Convert an Excel sheet to CSV.
    """
    pd = _load_pandas()
    excel_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    if sheet_name.strip():
        df = pd.read_excel(excel_path, sheet_name=sheet_name)
    else:
        df = pd.read_excel(excel_path)

    _ensure_parent(out_path)
    df.to_csv(out_path, index=False)

    return f"Converted Excel to CSV: {out_path.relative_to(PROJECT_ROOT)}"


# ---------------------------------------------------------------------
# Additional text and document tools
# ---------------------------------------------------------------------


@tool
def summarize_text_stats(text: str) -> str:
    """
    Produce text statistics including reading time and average word length.
    """
    words = re.findall(r"\b\w+\b", str(text))
    sentences = [s for s in re.split(r"[.!?]+", str(text)) if s.strip()]
    reading_minutes = round(len(words) / 200, 2)

    avg_word_length = 0
    if words:
        avg_word_length = round(sum(len(w) for w in words) / len(words), 2)

    result = {
        "characters": len(str(text)),
        "words": len(words),
        "sentences": len(sentences),
        "average_word_length": avg_word_length,
        "estimated_reading_minutes": reading_minutes,
    }

    return json.dumps(result, indent=2)


@tool
def extract_emails(text: str) -> str:
    """
    Extract email addresses from text.
    """
    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", str(text))))
    return json.dumps(emails, indent=2)


@tool
def extract_urls(text: str) -> str:
    """
    Extract URLs from text.
    """
    urls = sorted(set(re.findall(r"https?://[^\s\]\)\"']+", str(text))))
    return json.dumps(urls, indent=2)


@tool
def extract_phone_numbers(text: str) -> str:
    """
    Extract simple phone-number-like patterns from text.
    """
    phones = sorted(set(re.findall(r"(?:\+?\d[\d\s().-]{7,}\d)", str(text))))
    return json.dumps(phones, indent=2)


@tool
def extract_dates(text: str) -> str:
    """
    Extract common date-like patterns from text.
    """
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
        r"\b\d{1,2}-\d{1,2}-\d{2,4}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{2,4}\b",
    ]

    dates = []
    for pattern in patterns:
        dates.extend(re.findall(pattern, str(text), flags=re.IGNORECASE))

    return json.dumps(sorted(set(dates)), indent=2, ensure_ascii=False)


@tool
def split_text_into_chunks(text: str, chunk_size: int = 1000, overlap: int = 100) -> str:
    """
    Split text into chunks with optional overlap.
    """
    text = str(text)
    chunk_size = max(100, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size - 1))

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap

    return json.dumps(
        [{"index": i + 1, "length": len(chunk), "text": chunk} for i, chunk in enumerate(chunks)],
        indent=2,
        ensure_ascii=False,
    )


@tool
def text_to_bullets(text: str, max_bullets: int = 10) -> str:
    """
    Convert text into simple sentence-based bullet points.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(text)) if s.strip()]
    max_bullets = max(1, min(int(max_bullets), 50))
    bullets = [f"- {sentence}" for sentence in sentences[:max_bullets]]
    return "\n".join(bullets) if bullets else "No bullet points generated."


@tool
def remove_markdown(text: str) -> str:
    """
    Remove common markdown syntax from text.
    """
    text = str(text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~>#-]+", " ", text)
    return clean_text(text)


@tool
def markdown_to_html(path: str, output_path: str = "./output/converted.html") -> str:
    """
    Convert a markdown file to simple HTML.
    Uses markdown package if installed, otherwise wraps text in <pre>.
    """
    md_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    markdown_text = _safe_text_read(md_path, max_chars=200000)

    try:
        import markdown
        body = markdown.markdown(markdown_text, extensions=["tables", "fenced_code"])
    except Exception:
        import html
        body = "<pre>" + html.escape(markdown_text) + "</pre>"

    html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{md_path.name}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 860px; margin: 40px auto; line-height: 1.6; padding: 0 20px; }}
    pre, code {{ background: #f5f5f5; border-radius: 8px; padding: 2px 5px; }}
    pre {{ padding: 14px; overflow: auto; }}
    table {{ border-collapse: collapse; width: 100%; }}
    td, th {{ border: 1px solid #ddd; padding: 8px; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""

    _ensure_parent(out_path)
    out_path.write_text(html_doc, encoding="utf-8")

    return f"HTML saved to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def create_minutes_from_notes(notes: str, output_path: str = "./output/meeting_minutes.md") -> str:
    """
    Create a simple meeting minutes markdown file from notes.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cleaned = clean_text(notes)
    bullets = text_to_bullets(cleaned, max_bullets=20)

    content = f"""# Meeting Minutes

Generated on: {now}

## Notes

{cleaned}

## Key Points

{bullets}

## Action Items

- [ ] Review notes
- [ ] Assign owners
- [ ] Confirm deadlines
"""

    out_path = _ensure_safe_path(output_path, must_exist=False)
    _ensure_parent(out_path)
    out_path.write_text(content, encoding="utf-8")

    return f"Meeting minutes created: {out_path.relative_to(PROJECT_ROOT)}"


@tool
def create_brief_from_text(title: str, text: str, output_path: str = "./output/brief.md") -> str:
    """
    Create a simple brief document from text.
    """
    stats = json.loads(summarize_text_stats(text))
    keywords = extract_keywords(text, top_n=12)
    bullets = text_to_bullets(text, max_bullets=12)

    content = f"""# {title}

## Summary Bullets

{bullets}

## Keywords

{keywords}

## Text Statistics

```json
{json.dumps(stats, indent=2)}
```

## Source Text

{text.strip()}
"""

    out_path = _ensure_safe_path(output_path, must_exist=False)
    _ensure_parent(out_path)
    out_path.write_text(content, encoding="utf-8")

    return f"Brief created: {out_path.relative_to(PROJECT_ROOT)}"


# ---------------------------------------------------------------------
# Additional JSON and configuration tools
# ---------------------------------------------------------------------


@tool
def validate_json_text(data_json: str) -> str:
    """
    Validate JSON text.
    """
    try:
        data = json.loads(data_json)
        return json.dumps(
            {
                "valid": True,
                "type": type(data).__name__,
                "preview": data if isinstance(data, (dict, list)) else str(data),
            },
            indent=2,
            ensure_ascii=False,
            default=_json_default,
        )
    except Exception as exc:
        return json.dumps({"valid": False, "error": str(exc)}, indent=2)


@tool
def json_keys(path: str) -> str:
    """
    List top-level keys in a JSON object file.
    """
    json_path = _ensure_safe_path(path, must_exist=True)
    data = json.loads(json_path.read_text(encoding="utf-8"))

    if isinstance(data, dict):
        return json.dumps(list(data.keys()), indent=2, ensure_ascii=False)

    if isinstance(data, list):
        keys = sorted({key for item in data if isinstance(item, dict) for key in item.keys()})
        return json.dumps(keys, indent=2, ensure_ascii=False)

    return "JSON is not an object or list of objects."


@tool
def json_get_value(path: str, key_path: str) -> str:
    """
    Get a nested value from JSON using dot notation.
    Example: user.name or items.0.title
    """
    json_path = _ensure_safe_path(path, must_exist=True)
    data = json.loads(json_path.read_text(encoding="utf-8"))

    current = data
    for part in key_path.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(f"Cannot access {part} inside non-container value.")

    return json.dumps(current, indent=2, ensure_ascii=False, default=_json_default)


@tool
def json_set_value(path: str, key_path: str, value_json: str, output_path: str = "") -> str:
    """
    Set a nested JSON value using dot notation and write the result.
    If output_path is empty, overwrites the original file.
    """
    json_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False) if output_path.strip() else json_path

    data = json.loads(json_path.read_text(encoding="utf-8"))
    value = json.loads(value_json)

    parts = key_path.split(".")
    current = data

    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current.setdefault(part, {})

    last = parts[-1]
    if isinstance(current, list):
        current[int(last)] = value
    else:
        current[last] = value

    _ensure_parent(out_path)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    return f"JSON value set and written to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def json_to_csv(path: str, output_path: str = "./output/json_converted.csv") -> str:
    """
    Convert a JSON list of objects to CSV.
    """
    pd = _load_pandas()
    json_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    data = json.loads(json_path.read_text(encoding="utf-8"))

    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise ValueError("JSON must be an object or list of objects.")

    df = pd.DataFrame(data)
    _ensure_parent(out_path)
    df.to_csv(out_path, index=False)

    return f"Converted JSON to CSV: {out_path.relative_to(PROJECT_ROOT)}"


# ---------------------------------------------------------------------
# Additional PDF and document tools
# ---------------------------------------------------------------------


@tool
def split_pdf_pages(path: str, output_folder: str = "./output/pdf_pages") -> str:
    """
    Split a PDF into one PDF per page.
    Requires pypdf.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as exc:
        raise ImportError("This tool requires pypdf. Install with: pip install pypdf") from exc

    pdf_path = _ensure_safe_path(path, must_exist=True)
    out_folder = _ensure_safe_path(output_folder, must_exist=False)
    out_folder.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(pdf_path))
    created = []

    for i, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        out_path = out_folder / f"page_{i}.pdf"
        with out_path.open("wb") as f:
            writer.write(f)
        created.append(str(out_path.relative_to(PROJECT_ROOT)))

    return json.dumps(created, indent=2, ensure_ascii=False)


@tool
def merge_pdfs(paths_json: str, output_path: str = "./output/merged.pdf") -> str:
    """
    Merge PDFs.
    paths_json must be a JSON list of PDF paths.
    Requires pypdf.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as exc:
        raise ImportError("This tool requires pypdf. Install with: pip install pypdf") from exc

    paths = json.loads(paths_json)
    if not isinstance(paths, list):
        raise ValueError("paths_json must be a JSON list.")

    writer = PdfWriter()

    for path in paths:
        pdf_path = _ensure_safe_path(str(path), must_exist=True)
        reader = PdfReader(str(pdf_path))
        for page in reader.pages:
            writer.add_page(page)

    out_path = _ensure_safe_path(output_path, must_exist=False)
    _ensure_parent(out_path)

    with out_path.open("wb") as f:
        writer.write(f)

    return f"Merged PDF saved to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def extract_pdf_pages_text(path: str, start_page: int = 1, end_page: int = 1, max_chars: int = 12000) -> str:
    """
    Extract text from a page range in a PDF.
    Page numbers are 1-based.
    """
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise ImportError("This tool requires pypdf. Install with: pip install pypdf") from exc

    pdf_path = _ensure_safe_path(path, must_exist=True)
    reader = PdfReader(str(pdf_path))

    start_page = max(1, int(start_page))
    end_page = min(len(reader.pages), int(end_page))

    chunks = []
    for page_number in range(start_page, end_page + 1):
        text = reader.pages[page_number - 1].extract_text() or ""
        chunks.append(f"\n--- Page {page_number} ---\n{text}")

    return _shorten("\n".join(chunks), max_chars=max_chars)


# ---------------------------------------------------------------------
# Image tools
# ---------------------------------------------------------------------


@tool
def image_info(path: str) -> str:
    """
    Get image metadata.
    Requires pillow: pip install pillow
    """
    try:
        from PIL import Image
    except Exception as exc:
        raise ImportError("This tool requires pillow. Install with: pip install pillow") from exc

    image_path = _ensure_safe_path(path, must_exist=True)

    with Image.open(image_path) as img:
        info = {
            "path": str(image_path.relative_to(PROJECT_ROOT)),
            "format": img.format,
            "mode": img.mode,
            "width": img.width,
            "height": img.height,
            "size_bytes": image_path.stat().st_size,
        }

    return json.dumps(info, indent=2, ensure_ascii=False)


@tool
def resize_image(path: str, output_path: str = "./output/resized.png", width: int = 800, height: int = 600) -> str:
    """
    Resize an image.
    Requires pillow: pip install pillow
    """
    try:
        from PIL import Image
    except Exception as exc:
        raise ImportError("This tool requires pillow. Install with: pip install pillow") from exc

    image_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    width = max(1, int(width))
    height = max(1, int(height))

    with Image.open(image_path) as img:
        resized = img.resize((width, height))
        _ensure_parent(out_path)
        resized.save(out_path)

    return f"Resized image saved to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def convert_image_format(path: str, output_path: str = "./output/converted.png") -> str:
    """
    Convert image format based on output_path extension.
    Requires pillow: pip install pillow
    """
    try:
        from PIL import Image
    except Exception as exc:
        raise ImportError("This tool requires pillow. Install with: pip install pillow") from exc

    image_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    with Image.open(image_path) as img:
        _ensure_parent(out_path)
        if img.mode in ["RGBA", "P"] and out_path.suffix.lower() in [".jpg", ".jpeg"]:
            img = img.convert("RGB")
        img.save(out_path)

    return f"Converted image saved to {out_path.relative_to(PROJECT_ROOT)}"


@tool
def create_thumbnail(path: str, output_path: str = "./output/thumbnail.png", max_size: int = 256) -> str:
    """
    Create an image thumbnail.
    Requires pillow: pip install pillow
    """
    try:
        from PIL import Image
    except Exception as exc:
        raise ImportError("This tool requires pillow. Install with: pip install pillow") from exc

    image_path = _ensure_safe_path(path, must_exist=True)
    out_path = _ensure_safe_path(output_path, must_exist=False)

    with Image.open(image_path) as img:
        img.thumbnail((int(max_size), int(max_size)))
        _ensure_parent(out_path)
        img.save(out_path)

    return f"Thumbnail saved to {out_path.relative_to(PROJECT_ROOT)}"


# ---------------------------------------------------------------------
# Code and project analysis tools
# ---------------------------------------------------------------------


@tool
def list_python_functions(path: str) -> str:
    """
    List Python functions and classes in a .py file.
    """
    import ast

    py_path = _ensure_safe_path(path, must_exist=True)
    tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"))

    items = []

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            items.append({"type": "function", "name": node.name, "line": node.lineno})
        elif isinstance(node, ast.AsyncFunctionDef):
            items.append({"type": "async_function", "name": node.name, "line": node.lineno})
        elif isinstance(node, ast.ClassDef):
            items.append({"type": "class", "name": node.name, "line": node.lineno})

    return json.dumps(items, indent=2, ensure_ascii=False)


@tool
def python_imports_report(path: str) -> str:
    """
    List imports used in a Python file.
    """
    import ast

    py_path = _ensure_safe_path(path, must_exist=True)
    tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"))
    imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"type": "import", "module": alias.name, "alias": alias.asname})
        elif isinstance(node, ast.ImportFrom):
            imports.append(
                {
                    "type": "from_import",
                    "module": node.module,
                    "names": [alias.name for alias in node.names],
                }
            )

    return json.dumps(imports, indent=2, ensure_ascii=False)


@tool
def count_lines_of_code(folder: str = ".", extension: str = ".py", recursive: bool = True) -> str:
    """
    Count lines of code, blank lines, and comment lines.
    """
    folder_path = _ensure_safe_path(folder, must_exist=True)

    if not folder_path.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    if not extension.startswith("."):
        extension = "." + extension

    pattern = "**/*" if recursive else "*"
    result = {
        "files": 0,
        "total_lines": 0,
        "blank_lines": 0,
        "comment_lines": 0,
        "code_lines": 0,
    }

    for path in folder_path.glob(pattern):
        if not path.is_file() or path.suffix.lower() != extension.lower():
            continue

        result["files"] += 1
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

        for line in lines:
            stripped = line.strip()
            result["total_lines"] += 1
            if not stripped:
                result["blank_lines"] += 1
            elif stripped.startswith("#"):
                result["comment_lines"] += 1
            else:
                result["code_lines"] += 1

    return json.dumps(result, indent=2)


@tool
def grep_code(folder: str, query: str, extension: str = ".py", max_matches: int = 100) -> str:
    """
    Search source code files for a query.
    """
    if not extension.startswith("."):
        extension = "." + extension

    return search_text_in_files(folder=folder, query=query, file_pattern=f"*{extension}", max_matches=max_matches)


# ---------------------------------------------------------------------
# Date and planning tools
# ---------------------------------------------------------------------


@tool
def current_datetime() -> str:
    """
    Return the current local date and time.
    """
    return datetime.now().isoformat(timespec="seconds")


@tool
def add_days_to_date(date_text: str, days: int) -> str:
    """
    Add days to a date.
    date_text format: YYYY-MM-DD
    """
    from datetime import timedelta

    dt = datetime.strptime(date_text, "%Y-%m-%d")
    return (dt + timedelta(days=int(days))).strftime("%Y-%m-%d")


@tool
def days_between_dates(start_date: str, end_date: str) -> str:
    """
    Calculate days between two dates.
    Date format: YYYY-MM-DD
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    return str((end - start).days)


@tool
def create_schedule_markdown(tasks_json: str, start_date: str, output_path: str = "./output/schedule.md") -> str:
    """
    Create a simple day-by-day markdown schedule.
    tasks_json must be a JSON list of task strings.
    """
    from datetime import timedelta

    tasks = json.loads(tasks_json)
    if not isinstance(tasks, list):
        raise ValueError("tasks_json must be a JSON list.")

    start = datetime.strptime(start_date, "%Y-%m-%d")
    lines = ["# Schedule", ""]

    for index, task in enumerate(tasks):
        day = start + timedelta(days=index)
        lines.append(f"## {day.strftime('%Y-%m-%d')}")
        lines.append(f"- {task}")
        lines.append("")

    out_path = _ensure_safe_path(output_path, must_exist=False)
    _ensure_parent(out_path)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    return f"Schedule created: {out_path.relative_to(PROJECT_ROOT)}"


# ---------------------------------------------------------------------
# Web-free local HTML and lightweight extraction tools
# ---------------------------------------------------------------------


@tool
def html_to_text(path: str) -> str:
    """
    Convert a local HTML file to readable text.
    """
    html_path = _ensure_safe_path(path, must_exist=True)
    html = html_path.read_text(encoding="utf-8", errors="replace")

    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html)

    return html.strip()


@tool
def extract_html_links(path: str) -> str:
    """
    Extract links from a local HTML file.
    """
    html_path = _ensure_safe_path(path, must_exist=True)
    html = html_path.read_text(encoding="utf-8", errors="replace")

    links = []
    for match in re.finditer(r"""<a\s+[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""", html, flags=re.IGNORECASE | re.DOTALL):
        href = match.group(1)
        label = re.sub(r"<[^>]+>", " ", match.group(2))
        label = re.sub(r"\s+", " ", label).strip()
        links.append({"href": href, "label": label})

    return json.dumps(links, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------
# Lightweight validation and grading tools
# ---------------------------------------------------------------------


@tool
def rubric_score(criteria_json: str, evidence_text: str) -> str:
    """
    Score evidence against rubric criteria.
    criteria_json must be a JSON list of criteria strings.
    This is rule-based and checks keyword overlap.
    """
    criteria = json.loads(criteria_json)
    if not isinstance(criteria, list):
        raise ValueError("criteria_json must be a JSON list.")

    evidence_words = set(re.findall(r"\b\w+\b", evidence_text.lower()))
    results = []

    for criterion in criteria:
        criterion_words = set(re.findall(r"\b\w+\b", str(criterion).lower()))
        overlap = criterion_words & evidence_words
        score = 0 if not criterion_words else round(len(overlap) / len(criterion_words), 3)

        results.append(
            {
                "criterion": criterion,
                "score": score,
                "matched_terms": sorted(overlap),
            }
        )

    average = round(sum(item["score"] for item in results) / len(results), 3) if results else 0

    return json.dumps({"average_score": average, "criteria": results}, indent=2, ensure_ascii=False)


@tool
def check_required_sections(markdown_text: str, sections_json: str) -> str:
    """
    Check whether required markdown sections exist.
    sections_json must be a JSON list of section names.
    """
    sections = json.loads(sections_json)
    if not isinstance(sections, list):
        raise ValueError("sections_json must be a JSON list.")

    headings = re.findall(r"^#{1,6}\s+(.+)$", markdown_text, flags=re.MULTILINE)
    normalized = {h.strip().lower() for h in headings}

    result = []
    for section in sections:
        result.append(
            {
                "section": section,
                "present": str(section).strip().lower() in normalized,
            }
        )

    return json.dumps(result, indent=2, ensure_ascii=False)


@tool
def simple_sentiment(text: str) -> str:
    """
    Very simple local sentiment estimate using keyword lists.
    """
    positive = {
        "good", "great", "excellent", "amazing", "useful", "strong", "happy",
        "success", "successful", "positive", "improved", "clear", "helpful",
    }
    negative = {
        "bad", "poor", "failed", "failure", "weak", "sad", "angry", "negative",
        "problem", "issue", "risk", "late", "missing", "unclear", "error",
    }

    words = re.findall(r"\b\w+\b", text.lower())
    pos = sum(1 for word in words if word in positive)
    neg = sum(1 for word in words if word in negative)

    label = "neutral"
    if pos > neg:
        label = "positive"
    elif neg > pos:
        label = "negative"

    return json.dumps(
        {
            "label": label,
            "positive_hits": pos,
            "negative_hits": neg,
            "score": pos - neg,
        },
        indent=2,
    )


# ---------------------------------------------------------------------
# Local automation helper tools
# ---------------------------------------------------------------------


@tool
def create_readme(project_name: str, problem: str, solution: str, output_path: str = "./output/README.md") -> str:
    """
    Create a simple README for a hackathon or automation project.
    """
    content = f"""# {project_name}

## Problem

{problem.strip()}

## Solution

{solution.strip()}

## How it works

1. Put input files inside `input/`.
2. Run the automation.
3. Check generated files inside `output/`.

## Local-first

This project runs locally using AgentKit and Ollama.

## Requirements

- Python
- Ollama
- AgentKit local tools

## Demo

Add screenshots, sample inputs, and output reports here.
"""

    out_path = _ensure_safe_path(output_path, must_exist=False)
    _ensure_parent(out_path)
    out_path.write_text(content, encoding="utf-8")

    return f"README created: {out_path.relative_to(PROJECT_ROOT)}"


@tool
def create_project_scaffold(project_name: str, output_folder: str = "./output/project_scaffold") -> str:
    """
    Create a simple local AgentKit project scaffold.
    """
    folder = _ensure_safe_path(output_folder, must_exist=False)
    folder.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", project_name).strip("_") or "agentkit_project"

    files = {
        "README.md": f"# {project_name}\n\nLocal AgentKit automation project.\n",
        "app.py": f'''from agentkit import Agent
from tools import list_files, read_file, create_markdown_report

agent = Agent(
    name={safe_name!r},
    model="gemma4",
    tools=[list_files, read_file, create_markdown_report],
)

agent.run("""
Read files from ./input and create ./output/report.md.
""")
''',
        "input/.gitkeep": "",
        "output/.gitkeep": "",
    }

    created = []
    for rel, content in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(str(path.relative_to(PROJECT_ROOT)))

    return json.dumps(created, indent=2, ensure_ascii=False)


@tool
def create_agent_app_file(agent_name: str, task: str, tools_json: str, output_path: str = "./output/app.py", model: str = "gemma4") -> str:
    """
    Generate a simple app.py file for AgentKit.
    tools_json must be a JSON list of tool names.
    """
    tools = json.loads(tools_json)
    if not isinstance(tools, list):
        raise ValueError("tools_json must be a JSON list.")

    tool_names = [str(t) for t in tools]
    imports = ", ".join(tool_names) if tool_names else "list_files, read_file, create_markdown_report"
    tool_expr = ", ".join(tool_names) if tool_names else "list_files, read_file, create_markdown_report"

    code = f'''from agentkit import Agent
from tools import {imports}

agent = Agent(
    name={agent_name!r},
    model={model!r},
    tools=[{tool_expr}],
)

agent.run("""
{task.strip()}
""")
'''

    out_path = _ensure_safe_path(output_path, must_exist=False)
    _ensure_parent(out_path)
    out_path.write_text(code, encoding="utf-8")

    return f"Agent app file created: {out_path.relative_to(PROJECT_ROOT)}"


# =====================================================================
# REPLACEMENT TOOL BUNDLES
# Replace your existing "# Tool bundles" section with this.
# =====================================================================


# ---------------------------------------------------------------------
# Tool bundles
# ---------------------------------------------------------------------


CORE_TOOLS = [
    list_files,
    read_file,
    write_file,
    append_file,
    create_folder,
    move_file,
    copy_file,
    rename_file,
    file_info,
    search_text_in_files,
    list_files_by_extension,
    list_recent_files,
    find_duplicate_files,
    get_folder_tree,
    make_file_manifest,
    batch_rename_files,
    zip_folder,
    unzip_file,
    delete_file_safe,
    create_markdown_report,
    ensure_project_folders,
]

DATA_TOOLS = [
    read_csv,
    summarize_csv,
    filter_csv,
    write_csv,
    convert_csv_to_json,
    create_chart_from_csv,
    csv_column_names,
    csv_shape,
    csv_missing_report,
    csv_value_counts,
    csv_groupby_summary,
    sort_csv,
    select_csv_columns,
    merge_csv_files,
    deduplicate_csv,
    sample_csv,
    csv_to_markdown_table,
    read_excel,
    excel_sheet_names,
    excel_to_csv,
    basic_stats,
]

PDF_TOOLS = [
    read_pdf,
    pdf_info,
    split_pdf_pages,
    merge_pdfs,
    extract_pdf_pages_text,
]

TEXT_TOOLS = [
    extract_keywords,
    count_words,
    compare_texts,
    clean_text,
    summarize_text_stats,
    extract_emails,
    extract_urls,
    extract_phone_numbers,
    extract_dates,
    split_text_into_chunks,
    text_to_bullets,
    remove_markdown,
    markdown_to_html,
    create_minutes_from_notes,
    create_brief_from_text,
    simple_sentiment,
    rubric_score,
    check_required_sections,
]

JSON_TOOLS = [
    read_json,
    write_json,
    validate_json_text,
    json_keys,
    json_get_value,
    json_set_value,
    json_to_csv,
]

IMAGE_TOOLS = [
    image_info,
    resize_image,
    convert_image_format,
    create_thumbnail,
]

CODE_TOOLS = [
    list_python_functions,
    python_imports_report,
    count_lines_of_code,
    grep_code,
]

DATE_TOOLS = [
    current_datetime,
    add_days_to_date,
    days_between_dates,
    create_schedule_markdown,
]

HTML_TOOLS = [
    html_to_text,
    extract_html_links,
]

MEMORY_TOOLS = [
    memory_set,
    memory_get,
    memory_list,
]

MATH_TOOLS = [
    calculate,
    basic_stats,
]

PROJECT_TOOLS = [
    create_readme,
    create_project_scaffold,
    create_agent_app_file,
]

ALL_TOOLS = (
    CORE_TOOLS
    + DATA_TOOLS
    + PDF_TOOLS
    + TEXT_TOOLS
    + JSON_TOOLS
    + IMAGE_TOOLS
    + CODE_TOOLS
    + DATE_TOOLS
    + HTML_TOOLS
    + MEMORY_TOOLS
    + MATH_TOOLS
    + PROJECT_TOOLS
)


__all__ = [
    "list_files",
    "read_file",
    "write_file",
    "append_file",
    "create_folder",
    "move_file",
    "copy_file",
    "rename_file",
    "file_info",
    "search_text_in_files",
    "list_files_by_extension",
    "list_recent_files",
    "find_duplicate_files",
    "get_folder_tree",
    "make_file_manifest",
    "batch_rename_files",
    "zip_folder",
    "unzip_file",
    "delete_file_safe",

    "read_csv",
    "summarize_csv",
    "filter_csv",
    "write_csv",
    "convert_csv_to_json",
    "create_chart_from_csv",
    "csv_column_names",
    "csv_shape",
    "csv_missing_report",
    "csv_value_counts",
    "csv_groupby_summary",
    "sort_csv",
    "select_csv_columns",
    "merge_csv_files",
    "deduplicate_csv",
    "sample_csv",
    "csv_to_markdown_table",
    "read_excel",
    "excel_sheet_names",
    "excel_to_csv",

    "read_pdf",
    "pdf_info",
    "split_pdf_pages",
    "merge_pdfs",
    "extract_pdf_pages_text",

    "extract_keywords",
    "count_words",
    "compare_texts",
    "clean_text",
    "summarize_text_stats",
    "extract_emails",
    "extract_urls",
    "extract_phone_numbers",
    "extract_dates",
    "split_text_into_chunks",
    "text_to_bullets",
    "remove_markdown",
    "markdown_to_html",
    "create_minutes_from_notes",
    "create_brief_from_text",
    "simple_sentiment",
    "rubric_score",
    "check_required_sections",

    "create_markdown_report",
    "create_todo_file",
    "create_table_markdown",

    "read_json",
    "write_json",
    "validate_json_text",
    "json_keys",
    "json_get_value",
    "json_set_value",
    "json_to_csv",

    "image_info",
    "resize_image",
    "convert_image_format",
    "create_thumbnail",

    "list_python_functions",
    "python_imports_report",
    "count_lines_of_code",
    "grep_code",

    "current_datetime",
    "add_days_to_date",
    "days_between_dates",
    "create_schedule_markdown",

    "html_to_text",
    "extract_html_links",

    "memory_set",
    "memory_get",
    "memory_list",

    "calculate",
    "basic_stats",

    "ensure_project_folders",
    "create_readme",
    "create_project_scaffold",
    "create_agent_app_file",

    "CORE_TOOLS",
    "DATA_TOOLS",
    "PDF_TOOLS",
    "TEXT_TOOLS",
    "JSON_TOOLS",
    "IMAGE_TOOLS",
    "CODE_TOOLS",
    "DATE_TOOLS",
    "HTML_TOOLS",
    "MEMORY_TOOLS",
    "MATH_TOOLS",
    "PROJECT_TOOLS",
    "ALL_TOOLS",
]
