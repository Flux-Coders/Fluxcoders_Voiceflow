# VoiceFlow System Architecture & Technical Design

## 1. System Overview & Architectural Topology

VoiceFlow is an interruption-safe realtime voice agent designed for high-concurrency, low-latency conversational tasks involving multi-step reasoning and asynchronous tool execution.

### High-Level Topology

```
+-----------------------------------------------------------------------------+
|                                CLIENT TIER                                  |
|  - WebRTC Audio Stream (Mic In / Speaker Out)                               |
|  - Local VAD / Instant Mute Gate                                            |
|  - Control DataChannel (Interruption signals, Request IDs, UI State)        |
+--------------------------------------▲--------------------------------------+
                                       │ WebRTC (Audio Tracks + DataChannels)
                                       │ / WebSocket Fallback
+--------------------------------------▼--------------------------------------+
|                           REALTIME TRANSPORT TIER                           |
|  LiveKit SFU / WebRTC Room Server                                           |
|  - Bidirectional Opus Audio Streaming                                       |
|  - Low-latency Datachannel Signaling (<20ms)                                |
+--------------------------------------▲--------------------------------------+
                                       │ LiveKit Agent Protocol / Async Stream
+--------------------------------------▼--------------------------------------+
|                            BACKEND CORE (FastAPI)                           |
|                                                                             |
|  +-----------------------------------------------------------------------+  |
|  |                         Session Controller                             |  |
|  |  - Active Request State: (active_request_id, active_version)          |  |
|  |  - Task Registry & Cancellation Token Dispatcher                      |  |
|  |  - Ephemeral Session State Machine                                    |  |
|  +-----------------▲-------------------------▲------------------------▲--+  |
|                    │                         │                        │     |
|  +-----------------▼---------+  +------------▼-----------+  +---------▼--+  |
|  |      Streaming STT        |  |    LLM Orchestrator    |  | Rime TTS   |  |
|  | - Interim / Final text    |  | - Tool Calling Engine  |  | Pipeline   |  |
|  | - Speech Start Detector   |  | - Streaming Token Gen  |  | - Streaming|  |
|  +---------------------------+  +------------▲-----------+  |   Audio    |  |
|                                              │              +------------+  |
|                                 +------------▼-----------+                  |
|                                 | Async Tool Worker Pool |                  |
|                                 | - Mock Train Search    |                  |
|                                 | - Version-Gated Return |                  |
|                                 +------------------------+                  |
+-----------------------------------------------------------------------------+
```

---

## 2. Frontend Architecture

### Core Responsibilities
1. **Audio Capture & Render**: Stream microphone audio to backend via LiveKit WebRTC track; render incoming synthesized audio buffer to user speakers.
2. **Fast-Path Interruption (Instant Audio Kill)**: On detecting user vocalization (via client VAD or speech start event), immediately mute/flush local WebAudio buffer before roundtrip confirmation.
3. **Session & Latency Instrumentation**: Track local timestamps for user speech start, interruption trigger, audio stop, and final response delivery.
4. **State Observability**: Display real-time UI state (`idle`, `listening`, `thinking`, `tool_running`, `speaking`, `interrupted`) with active request ID and version counter.

### Component Structure
- `AudioEngine`: WebAudio API pipeline (`AudioContext`, `AudioWorkletNode`, gain nodes for hard-cut muting, buffer flushing).
- `LiveKitBridge`: Manages room connection, audio track publication/subscription, and reliable control data packets.
- `InteractionController`: Coordinates UI state, tracks request IDs, dispatches client-initiated interrupt events.
- `StatusDisplay`: Reactive dashboard showing conversation history, active version, running tools, and measured latency metrics.

---

## 3. Backend Architecture

The backend is built with Python using `asyncio` and `FastAPI` (integrating with the LiveKit Agents framework).

#### Key Subsystems & Modules

```
backend/
├── app/
│   ├── core/
│   │   ├── session.py          # Session & SessionManager: atomic version, request lifecycle & turn orchestration
│   │   ├── state.py            # ConversationStateManager: versioned state, history & SlotPatch application
│   │   ├── cancellation.py     # CancellationToken & TaskRegistry
│   │   ├── tool_executor.py    # Asynchronous ToolExecutor with step cancellation checks & version gates
│   │   ├── versioning.py       # RequestVersionGate: validation rules & version mismatch errors
│   │   ├── rime_gate.py        # RimeTTSGate: strict version gate before speech synthesis
│   │   ├── event_logger.py     # Structured domain telemetry logging
│   │   └── metrics.py          # Dynamic time.perf_counter() latency measurements
│   ├── engine/
│   │   ├── llm_orchestrator.py # LLMOrchestrator: Step 1 (intent/slot extraction), Tool dispatch & Step 2 (synthesis)
│   │   └── llm_provider.py     # BaseLLMClient (abstract) & MockLLMClient (deterministic slot patching & synthesis)
│   ├── tools/
│   │   ├── registry.py         # ToolRegistry: strict tool whitelist, permission check & Pydantic arg validation
│   │   └── train_search.py     # Mock train search tool with configurable delay & OpenAPI schema
│   └── api/
│       └── routes.py           # Health, session lifecycle & telemetry endpoints
```

### Module Responsibilities
- **`Session` & `ConversationStateManager`**:
  - Maintains `(active_request_id, active_version, conversation_history, slots)`.
  - Atomic monotonic version increments on new user turns.
  - Applies `SlotPatch` updates (adding, replacing, clearing constraints) strictly tied to `active_version`.
- **`LLMOrchestrator`**:
  - Step 1: Extracts intent/slots from user utterance, emits structured tool call if required slots exist, or prompts clarification.
  - Intercepts cancellations before and after Step 1.
  - Validates tool calls against `ToolRegistry` permissions and Pydantic argument models.
  - Dispatches tool via `Session.run_tool()`.
  - Step 2: Synthesizes natural language response from `TrainSearchResult`.
  - Commits turn via atomic version gate `Session.complete_turn()`.
- **`BaseLLMClient` & `MockLLMClient`**:
  - Provider-agnostic abstraction for LLM inference.
  - Completely isolated from audio hardware and TTS; cannot mutate state directly.
  - Deterministically extracts slots, handles constraint replacements (e.g. "Only AC" -> "Sleeper is fine") and constraint clearing (e.g. "Only after 8 PM" -> "Any time is fine").
- **`ToolRegistry`**:
  - Enforces registration, permissions, and typed parameter validation for all callable tools.
- **`ToolExecutor` & `RequestVersionGate`**:
  - Executes tool functions asynchronously with 50ms cooperative cancellation checks; discards late results if version changed.
- **`RimeTTSGate` & `MetricsCollector`**:
  - Ensures only valid, active requests reach TTS. Measures latency dynamically via `time.perf_counter()`.

---

## 4. Realtime Event Flow

### Scenario A: Normal Execution Flow
- STT detects final utterance ("Find me a train from Nagpur to Mumbai tomorrow").
- `SessionManager` initializes `v1` with `req-001`.
- `LLMRunner` receives prompt and emits `ToolCall(name="train_search", args={"origin": "Nagpur", "destination": "Mumbai", "date": "tomorrow"})`.
- `ToolExecutionWorker` runs search asynchronously with simulated delay.
- `ToolExecutionWorker` verifies `(v1, req-001)` still valid; returns result list.
- `LLMRunner` synthesizes natural language response.
- `RimeClient` streams audio chunks to `AudioDispatcher`.
- `AudioDispatcher` renders audio frames onto the LiveKit audio track.

---

### Scenario B: Interruption During Speech Playback (Test 2)
- Agent is currently streaming Rime audio for `(v1, req-001)`.
- User speaks: "Wait".
- Fast-path: Client mutes local audio playback immediately (<10ms).
- VAD / STT fires `SpeechStartedEvent`.
- `SessionManager` triggers hard cancellation on `(v1, req-001)`:
  - Aborts active Rime TTS streaming connection.
  - Flushes WebRTC audio queue.
  - Cancels active LLM generation coroutine.
- `SessionManager` increments version to `v2` and allocates `req-002`.
- STT delivers transcript ("Wait").
- LLM synthesizes brief acknowledgment ("I'm listening.") for `v2`.
- Rime plays audio for `v2`.

---

### Scenario C: Change Request During Tool Execution (Test 3 & 4)
- User asks: "Find trains from Nagpur to Mumbai" (`v1`, `req-001`).
- LLM triggers `train_search("Nagpur", "Mumbai")` on `ToolExecutionWorker` (e.g. 3.0s simulated latency).
- At $t=1.0\text{s}$, user interrupts: "Actually, only trains after 8 PM".
- `SpeechStartedEvent` immediately increments version to `v2`, allocates `req-002`, and signals cancellation for `v1`.
- LLM is dispatched with updated context for `v2` (combining route "Nagpur to Mumbai" with filter "after 8 PM").
- LLM launches updated `train_search("Nagpur", "Mumbai", min_departure="20:00")` (`v2`, `req-002`).
- At $t=3.0\text{s}$, original Tool for `v1` completes:
  - Gate check: `v1 != active_version (v2)` -> **REJECTED & DISCARDED**.
  - No state change, no LLM feeding, no TTS speech.
- Tool for `v2` completes -> Gate check passes (`v2 == v2`).
- Filtered results delivered to LLM -> Rime synthesizes only trains after 8 PM.

---

## 5. Request Versioning Strategy

### Data Model & Invariants
```python
@dataclass
class RequestContext:
    request_id: str
    version: int
    created_at: float
    is_cancelled: bool = False
```

1. **Monotonic Progression**: Every new user utterance detected by STT increments `session.active_version` by `+1` and assigns a fresh UUID `active_request_id`.
2. **Context Propagation**: Every spawned sub-task (`LLM generation`, `Tool execution`, `Rime streaming`, `Audio dispatch`) receives a cloned, immutable `RequestContext(request_id, version)`.
3. **Atomic Gate Validation**:
   ```python
   def is_valid(ctx: RequestContext, session: SessionState) -> bool:
       return (not ctx.is_cancelled) and \
              (ctx.version == session.active_version) and \
              (ctx.request_id == session.active_request_id)
   ```

---

## 6. Interruption & Cancellation Strategy

### Tier 1: Fast-Path Audio Suppression (< 50ms)
- **Client-Side**: Immediate gain-node cutoff and audio buffer drain on VAD trip.
- **Transport-Side**: LiveKit DataChannel emits an immediate `OP_INTERRUPT` packet.
- **Backend Audio Dispatcher**: Drops all pending PCM/Opus chunks in the queue for the active track; sends silence frames if needed to reset WebRTC jitter buffers.

### Tier 2: Asynchronous Task Invalidation & Cancellation
- **Task Registry**: The `SessionManager` holds references to running `asyncio.Task` objects tagged with `(request_id, version)`:
  - `llm_task`
  - `tool_task`
  - `tts_task`
- **Cancellation Cascade**:
  1. Set `ctx.is_cancelled = True`.
  2. Call `task.cancel()` on `llm_task` and `tts_task`.
  3. For `tool_task`: Coroutine cancelled or outcome blocked by version gate check upon return.
  4. Terminate HTTP/WebSocket streaming request to Rime TTS.

---

## 7. Stale-Result Protection Strategy

| Race Condition | Manifestation | Architectural Mitigation |
| :--- | :--- | :--- |
| **1. Tool Finish vs New Speech** | Tool #1 completes 5ms after user begins Request #2. | `ToolExecutionWorker` checks `ctx.version == session.active_version` immediately before delivering payload to queue. If mismatched, log `StaleToolResultDiscarded` and drop silently. |
| **2. LLM Stream vs Interruption** | LLM finishes generating sentence for Request #1 while user speaks "Stop". | `LLMRunner` checks cancellation token at every `yield token`. If cancelled/obsolete, generator terminates immediately and partial output is excised from history. |
| **3. Rime Chunks In-Flight** | Network delivers audio packets for Request #1 after version bumped to v2. | Audio packets are wrapped in metadata `AudioFrame(version=v, request_id=id, data=bytes)`. Audio Dispatcher rejects any frame where `frame.version != active_version`. |
| **4. Rapid Utterances** | User says: "Find trains" (v1) -> "Wait" (v2) -> "Nagpur to Mumbai" (v3) within 1s. | Monotonic version increments to v3. Tasks for v1 and v2 are cancelled. Only results carrying `v3` can reach TTS and history. |
| **5. Context Poisoning** | Stale tool result overwrites conversation context. | State update operations (`append_message`, `set_slots`) are restricted exclusively to `SessionStateManager.apply_turn()`, which validates `(version, request_id)` atomically. |

---

## 8. Rime Integration Boundary & Three-Level Stale Protection

In accordance with **Rule 1** and **Rule 12** ("Rime is the primary TTS provider. Do not replace Rime with another TTS provider in the primary flow"):

### Production Endpoint & Model Configuration
- **API Endpoint**: `POST https://users.rime.ai/v1/rime-tts` (configurable via `RIME_ENDPOINT`).
- **Production Model**: `mistv3` (configurable via `RIME_MODEL`, supports `mistv3`, `coda`, `mistv2`).
- **Production Voice / Speaker**: `astra` (configurable via `RIME_SPEAKER`, supports `astra`, `celeste`, `marsh`, `amber`, etc.).
- **Language Code**: `eng` (configurable via `RIME_LANGUAGE`).
- **Audio Output Format**: `audio/pcm` (16-bit LE PCM at 16,000 Hz, configurable via `RIME_AUDIO_FORMAT` and `RIME_SAMPLE_RATE`).
- **Transport**: Asynchronous HTTP chunked streaming using `httpx.AsyncClient` with `Transfer-Encoding: chunked`.
- **Authentication**: `Authorization: Bearer <RIME_API_KEY>` loaded securely from environment (never hardcoded, logged, or exposed).

### Three-Level Stale Protection Architecture

```
[Rime TTS HTTP Stream (POST /v1/rime-tts)]
                   │
                   ▼ (Raw audio chunks)
┌─────────────────────────────────────────────────────────────┐
│ 1. Level 1: Rime Stream Gate (stream_synthesize)            │
│    - Pre-flight check: (version == active_version)          │
│    - In-stream check: on every raw chunk received           │
│    - On cancel/stale: Immediately sever HTTP connection     │
│      (response.aclose()), drop chunk & emit telemetry       │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼ (StreamedAudioChunk carrying request_id & version)
┌─────────────────────────────────────────────────────────────┐
│ 2. Level 2: Audio Dispatcher Buffer Gate                    │
│    - filter_buffered_chunks(): Inspects queued frames       │
│    - If frame.version != active_version: Purge immediately  │
│    - Log STALE_AUDIO_DISCARDED & StaleResultRecord          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼ (Validated frames)
┌─────────────────────────────────────────────────────────────┐
│ 3. Level 3: Final Playback Gate (can_play_chunk)            │
│    - Evaluated immediately before writing frame to speaker   │
│      or WebRTC Opus track                                   │
│    - If interrupted / version mismatch: Block playback      │
│    - Emit AUDIO_OUTPUT_STOPPED                              │
└─────────────────────────────────────────────────────────────┘
```

### Telemetry & Monotonic Instrumentation
All timing metrics use monotonic clock deltas (`time.perf_counter()`):
- `RIME_STREAM_STARTED`: Logs start time (`t_start`).
- `RIME_FIRST_AUDIO_CHUNK`: Measures first-chunk audio latency `(t_first - t_start) * 1000`.
- `RIME_CHUNK_RECEIVED`: Telemetry on every received chunk index and byte payload.
- `RIME_STREAM_CANCELLED`: Logs cancellation timestamp (`t_cancel`) and chunks streamed before cut.
- `RIME_STREAM_COMPLETED`: Total streaming duration `(t_complete - t_start) * 1000`.
- `AUDIO_OUTPUT_STOPPED` & `STALE_AUDIO_DISCARDED`: Explicit stale drop audit log.

---

## 9. LLM Provider Architecture & Tool Calling Safety

### Provider Abstraction & OpenAI Compatibility
- **`BaseLLMClient`**: Abstract interface decoupled from state management, tool execution, and audio hardware.
- **`MockLLMClient`**: Deterministic offline mock implementation supporting rule-based slot extraction, slot patching, and response synthesis.
- **`OpenAILLMClient`**: Production OpenAI-compatible chat completions provider invoking `POST /chat/completions` via asynchronous `httpx.AsyncClient`.
  - Configurable via `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_TEMPERATURE`, `OPENAI_TIMEOUT_SECONDS`.
  - Secure credential handling: API keys are never logged, printed, or exposed in error messages.
  - Supports standard OpenAI function calling tool schemas (`{"type": "function", "function": {...}}`).

### Strict Tool Cardinality Enforcement
The `LLMOrchestrator` deterministically evaluates returned tool calls with explicit cardinality rules:
1. **0 tool calls**: Direct conversational response without tool execution (`Branch A`).
2. **Exactly 1 tool call**: Authorized against `ToolRegistry.is_permitted()` and validated via Pydantic model (`TrainSearchParams`), then executed asynchronously (`Branch C`).
3. **>1 tool calls**: Explicitly rejected as unsupported. Logs `TOOL_UNKNOWN_OR_FORBIDDEN` warning event, delivers user clarification ("Multiple simultaneous tool calls are not supported. Please make one request at a time."), and terminates turn safely without executing any tools (`Branch B`).

### Typed Exception Hierarchy
All provider-level errors are wrapped in typed domain exceptions:
- `LLMError`: Base exception for all LLM errors.
- `LLMConfigError`: Missing or invalid configuration (e.g. missing API key).
- `LLMAuthenticationError`: HTTP 401/403 authentication failures.
- `LLMBadRequestError`: HTTP 400 bad request.
- `LLMRateLimitError`: HTTP 429 rate limit exceeded.
- `LLMServerError`: HTTP 5xx upstream server errors.
- `LLMTimeoutError`: Network request timeout.
- `LLMConnectionError`: Network connection failure.
- `LLMCancellationError`: Turn cancellation due to user interruption.

---

## 10. Testing Architecture

In compliance with **Rule 9** ("Never hardcode performance metrics") and **Rule 10** ("Every realtime feature must have a failure test"):

### Automated Test Matrix
- **Unit Tests**:
  - `test_session_state.py`: Monotonic version increments, state immutability, request context generation.
  - `test_cancellation.py`: Task registry lifecycle, token propagation, cascade aborts.
  - `test_tool_gate.py`: Stale payload rejection, valid payload dispatch.
- **Integration Tests (Acceptance Tests 1-6)**:
  - `test_test1_normal_flow.py`: Complete standard query-tool-response loop.
  - `test_test2_speech_interrupt.py`: Speech detection during active TTS stream verifying prompt playback halt.
  - `test_test3_tool_interrupt.py`: User constraint modification during tool execution.
  - `test_test4_stale_result.py`: Artificial delay on Request #1; verification that delayed tool result is discarded and never spoken.
  - `test_test5_rapid_interrupt.py`: Multiple rapid interruptions stress test.
  - `test_test6_consistency.py`: Final state & spoken response consistency check.
- **Failure Tests**:
  - `test_stt_failure.py`: Sudden STT disconnect/malformed packet handling.
  - `test_rime_failure.py`: Rime API 5xx/timeout fallback and graceful error reporting.
  - `test_tool_timeout.py`: Asynchronous tool execution exceeding deadline.
- **Latency Instrumentation**: Monotonic clock delta measurement (`time.perf_counter()`) for interruption-to-audio-stop latency and recovery time.

---

## 11. Recommended Directory Structure

```
Fluxcoders_VoiceFlow/
├── .env.example
├── AGENTS.md
├── README.md
├── docs/
│   ├── ACCEPTANCE_TEST.md
│   ├── ARCHITECTURE.md
│   └── PROJECT_SPEC.md
├── backend/
│   ├── requirements.txt
│   ├── pyproject.toml
│   └── app/
│       ├── __init__.py
│       ├── main.py
│       ├── core/
│       │   ├── config.py
│       │   ├── session.py
│       │   ├── cancellation.py
│       │   └── events.py
│       ├── engine/
│       │   ├── orchestrator.py
│       │   ├── stt_handler.py
│       │   ├── llm_runner.py
│       │   └── audio_dispatcher.py
│       ├── tts/
│       │   ├── base.py
│       │   └── rime_client.py
│       ├── tools/
│       │   ├── base.py
│       │   └── train_search.py
│       └── api/
│           ├── routes.py
│           └── livekit_agent.py
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── audio/
│       │   ├── AudioEngine.ts
│       │   └── VADWorker.ts
│       ├── livekit/
│       │   └── LiveKitClient.ts
│       ├── state/
│       │   ├── sessionStore.ts
│       │   └── types.ts
│       └── components/
│           ├── StatusIndicator.tsx
│           ├── ConversationView.tsx
│           └── MetricsPanel.tsx
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_session_state.py
    │   ├── test_cancellation.py
    │   └── test_tool_gate.py
    ├── integration/
    │   ├── test_test1_normal_flow.py
    │   ├── test_test2_speech_interrupt.py
    │   ├── test_test3_tool_interrupt.py
    │   ├── test_test4_stale_result.py
    │   ├── test_test5_rapid_interrupt.py
    │   └── test_test6_consistency.py
    ├── failure/
    │   ├── test_stt_failure.py
    │   ├── test_rime_failure.py
    │   └── test_tool_timeout.py
    └── mocks/
        ├── mock_stt.py
        ├── mock_llm.py
        ├── mock_rime.py
        └── mock_tools.py
```
