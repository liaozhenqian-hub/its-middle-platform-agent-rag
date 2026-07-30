from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from urllib.parse import quote
from uuid import uuid4

import httpx


CLARIFICATION_MARKERS = (
    "请确认",
    "请您确认",
    "请提供",
    "请补充",
    "请您补充",
    "请描述",
    "请选择",
    "需要补充",
    "候选",
    "具体是",
    "请问您希望",
    "请问您想",
    "您希望从哪个",
    "请告诉我",
    "请问您关注",
    "建议你告诉我",
    "建议您告诉我",
    "请问您使用",
)
REFUSAL_MARKERS = (
    "服务范围",
    "不能查询",
    "不能执行",
    "无法查询",
    "无法执行",
    "无法协助",
    "不支持写",
    "不会执行",
    "不能泄露",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only the selected stable case ID; repeat for multiple cases.",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("domain-public-benchmark-cases-100-20260717.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("domain-public-benchmark-results-100-20260717.json"),
    )
    return parser.parse_args()


def select_cases(cases: list[dict], case_ids: list[str]) -> list[dict]:
    if not case_ids:
        return cases
    requested = set(case_ids)
    known = {str(case["id"]) for case in cases}
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(f"unknown benchmark case IDs: {', '.join(unknown)}")
    return [case for case in cases if str(case["id"]) in requested]


def citation_key(citation: dict) -> str:
    return f"{citation.get('source_type', '')}:{citation.get('source_id', '')}"


def evaluate(case: dict, response: dict, latency_ms: float) -> dict[str, bool]:
    tools = [item.get("tool_name", "") for item in response.get("tool_runs", [])]
    citations = response.get("citations", [])
    citation_types = {item.get("source_type", "") for item in citations}
    answer = str(response.get("answer") or "")
    expected_behavior = case["expected_behavior"]
    clarification = any(marker in answer for marker in CLARIFICATION_MARKERS)
    refusal = any(marker in answer for marker in REFUSAL_MARKERS)
    behavior_ok = {
        "answer": not refusal,
        "clarify": clarification,
        "refuse": refusal and not tools,
    }[expected_behavior]
    return {
        "completed": response.get("status") == "completed",
        "expected_tools": set(case["expected_tools"]).issubset(tools),
        "expected_citations": set(case["expected_citation_types"]).issubset(citation_types),
        "evidence": not case["expects_evidence"] or bool(citations),
        "behavior": behavior_ok,
        "latency": latency_ms <= float(case["latency_budget_ms"]),
    }


async def run_case(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    case: dict,
    base_url: str,
) -> dict:
    conversation_id = f"public-benchmark-100:{case['id']}:{uuid4()}"
    started: float | None = None
    try:
        async with semaphore:
            started = perf_counter()
            response = await client.post(
                f"{base_url}/api/v1/agent/chat",
                json={
                    "conversation_id": conversation_id,
                    "message": case["question"],
                    "knowledge_space_id": "middle-platform",
                },
            )
        latency_ms = round((perf_counter() - started) * 1000, 2)
        response.raise_for_status()
        payload = response.json()
        citations = [
            {
                "source_type": item.get("source_type"),
                "source_id": item.get("source_id"),
                "title": item.get("title"),
                "domain": item.get("domain"),
            }
            for item in payload.get("citations", [])
        ]
        tools = [
            {
                "tool_name": item.get("tool_name"),
                "agent_name": item.get("agent_name"),
                "status": item.get("status"),
                "duration_ms": item.get("duration_ms"),
            }
            for item in payload.get("tool_runs", [])
        ]
        checks = evaluate(case, {**payload, "citations": citations, "tool_runs": tools}, latency_ms)
        unique_citations = len({citation_key(item) for item in citations})
        return {
            **case,
            "conversation_id": conversation_id,
            "http_status": response.status_code,
            "status": payload.get("status"),
            "latency_ms": latency_ms,
            "answer": payload.get("answer"),
            "last_agent": payload.get("last_agent"),
            "tools": tools,
            "citations": citations,
            "citation_count": len(citations),
            "unique_citation_count": unique_citations,
            "checks": checks,
            "passed": all(checks.values()),
            "error_type": None,
        }
    except Exception as exc:
        return {
            **case,
            "conversation_id": conversation_id,
            "http_status": getattr(getattr(exc, "response", None), "status_code", None),
            "status": "error",
            "latency_ms": (
                round((perf_counter() - started) * 1000, 2)
                if started is not None
                else None
            ),
            "answer": None,
            "last_agent": "",
            "tools": [],
            "citations": [],
            "citation_count": 0,
            "unique_citation_count": 0,
            "checks": {
                "completed": False,
                "expected_tools": False,
                "expected_citations": False,
                "evidence": False,
                "behavior": False,
                "latency": False,
            },
            "passed": False,
            "error_type": type(exc).__name__,
        }
    finally:
        try:
            await client.delete(
                f"{base_url}/api/v1/agent/conversations/{quote(conversation_id, safe='')}",
            )
        except Exception:
            pass


async def main() -> None:
    args = parse_args()
    definition = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = select_cases(definition["cases"], args.case_id)
    semaphore = asyncio.Semaphore(args.concurrency)
    timeout = httpx.Timeout(args.timeout, connect=20.0)
    results: list[dict] = []
    lock = asyncio.Lock()

    async with httpx.AsyncClient(timeout=timeout) as client:
        async def execute(case: dict) -> None:
            result = await run_case(client, semaphore, case, args.base_url.rstrip("/"))
            async with lock:
                results.append(result)
                done = len(results)
                failed = ",".join(name for name, ok in result["checks"].items() if not ok)
                print(
                    f"[{done:03d}/{len(cases)}] {result['id']} status={result['status']} "
                    f"latency_ms={result['latency_ms']} tools={len(result['tools'])} "
                    f"citations={result['citation_count']} failed={failed or '-'}",
                    flush=True,
                )

        await asyncio.gather(*(execute(case) for case in cases))

    order = {case["id"]: index for index, case in enumerate(cases)}
    results.sort(key=lambda item: order[item["id"]])
    report = {
        "benchmark": definition["name"],
        "total_cases": len(results),
        "concurrency": args.concurrency,
        "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {args.output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
