# VoiceFlow

VoiceFlow is an interruption-safe realtime voice agent designed for multi-step reasoning and travel search.

---

## Key Features

1. **Interruption-Safe Orchestration**: Sub-50ms audio cut and task cancellation when user speaks during speech playback or tool execution.
2. **Deterministic Request Versioning**: Monotonic `conversation_version` paired with UUID `request_id` prevents obsolete requests from producing active speech or modifying state.
3. **Three-Level Stale Audio Protection**:
   - **Level 1 (Stream Gate)**: Aborts in-flight HTTP connections immediately upon cancellation.
   - **Level 2 (Buffer Gate)**: Purges queued audio chunks if conversation version advances.
   - **Level 3 (Playback Gate)**: Blocks obsolete audio frames right before speaker/WebRTC delivery.
4. **Primary TTS Provider**: Real Rime Labs text-to-speech HTTP streaming integration with typed `StreamedAudioChunk` metadata.
5. **Strict Tool Registry**: Whitelists and authorizes function calls with typed Pydantic validation (`search_trains`).
6. **Multi-Turn Slot Patch Semantics**: Supports adding, replacing ("Only AC" $\rightarrow$ "Sleeper is fine"), and clearing constraints ("Only after 8 PM" $\rightarrow$ "Any time is fine").

---

## Rime TTS Configuration

VoiceFlow connects to the official Rime streaming HTTP endpoint:
`POST https://users.rime.ai/v1/rime-tts`

### Configuration Variables (`.env`)

| Variable | Description | Default | Example Values |
| :--- | :--- | :--- | :--- |
| `RIME_API_KEY` | Secret Rime API Key | *(required in production)* | `your_rime_api_key_here` |
| `RIME_ENDPOINT` | Direct HTTP Streaming URL | `https://users.rime.ai/v1/rime-tts` | `https://users.rime.ai/v1/rime-tts` |
| `RIME_MODEL` | Production Rime Model | `mistv3` | `mistv3`, `coda`, `mistv2` |
| `RIME_SPEAKER` | Live Voice / Speaker ID | `astra` | `astra`, `celeste`, `marsh`, `amber` |
| `RIME_LANGUAGE` | Exact language code | `eng` | `eng`, `spa` |
| `RIME_AUDIO_FORMAT` | Audio payload format | `pcm` | `pcm`, `mp3`, `wav` |
| `RIME_SAMPLE_RATE` | Sample rate in Hz | `16000` | `16000`, `22050`, `24000` |

---

## Running the Test Suite

VoiceFlow has a comprehensive test suite with 100% deterministic offline mocks (no live API key required for testing):

```bash
# Activate virtual environment
cd backend
.\.venv\Scripts\Activate.ps1

# Run complete pytest test suite
pytest tests/ -v
```

All 59 unit and failure tests will execute in < 4 seconds.

---

## Running the Application

### Backend (FastAPI)
```bash
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

### Frontend (React + Vite + TypeScript)
```bash
cd frontend
npm install
npm run dev
```
Navigate to `http://localhost:5173` to interact with the VoiceFlow developer dashboard.

