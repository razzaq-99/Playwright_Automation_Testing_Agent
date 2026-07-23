from __future__ import annotations

import argparse
import asyncio

from agent_qa.agent.executor import QAAgentExecutor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an autonomous natural-language browser QA scenario")
    parser.add_argument("--scenario", required=True, help="High-level test scenario")
    parser.add_argument("--base-url", help="Override QA_BASE_URL for this run")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    state = await QAAgentExecutor().run(args.scenario, args.base_url)
    print(f"{state.status.upper()}: {state.run_id}")
    for report_name, report_path in state.report_paths.items():
        print(f"{report_name}: {report_path}")
    return 0 if state.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

