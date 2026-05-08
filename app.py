from agentkit import Agent
from tools import list_files, read_file, write_file, create_markdown_report

agent = Agent(
    name="My Local Agent",
    model="gemma4",
    tools=[
        list_files,
        read_file,
        write_file,
        create_markdown_report,
    ],
)

agent.run("""
Read files from ./input.
Summarize them.
Create ./output/my_report.md.
""")