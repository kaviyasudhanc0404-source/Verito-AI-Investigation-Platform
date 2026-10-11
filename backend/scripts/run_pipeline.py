"""
scripts/run_pipeline.py
-----------------------
Run the full investigation pipeline for an existing investigation from the
command line, print the live agent activity, then print the saved report.

    cd backend
    python -m scripts.run_pipeline <investigation_id>

Uses the same MongoDB / Qdrant / Groq settings as the API (.env). The
investigation's previous agent runs and report are replaced.
"""

import asyncio
import sys

from app.database.mongodb import connect, disconnect
from app.services.investigation_service import get_investigation
from app.services.report_service import get_report
from app.workflows.pipeline import run_investigation_pipeline


async def _print_event(event: str, payload: dict) -> None:
    line = payload.get("line")
    if line:
        print(f"  [{payload['agent_id']:9}] {line['kind']:5} {line['text']}")
    elif event != "agent_activity":
        print(f"* {event} {payload.get('agent_id', '')} {payload.get('error') or payload.get('reason') or ''}".rstrip())


def _print_report(report: dict) -> None:
    print("\n" + "=" * 80 + f"\n{report.get('title', 'Report')} ({report.get('status', 'final')})\n" + "=" * 80)
    print(report.get("summary", ""))
    quality = report.get("quality", {})
    print(f"Quality gate: {quality.get('status', 'n/a')}" + "".join(f"\n  ! {r}" for r in quality.get("reasons", [])))
    for item in report.get("at_a_glance", []):
        print(f"  • {item['label']}: {item['value']}")
    print("\nKEY FINDINGS")
    for finding in report.get("findings", []):
        print(f"{finding.get('id', '')} [{finding['severity'].upper()} · {finding.get('claim', '')} · "
              f"{finding.get('confidence', '')} confidence] {finding['title']}\n   {finding['detail']} {finding['refs']}")
    print("\nKEY EVENTS")
    for event in report.get("timeline", []):
        print(f"  {event['date']} {event['time']:5} {event.get('zone', ''):9} {event['event']} {event['refs']}")
    print("\nPEOPLE")
    for person in report.get("people", []):
        print(f"  {person['name']} — {person['role']} [{person['category']}, {person['involvement'] or 'n/a'}]\n    {person['note']}")
    for heading, key in (("OPEN QUESTIONS", "open_questions"), ("NEXT STEPS", "next_steps"), ("LIMITATIONS", "limitations")):
        print(f"\n{heading}")
        for item in report.get(key, []):
            print(f"  - {item}")


async def main(investigation_id: str) -> None:
    await connect()
    try:
        inv = await get_investigation(investigation_id)
        if not inv:
            raise SystemExit(f"Investigation {investigation_id} not found.")
        status = await run_investigation_pipeline(investigation_id, inv["user_id"], inv.get("prompt", ""), _print_event)
        print(f"\nPipeline finished with status: {status}")
        report = await get_report(investigation_id)
        if report:
            _print_report(report)
    finally:
        await disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m scripts.run_pipeline <investigation_id>")
    asyncio.run(main(sys.argv[1]))
