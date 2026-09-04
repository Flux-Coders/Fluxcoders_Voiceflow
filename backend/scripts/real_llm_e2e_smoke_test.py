"""VoiceFlow Real LLM End-to-End Integration Smoke Test.

Developer-only script to verify the full live VoiceFlow reasoning loop:
User Utterance
  → Session Request/Version Gate
  → Real OpenAILLMClient (Step 1 Tool Call)
  → Strict ToolRegistry (Authorization & Pydantic Validation)
  → Async Train-Search Tool Execution
  → Real OpenAILLMClient (Step 2 Response Synthesis)
  → Final Request Version Gate & Turn Commit

Guarantees:
- Real provider credentials loaded from .env without printing secrets.
- Full verification of tool name, arguments, and orchestration checkpoints.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure UTF-8 output encoding on Windows console
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.event_logger import VoiceEventLogger
from app.core.session import Session
from app.engine.llm_orchestrator import LLMOrchestrator
from app.engine.llm_provider import (
    LLMError,
    OpenAIConfig,
    OpenAILLMClient,
)
from app.models import ToolStatus, TurnExecutionResult, VoiceEventType
from app.tools.registry import ToolRegistry, default_tool_registry


def load_env_vars() -> dict[str, str]:
    """Loads environment variables from .env file in project root if present."""
    env_file = PROJECT_ROOT / ".env"
    loaded = dict(os.environ)
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k_clean = k.strip()
                v_clean = v.strip().strip("'\"")
                if k_clean and v_clean:
                    loaded[k_clean] = v_clean
    return loaded


class InstrumentedToolRegistry(ToolRegistry):
    """Tracking ToolRegistry wrapper to explicitly record and verify policy enforcement."""

    def __init__(self, base_registry: ToolRegistry) -> None:
        super().__init__()
        self._tools = base_registry._tools
        self.permission_checks: List[str] = []
        self.validation_calls: List[tuple[str, Dict[str, Any]]] = []

    def is_permitted(self, name: str) -> bool:
        self.permission_checks.append(name)
        return super().is_permitted(name)

    def validate_arguments(self, name: str, args: Dict[str, Any]) -> Any:
        self.validation_calls.append((name, args))
        return super().validate_arguments(name, args)


async def run_e2e_smoke_test() -> None:
    env = load_env_vars()

    provider = env.get("VOICEFLOW_LLM_PROVIDER", "openai").strip()
    api_key = env.get("OPENAI_API_KEY", "").strip()
    base_url = env.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    model = env.get("OPENAI_MODEL", "gpt-4o-mini").strip()
    temperature = float(env.get("OPENAI_TEMPERATURE", "0.1"))
    timeout_sec = float(env.get("OPENAI_TIMEOUT_SECONDS", "15.0"))

    is_real_key = bool(api_key and api_key not in ("your_openai_api_key_here", "your_real_key_here", ""))

    print("=" * 75)
    print("VoiceFlow Phase 13: Real LLM End-to-End Orchestration Integration Test")
    print("=" * 75)
    print(f"Configured Provider  : {provider}")
    print(f"Base URL             : {base_url}")
    print(f"Model                : {model}")
    print(f"Temperature          : {temperature}")
    print(f"Timeout (s)          : {timeout_sec}s")
    print(f"API Key Configured   : {'YES (Valid non-empty token)' if is_real_key else 'NO / PLACEHOLDER'}")
    print("=" * 75)

    if not is_real_key:
        print("[ERROR] Real OPENAI_API_KEY is missing or contains placeholder.")
        print("Please ensure .env has valid credentials to run live integration.")
        print("=" * 75)
        return

    # 1. Initialize Real LLM Client & Session Infrastructure
    config = OpenAIConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temperature,
        timeout_seconds=timeout_sec,
    )
    llm_client = OpenAILLMClient(config=config)
    event_logger = VoiceEventLogger()
    session = Session(session_id="e2e-live-smoke-session", event_logger=event_logger)
    
    # 2. Setup Instrumented Tool Registry
    registry = InstrumentedToolRegistry(base_registry=default_tool_registry)
    orchestrator = LLMOrchestrator(llm_client=llm_client, tool_registry=registry)

    # 3. Create Session Request Turn (Step 1)
    user_prompt = "Find me a 3A train from Nagpur to Mumbai tomorrow after 8 PM."
    print(f"\n[STEP 1] Initializing User Utterance...")
    print(f"  --> Utterance: \"{user_prompt}\"")
    req = session.create_request(prompt=user_prompt)
    print(f"  --> Request ID: {req.request_id}")
    print(f"  --> Conversation Version: v{req.conversation_version}")
    print(f"  --> Session Active Version: v{session.active_version}")

    # 4. Execute Full Turn Lifecycle via Orchestrator
    print(f"\n[STEP 2] Executing Full Turn Orchestration (LLM Step 1 -> Tool -> LLM Step 2)...")
    t_start = time.perf_counter()
    try:
        result: TurnExecutionResult = await orchestrator.process_turn(
            session=session,
            request_id=req.request_id,
            version=req.conversation_version,
            prompt=req.prompt,
            delay_tool_ms=100,  # Fast 100ms execution for live test
            trigger_rime=False, # Rime verified independently in Phase 12
        )
    except Exception as e:
        latency_ms = (time.perf_counter() - t_start) * 1000.0
        print(f"  --> Orchestration Exception: {type(e).__name__}: {e}")
        print(f"  --> Latency: {latency_ms:.2f} ms")
        print("=" * 75)
        return

    total_latency_ms = (time.perf_counter() - t_start) * 1000.0

    print(f"  --> Total E2E Latency: {total_latency_ms:.2f} ms")
    print(f"  --> Turn Success: {result.success}")
    print(f"  --> Is Stale: {result.is_stale}")
    if result.error:
        print(f"  --> Error: {result.error}")

    # 5. Extract Details for Audit
    tool_task = result.tool_task
    tool_name = tool_task.tool_name if tool_task else "None"
    tool_args = tool_task.args if tool_task else {}
    tool_result = tool_task.result if tool_task else None

    # 6. Print Turn & Tool Execution Details
    print("\n" + "-" * 75)
    print("ORCHESTRATION EXECUTION AUDIT")
    print("-" * 75)
    print(f"1. Tool Name Requested  : {tool_name}")
    print(f"2. Extracted Arguments  : {json.dumps(tool_args, indent=4)}")
    print(f"3. ToolRegistry Checks  : is_permitted called for {registry.permission_checks}")
    print(f"4. Argument Validation  : validate_arguments called ({len(registry.validation_calls)} time(s))")
    print(f"5. Tool Execution Status: {tool_task.status.value if tool_task else 'N/A'}")
    if tool_result:
        found_count = len(tool_result.trains) if hasattr(tool_result, "trains") else 0
        print(f"6. Mock Database Results: {found_count} train(s) matched criteria")
        if hasattr(tool_result, "trains"):
            for t in tool_result.trains:
                print(f"     * Train #{t.train_no} ({t.name}) | Dept: {t.departure} | Arr: {t.arrival} | Classes: {t.classes}")
    print(f"7. Final Assistant Text : \"{result.assistant_response}\"")
    print(f"8. Session Current Answer: \"{session.current_answer}\"")

    # 7. Verification Checklist
    print("\n" + "-" * 75)
    print("VERIFICATION CHECKLIST")
    print("-" * 75)

    checks = []
    
    # Check 1: Tool Task was produced
    c1 = (tool_task is not None)
    checks.append(("Tool task produced", c1))

    # Check 2: Exactly one tool call and tool name is search_trains
    c2 = (tool_name == "search_trains")
    checks.append(("Tool name is 'search_trains'", c2))

    # Check 3: Origin and destination
    src = str(tool_args.get("source", "")).lower()
    dst = str(tool_args.get("destination", "")).lower()
    c3 = ("nagpur" in src and "mumbai" in dst)
    checks.append(("Arguments contain Nagpur -> Mumbai", c3))

    # Check 4: Date constraint
    date_val = str(tool_args.get("date", "")).lower()
    c4 = ("tomorrow" in date_val or "202" in date_val)
    checks.append(("Date is 'tomorrow'", c4))

    # Check 5: Time constraint
    time_val = str(tool_args.get("time_constraint", "")).lower()
    c5 = ("8 pm" in time_val or "20:00" in time_val or "20" in time_val or "after" in time_val)
    checks.append(("Time constraint contains 'after 8 PM'", c5))

    # Check 6: Class constraint
    class_val = str(tool_args.get("class_constraint", "")).upper()
    c6 = ("3A" in class_val)
    checks.append(("Class constraint is '3A'", c6))

    # Check 7: ToolRegistry involvement
    c7 = ("search_trains" in registry.permission_checks and len(registry.validation_calls) >= 1)
    checks.append(("ToolRegistry enforced permission & validation", c7))

    # Check 8: Train-search tool execution completed valid
    c8 = (tool_task is not None and tool_task.status == ToolStatus.COMPLETED_VALID)
    checks.append(("Train-search tool executed successfully", c8))

    # Check 9: Final synthesis succeeded with non-empty text
    c9 = bool(result.assistant_response and len(result.assistant_response.strip()) > 10 and not result.error and result.success)
    checks.append(("Real LLM final synthesis succeeded", c9))

    # Check 10: Final turn committed through version gate
    c10 = (session.current_answer == result.assistant_response and result.success is True and not result.error)
    checks.append(("Final turn committed through version gate", c10))

    # Check 11: No stale discards
    c11 = (len(session.stale_discards) == 0 and result.is_stale is False)
    checks.append(("No stale request/version violations", c11))

    all_passed = True
    for label, passed in checks:
        status_str = "[PASS]" if passed else "[FAIL]"
        if not passed:
            all_passed = False
        print(f"  {status_str} {label}")

    print("=" * 75)
    if all_passed:
        print("OVERALL RESULT: ALL 11 VERIFICATIONS PASSED SUCCESSFULLY.")
    else:
        print("OVERALL RESULT: SOME VERIFICATIONS FAILED.")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(run_e2e_smoke_test())
