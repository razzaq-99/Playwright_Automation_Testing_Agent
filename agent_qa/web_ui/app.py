from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent_qa.agent.executor import QAAgentExecutor
from agent_qa.agent.state import AgentState
from agent_qa.config import get_settings

settings = get_settings()
app = FastAPI(title="Agent QA Control Center", version="0.1.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _runs() -> list[AgentState]:
    results: list[AgentState] = []
    if not settings.artifact_root.exists():
        return results
    for file in settings.artifact_root.glob("*/run.json"):
        try:
            results.append(AgentState.model_validate_json(file.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError):
            continue
    return sorted(results, key=lambda item: item.created_at, reverse=True)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"runs": _runs(), "base_url": settings.base_url})


@app.post("/runs")
async def create_run(scenario: str = Form(...), base_url: str = Form("")) -> RedirectResponse:
    # It is intentionally detached from the request; dashboard polling reads run.json state.
    async def execute() -> None:
        await QAAgentExecutor().run(scenario=scenario, base_url=base_url or None)

    asyncio.create_task(execute())
    return RedirectResponse(url="/", status_code=303)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(request: Request, run_id: str) -> HTMLResponse:
    matches = [run for run in _runs() if run.run_id == run_id]
    if not matches:
        raise HTTPException(status_code=404, detail="Run not found")
    return templates.TemplateResponse(request, "run_detail.html", {"run": matches[0]})


@app.get("/artifacts/{run_id}/{filename:path}")
async def artifact(run_id: str, filename: str) -> FileResponse:
    candidate = (settings.artifact_root / run_id / filename).resolve()
    root = settings.artifact_root.resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(candidate)

