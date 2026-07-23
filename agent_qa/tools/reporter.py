from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, SubElement

from agent_qa.agent.state import AgentState, StepStatus


class ReportWriter:
    """Writes machine-readable state, JUnit XML, and a portable HTML run summary."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def write_state(self, state: AgentState) -> Path:
        path = self.run_dir / "run.json"
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        return path

    def write_junit(self, state: AgentState) -> Path:
        suite = Element("testsuite", name="Agent QA", tests="1")
        failure = state.status in {"failed", "error"}
        suite.set("failures", "1" if failure else "0")
        suite.set("timestamp", state.created_at.isoformat())
        case = SubElement(suite, "testcase", name=state.scenario, classname="agent_qa")
        if failure:
            diagnostic = state.diagnostics.message if state.diagnostics else "Run failed"
            node = SubElement(case, "failure", message=diagnostic)
            node.text = diagnostic
        path = self.run_dir / "junit.xml"
        ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
        return path

    def write_html(self, state: AgentState) -> Path:
        rows = "".join(
            f"<tr><td>{step.number}</td><td>{html.escape(step.label)}</td><td><code>{html.escape(step.tool_call.name)}</code></td><td><span class='badge {step.status}'>{step.status}</span></td><td>{html.escape(step.outcome.message if step.outcome else '')}</td></tr>"
            for step in state.execution_log
        )
        diagnostic = html.escape(state.diagnostics.message) if state.diagnostics else "No failures recorded."
        document = f"""<!doctype html><html><head><meta charset='utf-8'><title>QA Run {state.run_id}</title>
        <style>body{{font:15px Inter,system-ui,sans-serif;background:#f8fafc;color:#0f172a;margin:40px}}.card{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:24px;margin-bottom:18px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:12px;border-bottom:1px solid #e2e8f0;text-align:left}}.badge{{padding:4px 8px;border-radius:999px;font-size:12px}}.passed{{background:#dcfce7;color:#166534}}.failed,.error{{background:#ffe4e6;color:#9f1239}}.running{{background:#e0e7ff;color:#3730a3}}code{{font-size:12px}}</style></head><body>
        <div class='card'><h1>Executive Summary</h1><p><b>{html.escape(state.scenario)}</b></p><p>Run <code>{state.run_id}</code> · Status: <span class='badge {state.status}'>{state.status}</span></p></div>
        <div class='card'><h2>Live Step Execution</h2><table><thead><tr><th>#</th><th>Step</th><th>Tool</th><th>Status</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>
        <div class='card'><h2>Failure Diagnostics</h2><p>{diagnostic}</p></div></body></html>"""
        path = self.run_dir / "summary.html"
        path.write_text(document, encoding="utf-8")
        return path

    def write_all(self, state: AgentState) -> dict[str, str]:
        return {"state": str(self.write_state(state)), "junit": str(self.write_junit(state)), "html": str(self.write_html(state))}

