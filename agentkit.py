# agentkit.py
"""
AgentKit Local
A tiny, local-first agentic AI framework for Ollama-powered automations.

Requirements:
    pip install requests

Optional:
    pip install rich

Ollama:
    ollama serve
    ollama pull gemma4
"""

from __future__ import annotations

import inspect
import json
import re
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import requests


try:
    from rich.console import Console

    _console = Console()
    _RICH = True
except Exception:
    _console = None
    _RICH = False


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------


class Logger:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _print(self, label: str, message: str):
        if not self.enabled:
            return

        text = f"[{label}] {message}"

        if _RICH:
            colors = {
                "AGENT": "cyan",
                "PLAN": "blue",
                "ACTION": "yellow",
                "OBSERVATION": "green",
                "ERROR": "red",
                "RETRY": "bold yellow",
                "DONE": "bold green",
                "WORKFLOW": "magenta",
                "MODEL": "cyan",
            }
            _console.print(f"[{colors.get(label, 'white')}]{text}[/]")
        else:
            print(text)

    def agent(self, message: str):
        self._print("AGENT", message)

    def model(self, message: str):
        self._print("MODEL", message)

    def plan(self, message: str):
        self._print("PLAN", message)

    def action(self, message: str):
        self._print("ACTION", message)

    def observation(self, message: str):
        self._print("OBSERVATION", message)

    def error(self, message: str):
        self._print("ERROR", message)

    def retry(self, message: str):
        self._print("RETRY", message)

    def done(self, message: str):
        self._print("DONE", message)

    def workflow(self, message: str):
        self._print("WORKFLOW", message)


# ---------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------


def truncate(text: Any, max_chars: int = 4000) -> str:
    text = str(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [truncated {len(text) - max_chars} chars]"


def safe_json_dumps(data: Any, *, max_chars: int = 12000) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False)
    except Exception:
        text = json.dumps(str(data), ensure_ascii=False)

    return truncate(text, max_chars=max_chars)


# ---------------------------------------------------------------------
# Tool system
# ---------------------------------------------------------------------


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    parameters: Dict[str, Any] = field(default_factory=dict)

    def run(self, **kwargs) -> Any:
        return self.fn(**kwargs)

    def schema_for_prompt(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def _python_type_to_json_type(annotation: Any) -> str:
    if annotation is inspect._empty:
        return "string"

    if isinstance(annotation, str):
        annotation = annotation.lower().strip()

        if annotation in ["str", "string"]:
            return "string"
        if annotation in ["int", "integer"]:
            return "integer"
        if annotation in ["float", "number"]:
            return "number"
        if annotation in ["bool", "boolean"]:
            return "boolean"
        if annotation.startswith("list") or annotation.startswith("sequence"):
            return "array"
        if annotation.startswith("dict"):
            return "object"

        return "string"

    if annotation is str:
        return "string"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"

    origin = getattr(annotation, "__origin__", None)

    if annotation in [list, List, Sequence] or origin in [list, List, Sequence]:
        return "array"
    if annotation in [dict, Dict] or origin in [dict, Dict]:
        return "object"

    return "string"


def tool(
    fn: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
):
    """
    Decorator to convert a normal Python function into an AgentKit tool.

    Example:
        @tool
        def read_file(path: str) -> str:
            ...
    """

    def decorator(func: Callable):
        sig = inspect.signature(func)
        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            json_type = _python_type_to_json_type(param.annotation)

            properties[param_name] = {
                "type": json_type,
                "description": f"{param_name} parameter",
            }

            if param.default is inspect._empty:
                required.append(param_name)

        parameters = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        return Tool(
            name=name or func.__name__,
            description=description
            or (inspect.getdoc(func) or f"Tool named {func.__name__}"),
            fn=func,
            parameters=parameters,
        )

    if fn is None:
        return decorator

    return decorator(fn)


# ---------------------------------------------------------------------
# Ollama client with real retry behavior
# ---------------------------------------------------------------------


class OllamaClient:
    def __init__(
        self,
        model: str = "gemma4",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.2,
        timeout: int = 300,
        request_retries: int = 2,
        retry_sleep: float = 2.0,
        json_mode: bool = True,
        num_ctx: int = 4096,
        num_predict: int = 1600,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.request_retries = request_retries
        self.retry_sleep = retry_sleep
        self.json_mode = json_mode
        self.num_ctx = num_ctx
        self.num_predict = num_predict

    def chat(self, messages: List[Dict[str, str]], system: Optional[str] = None) -> str:
        if system:
            final_messages = [{"role": "system", "content": system}] + messages
        else:
            final_messages = messages

        payload = {
            "model": self.model,
            "messages": final_messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx,
            },
        }

        if self.json_mode:
            payload["format"] = "json"

        last_error: Optional[BaseException] = None

        for attempt in range(1, self.request_retries + 2):
            try:
                response = requests.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code != 200:
                    raise RuntimeError(
                        f"Ollama error {response.status_code}: {response.text}"
                    )

                data = response.json()
                return data.get("message", {}).get("content", "")

            except requests.exceptions.ConnectionError as exc:
                raise RuntimeError(
                    "Could not connect to Ollama. Make sure Ollama is running with: ollama serve"
                ) from exc

            except (
                requests.exceptions.ReadTimeout,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError,
                RuntimeError,
            ) as exc:
                last_error = exc

                if attempt <= self.request_retries:
                    time.sleep(self.retry_sleep * attempt)
                    continue

        raise RuntimeError(
            f"Ollama request failed after {self.request_retries + 1} attempt(s): {last_error}"
        )

    def healthcheck(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except Exception:
            return False


# ---------------------------------------------------------------------
# JSON parsing and repair
# ---------------------------------------------------------------------


def extract_json(text: str) -> Dict[str, Any]:
    """
    Extracts a JSON object from model output.
    Handles raw JSON, fenced JSON, and text containing JSON.
    """

    if text is None:
        raise ValueError("Model returned empty response.")

    text = text.strip()

    if not text:
        raise ValueError("Model returned empty response.")

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
        raise ValueError("JSON response must be an object.")
    except Exception:
        pass

    obj = _extract_first_json_object(text)
    if obj:
        return obj

    raise ValueError(f"Could not parse JSON from model output:\n{truncate(text, 3000)}")


def _extract_first_json_object(text: str) -> Optional[Dict[str, Any]]:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(text)):
        char = text[index]

        if escape:
            escape = False
            continue

        if char == "\\":
            escape = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1

        if char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    value = json.loads(candidate)
                    if isinstance(value, dict):
                        return value
                except Exception:
                    return None

    return None


# ---------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------


DEFAULT_SYSTEM_PROMPT = """
You are an autonomous local AI agent.

You are not a chatbot.
You solve tasks by planning, using tools, observing results, and producing useful output files.

You must respond ONLY as valid JSON.
Do not use markdown outside JSON.
Do not wrap JSON in triple backticks.
Do not output explanations outside JSON.

Available response formats:

1. To use a tool:
{
  "type": "tool",
  "thought": "Short reason for using this tool",
  "tool": "tool_name",
  "args": {
    "arg1": "value"
  }
}

2. To finish:
{
  "type": "final",
  "thought": "Why the task is complete",
  "answer": "Final result summary"
}

Rules:
- Use tools whenever action is needed.
- If a tool fails, do not stop. Read the error, fix the arguments, and retry or use another tool.
- Do not invent file contents.
- Do not claim that files were created unless a writing tool succeeded.
- Prefer creating useful output files or reports.
- Keep JSON valid.
- Keep tool arguments concise.
- Do not write huge reports directly if it breaks JSON. Instead use a concise report.
- Stop only after the useful output file is actually created.
"""


JSON_REPAIR_PROMPT = """
Your previous response was not valid JSON or was incomplete.

Return ONLY one valid JSON object using exactly one of these formats:

Tool call:
{
  "type": "tool",
  "thought": "Short reason",
  "tool": "tool_name",
  "args": {}
}

Final:
{
  "type": "final",
  "thought": "Short reason",
  "answer": "Short final summary"
}

Do not include markdown outside JSON.
Do not include triple backticks.
"""


# ---------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------


@dataclass
class AgentResult:
    answer: str
    steps: int
    history: List[Dict[str, Any]]
    success: bool = True
    error: Optional[str] = None


class Agent:
    WRITE_TOOLS = {
        "write_file",
        "append_file",
        "create_markdown_report",
        "create_todo_file",
        "create_table_markdown",
        "write_csv",
        "write_json",
        "create_chart_from_csv",
        "convert_csv_to_json",
        "filter_csv",
        "copy_file",
        "move_file",
        "rename_file",
        "create_folder",
        "ensure_project_folders",
    }

    def __init__(
        self,
        name: str,
        model: str = "gemma4",
        goal: str = "",
        instructions: str = "",
        tools: Optional[Sequence[Tool]] = None,
        base_url: str = "http://localhost:11434",
        temperature: float = 0.2,
        max_steps: int = 12,
        verbose: bool = True,
        safe_mode: bool = True,
        timeout: int = 300,
        model_retries: int = 2,
        parse_retries: int = 2,
        tool_retries: int = 2,
        json_mode: bool = True,
    ):
        self.name = name
        self.goal = goal
        self.instructions = instructions
        self.max_steps = max_steps
        self.safe_mode = safe_mode
        self.model_retries = model_retries
        self.parse_retries = parse_retries
        self.tool_retries = tool_retries

        self.llm = OllamaClient(
            model=model,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
            request_retries=model_retries,
            json_mode=json_mode,
        )

        self.logger = Logger(enabled=verbose)

        self.tools: Dict[str, Tool] = {}
        for t in tools or []:
            self.add_tool(t)

        self.history: List[Dict[str, Any]] = []
        self.successful_write_count = 0

    def add_tool(self, t: Tool):
        if not isinstance(t, Tool):
            raise TypeError(
                f"Expected Tool instance. Did you forget to decorate function with @tool? Got: {t}"
            )
        self.tools[t.name] = t

    def _tool_prompt(self) -> str:
        if not self.tools:
            return "No tools available."

        return json.dumps(
            [t.schema_for_prompt() for t in self.tools.values()],
            indent=2,
            ensure_ascii=False,
        )

    def _build_system_prompt(self) -> str:
        return f"""
{DEFAULT_SYSTEM_PROMPT}

Agent name:
{self.name}

Agent goal:
{self.goal or "Complete the user's task successfully."}

Extra instructions:
{self.instructions or "None"}

Safe mode:
{self.safe_mode}

Available tools:
{self._tool_prompt()}
""".strip()

    def _coerce_tool_args(self, tool_obj: Tool, args: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(args, dict):
            return {}

        schema = tool_obj.parameters or {}
        properties = schema.get("properties", {})
        coerced = {}

        for key, value in args.items():
            expected = properties.get(key, {}).get("type")

            if value is None:
                coerced[key] = value
                continue

            if expected == "integer":
                if isinstance(value, bool):
                    coerced[key] = int(value)
                    continue
                if isinstance(value, (int, float)):
                    coerced[key] = int(value)
                    continue
                if isinstance(value, str):
                    try:
                        coerced[key] = int(float(value.strip()))
                        continue
                    except Exception:
                        pass

            if expected == "number":
                if isinstance(value, (int, float)):
                    coerced[key] = float(value)
                    continue
                if isinstance(value, str):
                    try:
                        coerced[key] = float(value.strip())
                        continue
                    except Exception:
                        pass

            if expected == "boolean":
                if isinstance(value, bool):
                    coerced[key] = value
                    continue
                if isinstance(value, str):
                    lowered = value.strip().lower()
                    if lowered in ["true", "yes", "1", "y"]:
                        coerced[key] = True
                        continue
                    if lowered in ["false", "no", "0", "n"]:
                        coerced[key] = False
                        continue

            if expected == "string" and not isinstance(value, str):
                coerced[key] = json.dumps(value, ensure_ascii=False)
                continue

            coerced[key] = value

        return coerced

    def _validate_tool_args(self, tool_obj: Tool, args: Dict[str, Any]) -> Tuple[bool, str]:
        schema = tool_obj.parameters or {}
        required = schema.get("required", [])

        missing = [key for key in required if key not in args]
        if missing:
            return False, f"Missing required argument(s): {missing}"

        return True, "OK"

    def _call_tool_once(self, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        if tool_name not in self.tools:
            return (
                False,
                f"Unknown tool: {tool_name}. Available tools: {list(self.tools.keys())}",
                args,
            )

        tool_obj = self.tools[tool_name]
        coerced_args = self._coerce_tool_args(tool_obj, args)

        valid, validation_message = self._validate_tool_args(tool_obj, coerced_args)
        if not valid:
            return False, validation_message, coerced_args

        try:
            result = tool_obj.run(**coerced_args)
            return True, truncate(result), coerced_args
        except Exception as exc:
            return (
                False,
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}",
                coerced_args,
            )

    def _call_tool_with_recovery(
        self,
        tool_name: str,
        args: Dict[str, Any],
        messages: List[Dict[str, str]],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        ok, observation, final_args = self._call_tool_once(tool_name, args)

        if ok:
            return True, observation, final_args

        for retry_index in range(1, self.tool_retries + 1):
            self.logger.retry(
                f"Tool failed. Asking model to repair tool call. Attempt {retry_index}/{self.tool_retries}"
            )

            repair_messages = messages + [
                {
                    "role": "assistant",
                    "content": safe_json_dumps(
                        {
                            "type": "tool",
                            "tool": tool_name,
                            "args": final_args,
                        }
                    ),
                },
                {
                    "role": "user",
                    "content": safe_json_dumps(
                        {
                            "tool_error": {
                                "tool": tool_name,
                                "args": final_args,
                                "error": observation,
                            },
                            "instruction": (
                                "The tool call failed. Return ONLY valid JSON. "
                                "Either retry the same tool with corrected args, use another tool, "
                                "or finish only if no tool action is needed."
                            ),
                            "available_tools": list(self.tools.keys()),
                        }
                    ),
                },
            ]

            try:
                raw = self.llm.chat(
                    messages=repair_messages,
                    system=self._build_system_prompt(),
                )
                repaired = extract_json(raw)
            except Exception as exc:
                observation = f"{observation}\nRepair attempt failed: {type(exc).__name__}: {exc}"
                continue

            if repaired.get("type") != "tool":
                observation = (
                    f"{observation}\nRepair response did not provide a tool call: {repaired}"
                )
                continue

            repaired_tool = repaired.get("tool")
            repaired_args = repaired.get("args", {})

            if not isinstance(repaired_args, dict):
                repaired_args = {}

            self.logger.action(
                f"{repaired_tool}({json.dumps(repaired_args, ensure_ascii=False)})"
            )

            ok, observation, final_args = self._call_tool_once(
                repaired_tool,
                repaired_args,
            )

            tool_name = repaired_tool

            if ok:
                return True, observation, final_args

        return False, observation, final_args

    def _model_decision(
        self,
        messages: List[Dict[str, str]],
        system: str,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        raw = ""

        for attempt in range(1, self.parse_retries + 2):
            try:
                raw = self.llm.chat(messages=messages, system=system)
                decision = extract_json(raw)

                valid, error = self._validate_decision(decision)
                if valid:
                    return decision, None

                messages.append({"role": "assistant", "content": truncate(raw)})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Invalid JSON decision: {error}. "
                            "Return ONLY valid JSON using type='tool' or type='final'."
                        ),
                    }
                )

            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                self.logger.error(error_text)

                if attempt <= self.parse_retries:
                    self.logger.retry(
                        f"Retrying model/JSON step {attempt}/{self.parse_retries}"
                    )
                    messages.append({"role": "assistant", "content": truncate(raw)})
                    messages.append(
                        {
                            "role": "user",
                            "content": JSON_REPAIR_PROMPT,
                        }
                    )
                    continue

                return None, error_text

        return None, "Unknown model decision failure."

    def _validate_decision(self, decision: Dict[str, Any]) -> Tuple[bool, str]:
        if not isinstance(decision, dict):
            return False, "Decision must be a JSON object."

        decision_type = decision.get("type")

        if decision_type not in ["tool", "final"]:
            return False, "Decision type must be either 'tool' or 'final'."

        if decision_type == "tool":
            if not decision.get("tool"):
                return False, "Tool decision must include 'tool'."
            if "args" in decision and not isinstance(decision["args"], dict):
                return False, "Tool decision 'args' must be an object."

        if decision_type == "final":
            if "answer" not in decision:
                return False, "Final decision must include 'answer'."

        return True, "OK"

    def _claimed_file_creation(self, text: str) -> bool:
        text = text.lower()
        return any(
            phrase in text
            for phrase in [
                "saved to",
                "created",
                "written to",
                "generated at",
                "./output",
                "output/",
                "output\\",
                ".md",
                ".csv",
                ".json",
                ".png",
            ]
        )

    def run(self, task: str) -> AgentResult:
        self.logger.agent(f"{self.name} started")
        self.logger.plan(task)

        messages: List[Dict[str, str]] = [
            {
                "role": "user",
                "content": f"Task:\n{task}",
            }
        ]

        self.history = []
        self.successful_write_count = 0
        system_prompt = self._build_system_prompt()

        for step in range(1, self.max_steps + 1):
            self.logger.model(f"Thinking step {step}/{self.max_steps}")

            decision, error = self._model_decision(messages, system_prompt)

            if decision is None:
                messages.append(
                    {
                        "role": "user",
                        "content": safe_json_dumps(
                            {
                                "error": error,
                                "instruction": (
                                    "Recover from this error. Return ONLY valid JSON. "
                                    "Use a tool if work is still needed."
                                ),
                            }
                        ),
                    }
                )
                continue

            thought = decision.get("thought", "")
            if thought:
                self.logger.plan(thought)

            decision_type = decision.get("type")

            if decision_type == "final":
                answer = decision.get("answer", "")

                if self._claimed_file_creation(answer) and self.successful_write_count == 0:
                    self.logger.retry(
                        "Model claimed output was created, but no writing tool succeeded yet."
                    )

                    self.history.append(
                        {
                            "step": step,
                            "type": "final_rejected",
                            "decision": decision,
                            "reason": "claimed file creation without successful write tool",
                        }
                    )

                    messages.append(
                        {
                            "role": "assistant",
                            "content": safe_json_dumps(decision),
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "You claimed that an output file was created, but no writing tool "
                                "has successfully run yet. You must now call the correct writing tool "
                                "to actually create the output file."
                            ),
                        }
                    )
                    continue

                self.history.append(
                    {
                        "step": step,
                        "type": "final",
                        "decision": decision,
                    }
                )

                self.logger.done(answer)
                return AgentResult(
                    answer=answer,
                    steps=step,
                    history=self.history,
                    success=True,
                )

            tool_name = decision.get("tool")
            args = decision.get("args", {})

            if not isinstance(args, dict):
                args = {}

            self.logger.action(
                f"{tool_name}({json.dumps(args, ensure_ascii=False)})"
            )

            ok, observation, final_args = self._call_tool_with_recovery(
                tool_name=tool_name,
                args=args,
                messages=messages,
            )

            if ok:
                self.logger.observation(observation)
                if tool_name in self.WRITE_TOOLS:
                    self.successful_write_count += 1
            else:
                self.logger.error(observation)

            event = {
                "step": step,
                "type": "tool",
                "tool": tool_name,
                "args": final_args,
                "success": ok,
                "observation": observation,
            }

            self.history.append(event)

            messages.append(
                {
                    "role": "assistant",
                    "content": safe_json_dumps(decision),
                }
            )

            messages.append(
                {
                    "role": "user",
                    "content": safe_json_dumps(
                        {
                            "tool_result": {
                                "tool": tool_name,
                                "success": ok,
                                "observation": observation,
                            },
                            "instruction": (
                                "If success is false, fix the issue and retry with corrected arguments "
                                "or use another suitable tool. If success is true, continue the task."
                            ),
                        }
                    ),
                }
            )

        answer = f"Stopped after reaching max_steps={self.max_steps}. Task may be incomplete."
        self.logger.error(answer)

        return AgentResult(
            answer=answer,
            steps=self.max_steps,
            history=self.history,
            success=False,
            error=answer,
        )


# ---------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------


@dataclass
class WorkflowStep:
    agent: Agent
    task: str


class Workflow:
    def __init__(self, name: str = "Workflow", verbose: bool = True):
        self.name = name
        self.steps: List[WorkflowStep] = []
        self.logger = Logger(enabled=verbose)

    def add_step(self, agent: Agent, task: str) -> "Workflow":
        self.steps.append(WorkflowStep(agent=agent, task=task))
        return self

    def run(self, initial_context: str = "") -> List[AgentResult]:
        self.logger.workflow(f"{self.name} started with {len(self.steps)} step(s)")

        results: List[AgentResult] = []
        context = initial_context.strip()

        for index, step in enumerate(self.steps, start=1):
            self.logger.workflow(f"Step {index}: {step.agent.name}")

            task = step.task
            if context:
                task = f"""
Previous context:
{context}

Current task:
{step.task}
""".strip()

            result = step.agent.run(task)
            results.append(result)

            context += f"\n\nStep {index} result from {step.agent.name}:\n{result.answer}"

        self.logger.done(f"{self.name} completed")
        return results


# ---------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------


class Supervisor:
    def __init__(
        self,
        name: str,
        workers: Sequence[Agent],
        model: str = "gemma4",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.2,
        verbose: bool = True,
        max_rounds: int = 6,
        timeout: int = 300,
    ):
        self.name = name
        self.workers = {worker.name: worker for worker in workers}
        self.llm = OllamaClient(
            model=model,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
        )
        self.logger = Logger(enabled=verbose)
        self.max_rounds = max_rounds

    def _system_prompt(self) -> str:
        workers = list(self.workers.keys())

        return f"""
You are a supervisor agent named {self.name}.

You manage worker agents.

You must respond ONLY in valid JSON.

Available workers:
{json.dumps(workers, indent=2)}

Response formats:

1. Delegate work:
{{
  "type": "delegate",
  "thought": "Why this worker should do the task",
  "worker": "worker_name",
  "task": "Specific task for the worker"
}}

2. Finish:
{{
  "type": "final",
  "thought": "Why the whole task is complete",
  "answer": "Final summarized result"
}}

Rules:
- Delegate specific tasks to the best worker.
- Use previous worker results to decide next step.
- Finish only when the user's task is complete.
""".strip()

    def run(self, task: str) -> AgentResult:
        self.logger.workflow(f"Supervisor {self.name} started")
        self.logger.plan(task)

        messages = [{"role": "user", "content": f"Main task:\n{task}"}]
        history: List[Dict[str, Any]] = []

        for round_number in range(1, self.max_rounds + 1):
            raw = ""

            try:
                raw = self.llm.chat(messages=messages, system=self._system_prompt())
                decision = extract_json(raw)
            except Exception as exc:
                self.logger.error(f"{type(exc).__name__}: {exc}")
                messages.append({"role": "assistant", "content": truncate(raw)})
                messages.append(
                    {
                        "role": "user",
                        "content": JSON_REPAIR_PROMPT,
                    }
                )
                continue

            history.append(decision)

            thought = decision.get("thought", "")
            if thought:
                self.logger.plan(thought)

            if decision.get("type") == "final":
                answer = decision.get("answer", "")
                self.logger.done(answer)
                return AgentResult(
                    answer=answer,
                    steps=round_number,
                    history=history,
                    success=True,
                )

            if decision.get("type") != "delegate":
                messages.append(
                    {
                        "role": "user",
                        "content": "Invalid type. Use delegate or final.",
                    }
                )
                continue

            worker_name = decision.get("worker")
            worker_task = decision.get("task", "")

            if worker_name not in self.workers:
                messages.append(
                    {
                        "role": "user",
                        "content": f"Unknown worker {worker_name}. Available workers: {list(self.workers.keys())}",
                    }
                )
                continue

            self.logger.action(f"Delegating to {worker_name}: {worker_task}")

            worker_result = self.workers[worker_name].run(worker_task)

            messages.append(
                {
                    "role": "assistant",
                    "content": safe_json_dumps(decision),
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": safe_json_dumps(
                        {
                            "worker_result": {
                                "worker": worker_name,
                                "success": worker_result.success,
                                "answer": worker_result.answer,
                                "error": worker_result.error,
                            }
                        }
                    ),
                }
            )

        answer = f"Supervisor stopped after max_rounds={self.max_rounds}. Task may be incomplete."
        self.logger.error(answer)

        return AgentResult(
            answer=answer,
            steps=self.max_rounds,
            history=history,
            success=False,
            error=answer,
        )


# ---------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------


def doctor(model: str = "gemma4", base_url: str = "http://localhost:11434") -> bool:
    logger = Logger(True)
    logger.workflow("Running AgentKit doctor")

    client = OllamaClient(model=model, base_url=base_url)

    if not client.healthcheck():
        logger.error("Ollama is not reachable. Start it with: ollama serve")
        return False

    logger.done("Ollama is reachable")

    try:
        response = client.chat(
            messages=[{"role": "user", "content": "Reply with OK only."}],
            system="You are a healthcheck assistant. Return JSON: {\"answer\":\"OK\"}",
        )
        logger.done(f"Model responded: {response.strip()}")
        return True
    except Exception as exc:
        logger.error(str(exc))
        return False


__all__ = [
    "Agent",
    "AgentResult",
    "Workflow",
    "Supervisor",
    "Tool",
    "tool",
    "OllamaClient",
    "doctor",
]