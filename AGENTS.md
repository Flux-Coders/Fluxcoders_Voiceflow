# VoiceFlow Engineering Rules

## Core Rule

VoiceFlow is an interruption-safe realtime voice agent.

## Non-negotiable requirements

1. Rime is the primary TTS provider.
2. Every user request must have a unique request ID.
3. Every user request must have a conversation version.
4. Obsolete requests must never produce active TTS output.
5. Every asynchronous tool result must validate its request/version
   before modifying conversation state.
6. Late results from obsolete requests must be discarded.
7. Interruption must stop active audio promptly.
8. The application must remain responsive while tools are running.
9. Never hardcode performance metrics.
10. Every realtime feature must have a failure test.
11. Do not add features unrelated to interruption/recovery until the
    core acceptance tests pass.
12. Do not replace Rime with another TTS provider in the primary flow.
13. Preserve existing working functionality when modifying the project.
14. Prefer small, testable modules over large files.
15. Document architectural decisions.