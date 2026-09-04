# VoiceFlow Acceptance Tests

## Test 1 — Normal Request

Input:
"Find me a train from Nagpur to Mumbai tomorrow."

Expected:
The system returns a valid train-search response.

---

## Test 2 — Interrupt During Speech

Start:
Agent is speaking.

User:
"Wait."

Expected:
Current Rime playback stops promptly.

---

## Test 3 — Change Request During Tool Execution

Initial:
"Find trains from Nagpur to Mumbai."

Updated:
"Only trains after 8 PM."

Expected:
The original task becomes obsolete and the new request becomes active.

---

## Test 4 — Stale Result Protection

Artificially delay request #1.

User creates request #2 before request #1 finishes.

Expected:
When request #1 finally finishes, its result must not be spoken
or applied to the active conversation.

---

## Test 5 — Multiple Interruptions

User rapidly changes constraints.

Expected:
Only the latest valid conversation state produces the final response.

---

## Test 6 — Final Consistency

The spoken response must correspond to the latest user request,
not the original request.