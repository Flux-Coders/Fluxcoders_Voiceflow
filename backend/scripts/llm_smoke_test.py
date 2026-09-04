"""VoiceFlow OpenAI-Compatible LLM Real API Smoke Test.

Developer-only script to verify live connectivity with configured OpenAI-compatible API
using credentials from .env without printing or leaking secrets.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
import httpx

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.engine.llm_provider import OpenAIConfig, OpenAILLMClient, LLMError
from app.models import LLMMessage
from app.tools.train_search import TRAIN_SEARCH_TOOL_SCHEMA


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


async def run_smoke_test() -> None:
    env = load_env_vars()

    provider = env.get("VOICEFLOW_LLM_PROVIDER", "mock").strip()
    api_key = env.get("OPENAI_API_KEY", "").strip()
    base_url = env.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    model = env.get("OPENAI_MODEL", "gpt-4o-mini").strip()
    temperature = float(env.get("OPENAI_TEMPERATURE", "0.1"))
    timeout_sec = float(env.get("OPENAI_TIMEOUT_SECONDS", "15.0"))

    is_real_key = bool(api_key and api_key not in ("your_openai_api_key_here", "your_real_key_here", ""))

    print("=" * 70)
    print("VoiceFlow Real LLM Provider Smoke Test")
    print("=" * 70)
    print(f"Provider Configured : {provider}")
    print(f"Base URL            : {base_url}")
    print(f"Model               : {model}")
    print(f"Temperature         : {temperature}")
    print(f"Timeout (s)         : {timeout_sec}s")
    print(f"API Key Set         : {'YES (Valid non-empty token)' if is_real_key else 'NO / PLACEHOLDER'}")
    print("-" * 70)

    if not is_real_key:
        print("[INFO] OPENAI_API_KEY is not configured or is a placeholder in .env.")
        print("To run live API smoke testing against an OpenAI-compatible endpoint:")
        print("  1. Add OPENAI_API_KEY=<your_key> to your .env file.")
        print("  2. Run: python backend/scripts/llm_smoke_test.py")
        print("=" * 70)
        return

    config = OpenAIConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temperature,
        timeout_seconds=timeout_sec,
    )
    client = OpenAILLMClient(config=config)

    # -------------------------------------------------------------
    # 1. Direct Conversational Test
    # -------------------------------------------------------------
    print("[TEST 1/2] Sending conversational completion request...")
    prompt_1 = "Hello from VoiceFlow. Please confirm you can assist with travel queries in one short sentence."
    messages_1 = [
        LLMMessage(role="system", content="You are VoiceFlow, a real-time travel voice assistant."),
        LLMMessage(role="user", content=prompt_1),
    ]

    t0 = time.perf_counter()
    try:
        resp_1 = await client.generate(messages=messages_1)
        latency_1_ms = (time.perf_counter() - t0) * 1000.0
        print(f"  --> Status        : SUCCESS")
        print(f"  --> Latency       : {latency_1_ms:.2f} ms")
        print(f"  --> Finish Reason : {resp_1.finish_reason}")
        print(f"  --> Content       : {resp_1.content}")
    except LLMError as e:
        latency_1_ms = (time.perf_counter() - t0) * 1000.0
        print(f"  --> Status        : FAILED ({e.__class__.__name__})")
        print(f"  --> Latency       : {latency_1_ms:.2f} ms")
        print(f"  --> Error Detail  : {e.message}")
        print("=" * 70)
        return
    except Exception as e:
        print(f"  --> Unexpected Error: {type(e).__name__}: {e}")
        print("=" * 70)
        return

    print("-" * 70)

    # -------------------------------------------------------------
    # 2. Structured Function / Tool Call Test
    # -------------------------------------------------------------
    print("[TEST 2/2] Sending structured tool calling request (search_trains)...")
    prompt_2 = "Find 3A trains from Nagpur to Mumbai departing tomorrow after 8 PM."
    messages_2 = [
        LLMMessage(
            role="system",
            content="You are VoiceFlow. If user requests train search, invoke the search_trains tool with extracted parameters.",
        ),
        LLMMessage(role="user", content=prompt_2),
    ]

    t1 = time.perf_counter()
    try:
        resp_2 = await client.generate(messages=messages_2, tools=[TRAIN_SEARCH_TOOL_SCHEMA])
        latency_2_ms = (time.perf_counter() - t1) * 1000.0
        print(f"  --> Status        : SUCCESS")
        print(f"  --> Latency       : {latency_2_ms:.2f} ms")
        print(f"  --> Finish Reason : {resp_2.finish_reason}")
        print(f"  --> Tool Calls    : {len(resp_2.tool_calls)}")
        for i, tc in enumerate(resp_2.tool_calls):
            print(f"      [{i+1}] Function : {tc.name}")
            print(f"          Arguments: {json.dumps(tc.arguments, indent=12).strip()}")
        if resp_2.slot_patch:
            print(f"  --> Slot Patch    : {resp_2.slot_patch.set_slots}")
    except LLMError as e:
        latency_2_ms = (time.perf_counter() - t1) * 1000.0
        print(f"  --> Status        : FAILED ({e.__class__.__name__})")
        print(f"  --> Latency       : {latency_2_ms:.2f} ms")
        print(f"  --> Error Detail  : {e.message}")
    except Exception as e:
        print(f"  --> Unexpected Error: {type(e).__name__}: {e}")

    print("=" * 70)
    print("Smoke Test Completed.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_smoke_test())

