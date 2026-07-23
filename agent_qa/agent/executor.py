from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from agent_qa.agent.state import (
    AgentState,
    AssertToolCall,
    BrowserPageContext,
    ClickToolCall,
    ExecutionStep,
    FailureDiagnostic,
    FillToolCall,
    NavigateToolCall,
    RunStatus,
    ScrollToolCall,
    StepStatus,
    ToolCall,
    ToolOutcome,
)
from agent_qa.config import Settings, get_settings
from agent_qa.tools.browser import BrowserController
from agent_qa.tools.reporter import ReportWriter


class ScenarioPlanner:
    """A deterministic function-calling planner for repeatable baseline coverage.

    It intentionally turns explicit natural-language directives into typed calls. A production
    LLM planner can implement the same `plan` interface without changing the executor.
    """

    _ACTION_PATTERN = re.compile(
        r"(?:then\s+)?(?P<verb>navigate to|go to|click|select|fill|enter|type|scroll down|scroll up|verify|assert)\s+(?P<arg>.+?)(?=(?:\s+(?:then\s+)?(?:navigate to|go to|click|select|fill|enter|type|scroll down|scroll up|verify|assert)\b)|[.;]|$)",
        re.IGNORECASE,
    )

    def plan(self, scenario: str, base_url: str) -> list[tuple[str, ToolCall]]:
        plan: list[tuple[str, ToolCall]] = [("Open target application", NavigateToolCall(url=self._target_url(scenario, base_url)))]
        matches = list(self._ACTION_PATTERN.finditer(scenario))
        for match in matches:
            verb, arg = match.group("verb").lower(), match.group("arg").strip(" '\"")
            if verb in {"navigate to", "go to"}:
                plan.append((f"Navigate to {arg}", NavigateToolCall(url=arg)))
            elif verb in {"click", "select"}:
                plan.append((f"Click {arg}", ClickToolCall(selector_or_text=arg)))
            elif verb in {"fill", "enter", "type"}:
                target, value = self._split_fill(arg)
                plan.append((f"Fill {target}", FillToolCall(selector_or_text=target, value=value)))
            elif verb.startswith("scroll"):
                plan.append((verb.title(), ScrollToolCall(direction="down" if "down" in verb else "up", pixels=650)))
            else:
                plan.append((f"Verify {arg}", AssertToolCall(target_condition=arg)))

        # A checkout scenario still gets a DOM-guided action even when written as a high-level goal.
        if not matches and "checkout" in scenario.lower():
            plan.extend(
                [
                    ("Open checkout", ClickToolCall(selector_or_text="checkout")),
                    ("Verify checkout page", AssertToolCall(target_condition="text: checkout")),
                ]
            )
        elif not matches:
            plan.append(("Verify the application is reachable", AssertToolCall(target_condition="url:" + urlparse(base_url).netloc)))
        return plan

    @staticmethod
    def _target_url(scenario: str, base_url: str) -> str:
        found = re.search(r"https?://[^\s'\"]+", scenario)
        return found.group(0).rstrip(".,)") if found else base_url

    @staticmethod
    def _split_fill(argument: str) -> tuple[str, str]:
        for separator in (" with ", " as ", " = "):
            if separator in argument.lower():
                index = argument.lower().index(separator)
                return argument[:index].strip(), argument[index + len(separator) :].strip(" '\"")
        return argument, ""


class QAAgentExecutor:
    """Observe → plan → act → retry executor, with a new Playwright context per run."""

    def __init__(self, settings: Settings | None = None, planner: ScenarioPlanner | None = None) -> None:
        self.settings = settings or get_settings()
        self.planner = planner or ScenarioPlanner()

    async def run(self, scenario: str, base_url: str | None = None) -> AgentState:
        run_id = f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        effective_base_url = base_url or self.settings.base_url
        state = AgentState(run_id=run_id, scenario=scenario, base_url=effective_base_url, status=RunStatus.RUNNING, started_at=datetime.now(timezone.utc))
        run_dir = self.settings.artifact_root / run_id
        reporter = ReportWriter(run_dir)
        browser = BrowserController(self.settings, run_dir)
        reporter.write_state(state)
        try:
            await browser.start()
            for number, (label, call) in enumerate(self.planner.plan(scenario, effective_base_url), start=1):
                step = ExecutionStep(number=number, label=label, tool_call=call, status=StepStatus.RUNNING, started_at=datetime.now(timezone.utc))
                state.execution_log.append(step)
                reporter.write_state(state)
                outcome = await self._perform_with_retries(browser, call, step)
                step.outcome = outcome
                step.finished_at = datetime.now(timezone.utc)
                step.status = StepStatus.PASSED if outcome.success else StepStatus.FAILED
                try:
                    page_context = await browser.get_page_context()
                    state.current_context = page_context
                    step.screenshot_path = page_context.screenshot_path
                except Exception:
                    # Never hide the primary tool outcome because diagnostic context could not be captured.
                    pass
                if not outcome.success:
                    artifacts = await browser.capture_failure_artifact(f"step-{number}-{label}")
                    state.status = RunStatus.FAILED
                    state.diagnostics = FailureDiagnostic(message=outcome.message, failed_step=label, **artifacts)
                    break
            else:
                state.status = RunStatus.PASSED
        except Exception as exc:
            state.status = RunStatus.ERROR
            state.diagnostics = FailureDiagnostic(message=str(exc), failed_step="Agent initialization or orchestration")
        finally:
            trace_path = await browser.stop(save_trace=state.status in {RunStatus.FAILED, RunStatus.ERROR})
            if state.diagnostics and trace_path:
                state.diagnostics.trace_path = trace_path
            state.finished_at = datetime.now(timezone.utc)
            state.report_paths = reporter.write_all(state)
            reporter.write_state(state)
        return state

    async def _perform_with_retries(self, browser: BrowserController, call: ToolCall, step: ExecutionStep) -> ToolOutcome:
        last = ToolOutcome(success=False, message="No attempts made")
        for attempt in range(1, self.settings.max_retries + 1):
            step.attempts = attempt
            last = await browser.execute(call)
            if last.success:
                return last
            # DOM changed or an overlay appeared: refresh context before the next auto-waiting retry.
            if attempt < self.settings.max_retries:
                try:
                    await browser.get_page_context()
                except Exception:
                    pass
        return last

