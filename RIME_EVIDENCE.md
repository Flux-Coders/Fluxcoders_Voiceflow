# VoiceFlow — Rime Evidence

## Hard Voice Claim

VoiceFlow addresses interruption safety in a realtime voice interaction.

When the user changes their request while the agent is speaking or while a tool is running, the system must:

1. Stop obsolete Rime playback.
2. Invalidate the previous request.
3. Create a new active request version.
4. Prevent late results from the obsolete request from affecting the active conversation.
5. Speak only the response corresponding to the latest user request.

The system uses Rime as the primary spoken-output provider.

---

## Acceptance Test

### Initial request

"Find me a train from Nagpur to Mumbai tomorrow."

The system begins processing request version v1.

### Deliberate stress

A delayed train-search operation keeps v1 active long enough for an interruption.

### User interruption

"Actually, only after 8 PM in 3A."

The interruption creates a new request version v2.

### Expected behavior

- Current obsolete Rime playback is stopped/cut.
- v1 becomes obsolete.
- v2 becomes the active request.
- Background work associated with v1 is cancelled or reconciled.
- A late result from v1 is rejected by the request-version gate.
- Only the v2 response is sent to Rime for speech.

---

## Manual Verification

### Test A — Normal Voice Request

Input:

"Find me a train from Nagpur to Mumbai tomorrow."

Observed:

- No premature execution before sentence completion.
- No false CLIENT_INTERRUPT.
- State progressed through LISTENING → THINKING → TOOL_RUNNING → SPEAKING.
- Rime/Astra audio was clearly audible and played continuously.

Result: PASS

### Test B — Genuine In-Flight Barge-In

Initial request:

"Find me a train from Nagpur to Mumbai tomorrow."

Interruption during tool execution:

"Actually, only after 8 PM in 3A."

Observed:

- Local audio cut was triggered by the meaningful interruption.
- Exactly one CLIENT_INTERRUPT was generated.
- v1 became obsolete.
- v2 became active.
- The late v1 result was discarded by the request-version gate.
- Only the v2 response was spoken.

Result: PASS

### Test C — Silence Immunity During Speaking

After completing a request, the user remained silent while Astra was speaking.

Observed:

- No false interruption.
- No aborted_on_interrupt.
- Playback completed normally.

Result: PASS
108 passed, 2 warnings
---

## Automated Verification

### Pytest

Command:

```powershell
.\backend\.venv\Scripts\pytest tests -q
