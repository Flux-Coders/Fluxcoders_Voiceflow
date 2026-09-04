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

from app.engine.llm_provider import (
    LLMError,
    LLMRateLimitError,
    OpenAIConfig,
    OpenAILLMClient,
)
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


async def diagnose_rate_limit(config: OpenAIConfig) -> dict[str, Any]:
    """Inspects upstream 429 response to distinguish quota vs request/token limits without leaking secrets."""
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            status_code = resp.status_code
            try:
                body = resp.json()
                err = body.get("error", {})
                code = err.get("code")
                err_type = err.get("type")
                msg = err.get("message", "")
            except Exception:
                code = None
                err_type = None
                msg = resp.text[:200]

            msg_lower = str(msg).lower()
            code_str = str(code or "").lower()
            type_str = str(err_type or "").lower()

            if (
                code_str == "insufficient_quota"
                or type_str == "insufficient_quota"
                or "quota" in msg_lower
                or "billing" in msg_lower
                or "credit" in msg_lower
                or "plan" in msg_lower
            ):
                classification = "QUOTA_OR_BILLING_EXHAUSTED"
                description = "Account has insufficient credits, unpaid invoices, or exceeded its hard monthly spending limit."
                remediation = "Check your OpenAI account billing dashboard at https://platform.openai.com/usage or https://platform.openai.com/account/billing to verify credits and payment methods."
            elif (
                code_str == "rate_limit_exceeded"
                or type_str in ("requests", "tokens")
                or "requests per min" in msg_lower
                or "tokens per min" in msg_lower
                or "tpm" in msg_lower
                or "rpm" in msg_lower
            ):
                classification = "TEMPORARY_RATE_LIMIT"
                description = "Requests-per-minute (RPM) or Tokens-per-minute (TPM) threshold reached for this model tier."
                remediation = "Wait a few seconds/minutes before making the next request, or request a tier limit increase."
            else:
                classification = "RATE_LIMIT_OTHER"
                description = f"Upstream returned HTTP {status_code}."
                remediation = "Inspect your organization / project limits on the provider platform."

            return {
                "status_code": status_code,
                "error_code": code or "None",
                "error_type": err_type or "None",
                "message": msg or "No message provided",
                "classification": classification,
                "description": description,
                "remediation": remediation,
            }
    except Exception as probe_err:
        return {
            "status_code": 429,
            "error_code": "PROBE_FAILED",
            "error_type": "None",
            "message": str(probe_err),
            "classification": "DIAGNOSTIC_PROBE_ERROR",
            "description": "Unable to complete diagnostic HTTP probe.",
            "remediation": "Verify network connectivity and base URL accessibility.",
        }


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
        print(f"  --> HTTP Status   : {e.status_code if e.status_code is not None else 'N/A'}")
        print(f"  --> Latency       : {latency_1_ms:.2f} ms")
        print(f"  --> Error Detail  : {e.message}")
        if isinstance(e, LLMRateLimitError) or e.status_code == 429:
            print("-" * 70)
            print("  [DIAGNOSTIC: HTTP 429 RATE LIMIT CLASSIFICATION]")
            diag = await diagnose_rate_limit(config)
            print(f"  --> Upstream Status Code : {diag['status_code']}")
            print(f"  --> Upstream Error Code  : {diag['error_code']}")
            print(f"  --> Upstream Error Type  : {diag['error_type']}")
            print(f"  --> Upstream Message     : {diag['message']}")
            print(f"  --> Classification       : {diag['classification']}")
            print(f"  --> Description          : {diag['description']}")
            print(f"  --> Remediation Action   : {diag['remediation']}")
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
        print(f"  --> HTTP Status   : {e.status_code if e.status_code is not None else 'N/A'}")
        print(f"  --> Latency       : {latency_2_ms:.2f} ms")
        print(f"  --> Error Detail  : {e.message}")
        if isinstance(e, LLMRateLimitError) or e.status_code == 429:
            print("-" * 70)
            print("  [DIAGNOSTIC: HTTP 429 RATE LIMIT CLASSIFICATION]")
            diag = await diagnose_rate_limit(config)
            print(f"  --> Upstream Status Code : {diag['status_code']}")
            print(f"  --> Upstream Error Code  : {diag['error_code']}")
            print(f"  --> Upstream Error Type  : {diag['error_type']}")
            print(f"  --> Upstream Message     : {diag['message']}")
            print(f"  --> Classification       : {diag['classification']}")
            print(f"  --> Description          : {diag['description']}")
            print(f"  --> Remediation Action   : {diag['remediation']}")
    except Exception as e:
        print(f"  --> Unexpected Error: {type(e).__name__}: {e}")

    print("=" * 70)
    print("Smoke Test Completed.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_smoke_test())

