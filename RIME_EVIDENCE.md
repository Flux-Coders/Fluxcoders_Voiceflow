# VoiceFlow — Rime Evidence

## Hard Voice Claim

VoiceFlow prevents obsolete speech and obsolete tool results from leaking into the active conversation when a user interrupts or changes a request.

---

## Acceptance Test

### User Interaction Scenario
1. **Initial Prompt**:
   > *"Find me a train from Nagpur to Mumbai tomorrow."*

2. **User Interruption (during background tool execution)**:
   > *"Actually, only after 8 PM in 3A."*

### Expected System Behavior
- Current obsolete Rime playback is stopped immediately.
- Previous request (v1) is marked `OBSOLETE` / `INVALIDATED`.
- New request version (v2) becomes active and takes ownership of state.
- In-flight background work for v1 is cooperatively cancelled or reconciled.
- Late v1 tool result is rejected by the `RequestVersionGate` upon arrival.
- Only the latest v2 response is synthesized and spoken by Rime.

---

## Manual Verification

### TEST A — Normal Request
- **Utterance**: *"Find me a train from Nagpur to Mumbai tomorrow."*
- **Observed Behavior**:
  - Sentence is fully spoken before processing starts.
  - VAD gates turn endpointing; 0 premature executions.
  - State progression: `LISTENING` -> `THINKING` -> `TOOL_RUNNING` -> `SPEAKING`.
  - Astra is clearly audible with continuous, non-overlapping PCM playback.
- **Result**: **PASS**

### TEST B — Genuine In-Flight Interruption
- **Utterance 1**: *"Find me a train from Nagpur to Mumbai tomorrow."*
- **Utterance 2 (during `TOOL_RUNNING`)**: *"Actually, only after 8 PM in 3A."*
- **Observed Behavior**:
  - `isMeaningfulBargeInTranscript("actually")` triggered instant local audio cut (< 1 ms).
  - Exactly one `CLIENT_INTERRUPT` event dispatched to backend.
  - Version v1 became `OBSOLETE`; v2 initialized on utterance closure.
  - Late v1 tool completion intercepted and discarded by `RequestVersionGate`.
  - Only v2 response was spoken by Astra.
- **Result**: **PASS**

### TEST C — Silence Immunity during Playback
- **Utterance**: Normal request executed; user remained completely silent while Astra was speaking.
- **Observed Behavior**:
  - Acoustic echo / room tone qualified without false barge-in.
  - 0 false `CLIENT_INTERRUPT` events.
  - 0 `aborted_on_interrupt` transitions.
  - Playback streamed and completed cleanly to `IDLE`.
- **Result**: **PASS**

---

## Automated Verification

| Verification Suite | Target & Scope | Result |
| :--- | :--- | :---: |
| **Pytest Test Suite** | 108 unit & integration tests covering monotonic versioning, cancellation tokens, stale-result gates, and PCM queue sequencing | **108 passed, 2 warnings** |
| **Voice Interruption Smoke Test** | 6 deterministic checks verifying Rime config, multi-segment coalescing, barge-in during tool execution, noise immunity, and late-result drop | **6/6 passed** |
| **Browser E2E Test (Chrome CDP)** | 8 full-pipeline browser checks with WebAudio PCM timeline verification and screenshot artifacts | **8/8 checks passed** |
| **Frontend Production Build** | Full TypeScript compilation (`tsc`) and Vite asset bundling | **Passed (0 errors)** |
| **Git Diff Quality Check** | Formatting, trailing whitespace, and syntax validation | **Clean** |

---

## Rime Configuration

VoiceFlow integrates Rime Labs as its primary TTS engine with the following verified configuration:

- **Endpoint**: `https://users.rime.ai/v1/rime-tts`
- **Model**: `mistv3`
- **Speaker**: `astra`
- **Language**: `eng`
- **Audio Format**: `pcm` (raw 16-bit linear PCM)
- **Sample Rate**: `16000` Hz (mono)
- **Transport**: Direct HTTP streaming over chunked transfer to WebSocket

---

## Reproducibility

To reproduce all verification results and run VoiceFlow locally:

### 1. Start Backend Server
```bash
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8000
```

### 2. Start Frontend Dev Server
```bash
cd frontend
npm install
npm run dev
```

### 3. Run Pytest Suite (108 Tests)
```bash
.\backend\.venv\Scripts\pytest tests -q
```

### 4. Run Voice Interruption Smoke Test
```bash
.\backend\.venv\Scripts\python backend/scripts/voice_interruption_smoke_test.py
```

### 5. Run Automated Browser E2E Test
```bash
.\backend\.venv\Scripts\python backend/scripts/browser_e2e_test.py
```

---

## Limitations

1. **Browser Speech Recognition Dependency**: VoiceFlow utilizes the standard browser `SpeechRecognition` / `webkitSpeechRecognition` interface. Testing and daily usage are targeted for Chromium-based browsers (Google Chrome, Microsoft Edge).
2. **Acoustic Feedback & Environmental VAD**: On laptop systems with open speakers and built-in microphones lacking hardware echo cancellation, loud speaker playback may bleed into the microphone. Using **earphones or headphones** is recommended during live voice sessions.

---

## Configuration Hygiene

- **Server-Side API Keys**: All authentication keys (`RIME_API_KEY`, `OPENAI_API_KEY`) are loaded exclusively server-side from local `.env` files and are never passed to the browser or bundled in frontend assets.
- **Git Protection**: `.env` and `.env.*` are strictly excluded in `.gitignore`.
- **Safe Template**: `.env.example` provides placeholders only, ensuring zero secret leakage in source control.
