from agentkit import Agent
from tools import list_files, read_file

agent = Agent(
    name='App Story Converter',
    model='gpt-4',
    tools=[list_files, read_file],
)

agent.run("""
Reads information about an application and converts it into a use case story for better understanding.
""")
