# examples.py
"""
Student-ready examples for AgentKit Local.

Before running:
    pip install requests pandas
    pip install pypdf matplotlib openpyxl   # optional but useful

Start Ollama:
    ollama serve
    ollama pull gemma4

Run:
    python examples.py

Project folders:
    input/      Put files here
    output/     Agent writes results here
    data/       Optional datasets
    reports/    Optional reports
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agentkit import Agent, Supervisor, Workflow, doctor
from tools import (
    CORE_TOOLS,
    DATA_TOOLS,
    JSON_TOOLS,
    MEMORY_TOOLS,
    PDF_TOOLS,
    TEXT_TOOLS,
    append_file,
    basic_stats,
    calculate,
    clean_text,
    compare_texts,
    convert_csv_to_json,
    copy_file,
    count_words,
    create_chart_from_csv,
    create_folder,
    create_markdown_report,
    create_table_markdown,
    create_todo_file,
    ensure_project_folders,
    extract_keywords,
    file_info,
    filter_csv,
    list_files,
    memory_get,
    memory_list,
    memory_set,
    move_file,
    pdf_info,
    read_csv,
    read_file,
    read_json,
    read_pdf,
    rename_file,
    search_text_in_files,
    summarize_csv,
    write_csv,
    write_file,
    write_json,
)


DEFAULT_MODEL = "gemma4"


def prepare_project() -> None:
    Path("input").mkdir(exist_ok=True)
    Path("output").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)


def file_organizer(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Organize messy files from input/ into useful folders and create a report.
    """

    prepare_project()

    agent = Agent(
        name="File Organizer",
        model=model,
        goal="Organize local files safely and create a clear organization report.",
        tools=[
            ensure_project_folders,
            list_files,
            file_info,
            create_folder,
            move_file,
            copy_file,
            write_file,
            create_markdown_report,
        ],
        max_steps=18,
    )

    return agent.run(
        """
        Look inside ./input.

        Organize the files into folders inside ./output/organized based on file type and purpose.
        Do not delete anything.
        Copy files instead of moving them if you are unsure.

        Create ./output/file_organization_report.md with:
        1. What files were found
        2. How they were organized
        3. Any files you skipped
        4. Suggestions for improvement
        """
    )


def assignment_feedback_agent(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Read student assignment files and generate feedback.
    """

    prepare_project()

    agent = Agent(
        name="Assignment Feedback Agent",
        model=model,
        goal="Give constructive feedback on student assignments.",
        tools=[
            list_files,
            read_file,
            read_pdf,
            count_words,
            extract_keywords,
            write_file,
            create_markdown_report,
        ],
        max_steps=20,
    )

    return agent.run(
        """
        Read all assignment files from ./input.
        Supported files may include .txt, .md, .py, and .pdf.

        For each assignment, provide:
        1. Short summary
        2. Strengths
        3. Areas for improvement
        4. Suggested score out of 10
        5. One practical improvement task

        Create the final report at ./output/assignment_feedback_report.md.
        """
    )


def csv_insight_agent(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Analyze a CSV dataset and generate a report.
    """

    prepare_project()

    agent = Agent(
        name="CSV Insight Agent",
        model=model,
        goal="Analyze CSV files and produce useful insights for humans.",
        tools=[
            list_files,
            read_csv,
            summarize_csv,
            filter_csv,
            create_chart_from_csv,
            write_file,
            create_markdown_report,
        ],
        max_steps=18,
    )

    return agent.run(
        """
        Look inside ./input for CSV files.

        Pick the most relevant CSV file.
        Analyze the data using available tools.
        Create useful insights, risks, patterns, and recommendations.

        If suitable numeric columns exist, create one chart in ./output/chart.png.

        Create ./output/csv_insight_report.md with:
        1. Dataset overview
        2. Important columns
        3. Key findings
        4. Any data quality issues
        5. Recommended actions
        """
    )


def attendance_risk_detector(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Detect students at attendance risk from a CSV file.
    """

    prepare_project()

    agent = Agent(
        name="Attendance Risk Detector",
        model=model,
        goal="Find students with low attendance and create a teacher-friendly report.",
        tools=[
            list_files,
            read_csv,
            summarize_csv,
            filter_csv,
            write_file,
            create_markdown_report,
        ],
        max_steps=18,
    )

    return agent.run(
        """
        Find an attendance-related CSV file inside ./input.
        Analyze the CSV.

        Identify students below 75% attendance.
        Group them into:
        - Critical risk: below 60%
        - Warning risk: 60% to 74%
        - Safe: 75% and above

        Create ./output/attendance_risk_report.md with:
        1. Class summary
        2. Critical-risk students
        3. Warning-risk students
        4. Suggested teacher actions
        5. Parent communication suggestions
        """
    )


def resume_ranker(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Rank resumes against a job description.
    """

    prepare_project()

    agent = Agent(
        name="Resume Ranker",
        model=model,
        goal="Compare resumes against a job description and rank candidates fairly.",
        tools=[
            list_files,
            read_file,
            read_pdf,
            compare_texts,
            extract_keywords,
            write_file,
            create_markdown_report,
        ],
        max_steps=24,
    )

    return agent.run(
        """
        Inside ./input, find:
        - One job description file
        - Multiple resume files

        Compare each resume with the job description.

        Create ./output/resume_ranking_report.md with:
        1. Job description summary
        2. Candidate ranking table
        3. Strengths of each candidate
        4. Missing skills or gaps
        5. Final recommendation

        Be transparent and avoid unfair assumptions.
        """
    )


def study_planner(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Create a study plan from syllabus or notes.
    """

    prepare_project()

    agent = Agent(
        name="Study Planner",
        model=model,
        goal="Convert syllabus and notes into a practical study plan.",
        tools=[
            list_files,
            read_file,
            read_pdf,
            extract_keywords,
            create_todo_file,
            write_file,
            create_markdown_report,
        ],
        max_steps=18,
    )

    return agent.run(
        """
        Read syllabus, notes, or topic files from ./input.

        Create a practical 7-day study plan.
        Include:
        1. Daily topics
        2. Practice tasks
        3. Revision checkpoints
        4. Important keywords
        5. Final exam preparation checklist

        Save the final plan to ./output/study_plan.md.
        """
    )


def research_folder_summarizer(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Summarize a folder of PDFs/text files into one research brief.
    """

    prepare_project()

    agent = Agent(
        name="Research Folder Summarizer",
        model=model,
        goal="Summarize local documents into a useful research brief.",
        tools=[
            list_files,
            read_file,
            read_pdf,
            pdf_info,
            extract_keywords,
            write_file,
            create_markdown_report,
        ],
        max_steps=26,
    )

    return agent.run(
        """
        Read the documents inside ./input.
        They may be PDFs, markdown files, or text files.

        Create ./output/research_brief.md with:
        1. Executive summary
        2. Key ideas from each document
        3. Common themes
        4. Contradictions or gaps
        5. Useful keywords
        6. Suggested next steps
        """
    )


def meeting_notes_agent(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Turn raw meeting notes into action items and minutes.
    """

    prepare_project()

    agent = Agent(
        name="Meeting Notes Agent",
        model=model,
        goal="Convert raw meeting notes into clean minutes and action items.",
        tools=[
            list_files,
            read_file,
            clean_text,
            extract_keywords,
            create_todo_file,
            write_file,
            create_markdown_report,
        ],
        max_steps=16,
    )

    return agent.run(
        """
        Read meeting notes from ./input.

        Create ./output/meeting_minutes.md with:
        1. Meeting summary
        2. Decisions made
        3. Action items with owners if available
        4. Risks or blockers
        5. Follow-up agenda

        Also create ./output/action_items.md as a checklist.
        """
    )


def expense_analyzer(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Analyze expenses from a CSV and produce a budget report.
    """

    prepare_project()

    agent = Agent(
        name="Expense Analyzer",
        model=model,
        goal="Analyze personal or team expenses and create a useful budget report.",
        tools=[
            list_files,
            read_csv,
            summarize_csv,
            filter_csv,
            create_chart_from_csv,
            calculate,
            write_file,
            create_markdown_report,
        ],
        max_steps=20,
    )

    return agent.run(
        """
        Find an expense CSV file inside ./input.

        Analyze spending patterns.
        Identify:
        1. Highest expense categories
        2. Unusual expenses
        3. Possible savings
        4. Monthly or weekly trends if date columns exist

        If possible, create a chart at ./output/expense_chart.png.

        Create ./output/expense_analysis_report.md.
        """
    )


def local_knowledge_base_agent(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Search local files and answer a project question with citations to filenames.
    """

    prepare_project()

    agent = Agent(
        name="Local Knowledge Base Agent",
        model=model,
        goal="Search local documents and produce grounded answers from files.",
        tools=[
            list_files,
            search_text_in_files,
            read_file,
            read_pdf,
            extract_keywords,
            write_file,
            create_markdown_report,
        ],
        max_steps=22,
    )

    return agent.run(
        """
        Search files inside ./input and create a local knowledge base report.

        Find the main topics, important facts, repeated keywords, and unanswered questions.
        Use filenames in the report so the user knows where information came from.

        Create ./output/local_knowledge_base_report.md.
        """
    )


def json_data_cleaner(model: str = DEFAULT_MODEL):
    """
    Project idea:
    Clean and transform JSON files.
    """

    prepare_project()

    agent = Agent(
        name="JSON Data Cleaner",
        model=model,
        goal="Read, understand, and clean local JSON data files.",
        tools=[
            list_files,
            read_json,
            write_json,
            create_markdown_report,
        ],
        max_steps=16,
    )

    return agent.run(
        """
        Find JSON files inside ./input.
        Inspect their structure.

        Create a cleaned version of the most relevant JSON file at ./output/cleaned_data.json.
        Also create ./output/json_cleaning_report.md explaining:
        1. Original structure
        2. Issues found
        3. Cleaning choices
        4. Final structure
        """
    )


def report_writer_workflow(model: str = DEFAULT_MODEL):
    """
    Workflow example:
    Reader agent -> Analyst agent -> Writer agent.
    """

    prepare_project()

    reader = Agent(
        name="Reader",
        model=model,
        goal="Read local files and extract important information.",
        tools=[
            list_files,
            read_file,
            read_pdf,
            read_csv,
            summarize_csv,
        ],
        max_steps=12,
    )

    analyst = Agent(
        name="Analyst",
        model=model,
        goal="Analyze extracted information and identify useful patterns.",
        tools=[
            extract_keywords,
            compare_texts,
            calculate,
            basic_stats,
            write_file,
        ],
        max_steps=10,
    )

    writer = Agent(
        name="Report Writer",
        model=model,
        goal="Write polished markdown reports.",
        tools=[
            write_file,
            create_markdown_report,
            create_table_markdown,
        ],
        max_steps=10,
    )

    workflow = Workflow("Local Report Writer Workflow")

    workflow.add_step(
        reader,
        """
        Inspect ./input.
        Read the important files.
        Produce a concise extraction summary.
        """,
    )

    workflow.add_step(
        analyst,
        """
        Analyze the extracted information.
        Identify key insights, risks, and recommendations.
        """,
    )

    workflow.add_step(
        writer,
        """
        Create a polished final report at ./output/workflow_report.md.
        Include summary, insights, risks, and recommendations.
        """,
    )

    return workflow.run()


def supervisor_demo(model: str = DEFAULT_MODEL):
    """
    Supervisor-worker example:
    A supervisor delegates to specialist agents.
    """

    prepare_project()

    file_worker = Agent(
        name="File Worker",
        model=model,
        goal="Inspect and read local files.",
        tools=[
            list_files,
            read_file,
            read_pdf,
            read_csv,
            summarize_csv,
        ],
        max_steps=10,
    )

    data_worker = Agent(
        name="Data Worker",
        model=model,
        goal="Analyze CSV and numeric data.",
        tools=[
            read_csv,
            summarize_csv,
            filter_csv,
            create_chart_from_csv,
            basic_stats,
            calculate,
        ],
        max_steps=12,
    )

    writing_worker = Agent(
        name="Writing Worker",
        model=model,
        goal="Create useful final reports and markdown files.",
        tools=[
            write_file,
            create_markdown_report,
            create_table_markdown,
            create_todo_file,
        ],
        max_steps=10,
    )

    supervisor = Supervisor(
        name="Hackathon Project Manager",
        model=model,
        workers=[
            file_worker,
            data_worker,
            writing_worker,
        ],
        max_rounds=8,
    )

    return supervisor.run(
        """
        Inspect the files in ./input.
        Understand what kind of project can be built from them.
        Analyze any data if available.
        Create a useful final report in ./output/supervisor_report.md.
        """
    )


EXAMPLES = {
    "file-organizer": file_organizer,
    "assignment-feedback": assignment_feedback_agent,
    "csv-insight": csv_insight_agent,
    "attendance-risk": attendance_risk_detector,
    "resume-ranker": resume_ranker,
    "study-planner": study_planner,
    "research-summary": research_folder_summarizer,
    "meeting-notes": meeting_notes_agent,
    "expense-analyzer": expense_analyzer,
    "knowledge-base": local_knowledge_base_agent,
    "json-cleaner": json_data_cleaner,
    "workflow-report": report_writer_workflow,
    "supervisor-demo": supervisor_demo,
}


def print_examples() -> None:
    print("\nAvailable examples:\n")
    for name in EXAMPLES:
        print(f"  python examples.py {name}")
    print("\nUtility commands:")
    print("  python examples.py doctor")
    print("  python examples.py list")


def main():
    parser = argparse.ArgumentParser(
        description="Run local AgentKit student examples."
    )

    parser.add_argument(
        "example",
        nargs="?",
        default="list",
        help="Example name to run. Use 'list' to see all examples.",
    )

    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Ollama model name. Example: gemma4, mistral, qwen2.5",
    )

    args = parser.parse_args()

    if args.example == "list":
        print_examples()
        return

    if args.example == "doctor":
        doctor(model=args.model)
        return

    if args.example not in EXAMPLES:
        print(f"Unknown example: {args.example}")
        print_examples()
        raise SystemExit(1)

    prepare_project()

    print(f"\nRunning example: {args.example}")
    print(f"Using model: {args.model}")
    print("Input folder: ./input")
    print("Output folder: ./output\n")

    result = EXAMPLES[args.example](model=args.model)

    print("\nFinished.\n")

    if isinstance(result, list):
        for index, item in enumerate(result, start=1):
            print(f"Step {index}: {item.answer}\n")
    else:
        print(result.answer)


if __name__ == "__main__":
    main()