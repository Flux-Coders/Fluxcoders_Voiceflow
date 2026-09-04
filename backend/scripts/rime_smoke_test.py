"""VoiceFlow Rime TTS Real API Smoke Test.

Developer-only script to verify live connectivity with Rime Text-to-Speech API
using credentials from .env without printing or leaking secrets.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
import httpx

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


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

    api_key = env.get("RIME_API_KEY", "").strip()
    endpoint = env.get("RIME_ENDPOINT", "https://users.rime.ai/v1/rime-tts").strip()
    model = env.get("RIME_MODEL", "mistv3").strip()
    speaker = env.get("RIME_SPEAKER", "astra").strip()
    language = env.get("RIME_LANGUAGE", "eng").strip()
    audio_format = env.get("RIME_AUDIO_FORMAT", "pcm").strip()
    sample_rate = int(env.get("RIME_SAMPLE_RATE", "16000"))

    print("=" * 70)
    print("VoiceFlow Rime TTS Real API Smoke Test")
    print("=" * 70)
    print(f"Endpoint     : {endpoint}")
    print(f"Model        : {model}")
    print(f"Speaker      : {speaker}")
    print(f"Language     : {language}")
    print(f"Audio Format : {audio_format}")
    print(f"Sample Rate  : {sample_rate} Hz")
    print(f"API Key Set  : {'YES (Valid non-empty token)' if api_key and api_key != 'your_real_key_here' else 'NO / PLACEHOLDER'}")
    print("-" * 70)

    if not api_key or api_key in ("your_real_key_here", "your_rime_api_key_here", ""):
        print("[ERROR] RIME_API_KEY is not configured with a valid real key in .env.")
        print("Please ensure your real Rime API key is set in .env before running this smoke test.")
        print("=" * 70)
        return

    # Map audio format to Accept header
    fmt = audio_format.lower()
    if fmt in ("pcm", "l16", "raw"):
        accept_header = "audio/pcm"
        file_ext = "pcm"
    elif fmt in ("mp3", "mpeg"):
        accept_header = "audio/mpeg"
        file_ext = "mp3"
    elif fmt in ("wav", "wave"):
        accept_header = "audio/wav"
        file_ext = "wav"
    else:
        accept_header = "audio/pcm"
        file_ext = "pcm"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": accept_header,
    }

    test_sentence = "Hello from VoiceFlow. This is a Rime streaming test."
    payload = {
        "speaker": speaker,
        "text": test_sentence,
        "modelId": model,
        "lang": language,
        "samplingRate": sample_rate,
        "speedAlpha": 1.0,
    }

    print(f"Test Sentence: \"{test_sentence}\"")
    print("Initiating streaming request...")

    t_start = time.perf_counter()
    t_first_chunk: float | None = None
    chunks_received = 0
    total_bytes = 0
    collected_audio = bytearray()

    output_dir = PROJECT_ROOT / "backend" / "temp_audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_filepath = output_dir / f"rime_smoke_test.{file_ext}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            req = client.build_request("POST", endpoint, headers=headers, json=payload)
            response = await client.send(req, stream=True)

            http_status = response.status_code
            print(f"HTTP Status  : {http_status} {response.reason_phrase}")

            if http_status != 200:
                body_bytes = await response.aread()
                await response.aclose()
                err_text = body_bytes.decode("utf-8", errors="replace")
                print(f"[ERROR] Rime API returned HTTP {http_status}: {err_text}")
                print("=" * 70)
                return

            async for chunk in response.aiter_bytes():
                if t_first_chunk is None and len(chunk) > 0:
                    t_first_chunk = time.perf_counter()
                if chunk:
                    chunks_received += 1
                    total_bytes += len(chunk)
                    collected_audio.extend(chunk)

            await response.aclose()

        t_end = time.perf_counter()
        total_duration_ms = (t_end - t_start) * 1000.0
        ttfb_ms = ((t_first_chunk - t_start) * 1000.0) if t_first_chunk is not None else 0.0

        # Save audio payload to file
        output_filepath.write_bytes(collected_audio)

        print("-" * 70)
        print("SMOKE TEST RESULT: SUCCESS")
        print(f"Time to First Audio Chunk (TTFB) : {ttfb_ms:.2f} ms")
        print(f"Total Stream Duration            : {total_duration_ms:.2f} ms")
        print(f"Total Audio Chunks Received      : {chunks_received}")
        print(f"Total Streamed Audio Bytes       : {total_bytes} bytes ({total_bytes / 1024:.2f} KB)")
        print(f"Saved Audio File                 : {output_filepath}")
        print(f"Format Playability               : 16-bit LE {sample_rate}Hz {audio_format.upper()} audio stream")
        print("=" * 70)

    except httpx.TimeoutException:
        print("[ERROR] Connection to Rime TTS endpoint timed out (15s timeout).")
    except httpx.ConnectError as e:
        print(f"[ERROR] Failed to connect to Rime endpoint '{endpoint}': {e}")
    except Exception as e:
        print(f"[ERROR] Unexpected error during Rime smoke test: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(run_smoke_test())

