# AI Dependency Usage Analyzer

AI-powered Dependency Usage Analyzer for Python projects using AST parsing, FastAPI, and Ollama.

The tool recursively scans Python projects, detects imports, tracks dependency usage locations, identifies unused imports, classifies dependencies, and generates intelligent dependency reports.

---

# Features

## Core Features

* Recursive Python project scanning
* AST-based dependency analysis
* Detects:

  * imports
  * aliases
  * from-imports
  * dependency usages
  * unused imports
* Dependency classification:

  * Standard Library
  * Third-Party Libraries
  * Local Modules
* FastAPI backend
* AI-generated dependency insights
* Markdown report generation
* Ollama local AI integration

---

# Tech Stack

## Backend

* Python
* FastAPI
* AST (Abstract Syntax Tree)
* Uvicorn

## AI Integration

* Ollama
* Qwen2.5-Coder / Llama3

## Frontend

* HTML
* CSS
* JavaScript

---

# Project Structure

```text
AI-AGENT/
│
├── analyzer/
│   ├── scanner.py
│   ├── parser.py
│   ├── usage_tracker.py
│   ├── classifier.py
│   └── engine.py
│
├── input/
│   └── sample.py
│
├── output/
│   └── generated_reports
│
├── static/
│   ├── index.html
│   ├── app.js
│   └── styles.css
│
├── api.py
├── requirements.txt
└── README.md
```

---

# Installation Guide

## Step 1 — Clone Repository

```bash
git clone https://github.com/YOUR_USERNAME/ai-dependency-usage-analyzer.git
```

---

## Step 2 — Open Project

```bash
cd ai-dependency-usage-analyzer
```

---

## Step 3 — Create Virtual Environment

### Windows

```bash
python -m venv venv
```

Activate:

```bash
.\venv\Scripts\activate
```

### Linux / Mac

```bash
python3 -m venv venv
source venv/bin/activate
```

---

## Step 4 — Install Dependencies

```bash
pip install -r requirements.txt
```

If requirements.txt is missing packages:

```bash
pip install fastapi uvicorn ollama
```

---

# Ollama Setup

## Step 1 — Install Ollama

Download Ollama:

[https://ollama.com/download](https://ollama.com/download)

---

## Step 2 — Pull AI Model

Recommended model:

```bash
ollama pull qwen2.5-coder:7b
```

Alternative:

```bash
ollama pull llama3:8b
```

---

## Step 3 — Verify Installation

```bash
ollama list
```

You should see:

```text
qwen2.5-coder:7b
```

---

# Running the Project

## Step 1 — Start FastAPI Server

```bash
python -m uvicorn api:app --reload
```

---

## Step 2 — Open Swagger API Docs

Open browser:

```text
http://127.0.0.1:8000/docs
```

---

# How to Use the Tool

## Method 1 — Analyze Any Python Project

### API Endpoint

```text
POST /analyze-project
```

---

## Example Request

```json
{
  "project_path": "C:/Users/YourName/Desktop/MyProject"
}
```

OR:

```json
{
  "project_path": "./"
}
```

---

# Example Output

```json
[
  {
    "file": "app.py",
    "imports": [
      {
        "module": "pandas",
        "alias": "pd",
        "type": "third_party",
        "unused": false,
        "usages": [
          {
            "name": "pd",
            "attribute": "read_csv",
            "line": 22
          }
        ]
      }
    ]
  }
]
```

---

# Dependency Types

The analyzer classifies dependencies into:

| Type             | Description                 |
| ---------------- | --------------------------- |
| standard_library | Built-in Python libraries   |
| third_party      | Installed external packages |
| local_module     | Project-specific modules    |

---

# Features Explained

## 1. Recursive Project Scanning

Automatically scans all `.py` files inside a project.

Ignored folders:

* venv
* .venv
* **pycache**
* node_modules
* .git

---

## 2. AST-Based Parsing

Uses Python AST parsing instead of regex for accurate import detection.

Detects:

```python
import pandas as pd
from flask import Flask
```

---

## 3. Usage Tracking

Tracks where dependencies are actually used.

Example:

```python
pd.read_csv()
```

---

## 4. Unused Import Detection

Identifies imports that are never used.

Example:

```python
import numpy as np
```

If `np` is never referenced:

```json
{
  "unused": true
}
```

---

# Generated Reports

Reports are generated inside:

```text
./output
```

Possible reports:

* Dependency Summary
* File-wise Dependency Mapping
* AI Dependency Insights
* Project Statistics
* Execution Logs

---

# Sample Test File

Inside:

```text
input/sample.py
```

Example:

```python
import pandas as pd
import numpy as np

from flask import Flask

app = Flask(__name__)

pd.read_csv("data.csv")
```

---

# API Endpoints

| Method | Endpoint         | Description                  |
| ------ | ---------------- | ---------------------------- |
| GET    | /docs            | Swagger UI                   |
| POST   | /analyze-project | Analyze project dependencies |
| GET    | /test-analysis   | Test AST engine              |

---

# Future Improvements

Planned upgrades:

* Dependency graph visualization
* Circular dependency detection
* Security risk analysis
* requirements.txt validation
* AI architecture explanation
* Export PDF/HTML reports
* VS Code extension
* Multi-language support

---

# Troubleshooting

## Uvicorn Not Recognized

Run:

```bash
python -m pip install uvicorn fastapi
```

Then:

```bash
python -m uvicorn api:app --reload
```

---

## Ollama Port Error

If you see:

```text
listen tcp 127.0.0.1:11434
```

Ollama is already running.

Do NOT run `ollama serve` again.

---

## Model Not Found

Install model:

```bash
ollama pull qwen2.5-coder:7b
```

---

## Route Not Found

Restart server:

```bash
python -m uvicorn api:app --reload
```

---

# Use Cases

* Dependency intelligence
* Codebase understanding
* Software architecture analysis
* AI-assisted debugging
* Unused import detection
* Developer productivity
* Educational learning tool
* Open-source project maintenance

---

# License

MIT License

---

# Author

Developed by Yashwin Devarakonda

AI-powered software engineering and developer tooling project.
