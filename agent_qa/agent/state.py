from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ToolName(str, Enum):
    NAVIGATE = "navigate_to_url"
    CONTEXT = "get_page_context"
    CLICK = "click_element"
    FILL = "fill_input"
    SCROLL = "scroll_page"
    ASSERT = "assert_visual_or_text"
    FAILURE_ARTIFACT = "capture_failure_artifact"


class BrowserPageContext(BaseModel):
    url: str
    title: str
    interactable_markdown: str
    screenshot_base64: str | None = None
    screenshot_path: str | None = None
    observed_at: datetime = Field(default_factory=utc_now)


class NavigateToolCall(BaseModel):
    name: Literal[ToolName.NAVIGATE] = ToolName.NAVIGATE
    url: str


class ContextToolCall(BaseModel):
    name: Literal[ToolName.CONTEXT] = ToolName.CONTEXT


class ClickToolCall(BaseModel):
    name: Literal[ToolName.CLICK] = ToolName.CLICK
    selector_or_text: str = Field(min_length=1)


class FillToolCall(BaseModel):
    name: Literal[ToolName.FILL] = ToolName.FILL
    selector_or_text: str = Field(min_length=1)
    value: str


class ScrollToolCall(BaseModel):
    name: Literal[ToolName.SCROLL] = ToolName.SCROLL
    direction: Literal["up", "down"]
    pixels: int = Field(default=600, ge=1, le=5_000)


class AssertToolCall(BaseModel):
    name: Literal[ToolName.ASSERT] = ToolName.ASSERT
    target_condition: str = Field(min_length=1)


class FailureArtifactToolCall(BaseModel):
    name: Literal[ToolName.FAILURE_ARTIFACT] = ToolName.FAILURE_ARTIFACT
    step_name: str = Field(min_length=1)


ToolCall = Annotated[
    NavigateToolCall
    | ContextToolCall
    | ClickToolCall
    | FillToolCall
    | ScrollToolCall
    | AssertToolCall
    | FailureArtifactToolCall,
    Field(discriminator="name"),
]


class ToolOutcome(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    success: bool
    message: str
    data: dict[str, object] = Field(default_factory=dict)
    error_type: str | None = None


class ExecutionStep(BaseModel):
    number: int
    label: str
    tool_call: ToolCall
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    outcome: ToolOutcome | None = None
    screenshot_path: str | None = None


class FailureDiagnostic(BaseModel):
    message: str
    failed_step: str
    screenshot_path: str | None = None
    dom_snapshot_path: str | None = None
    browser_logs_path: str | None = None
    trace_path: str | None = None


class AgentState(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    scenario: str
    base_url: str
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    current_context: BrowserPageContext | None = None
    execution_log: list[ExecutionStep] = Field(default_factory=list)
    diagnostics: FailureDiagnostic | None = None
    report_paths: dict[str, str] = Field(default_factory=dict)

