# VoiceFlow

## Goal

Build a realtime voice agent that safely handles user interruptions
and changed instructions during speech and tool execution.

## Target User

A user performing multi-step travel searches through voice.

## Demo Scenario

Train search from Nagpur to Mumbai.

Example:

User:
"Find me a train from Nagpur to Mumbai tomorrow."

Then while the system is processing:

"Actually, only trains after 8 PM."

## Core Voice Problem

Interruption and recovery.

## Core Claim

When a user changes a request while the agent is speaking or while
a tool is running, VoiceFlow must:

1. stop obsolete speech,
2. invalidate obsolete work,
3. accept the new instruction,
4. preserve conversation state,
5. reject stale tool results,
6. generate a response based only on the latest instruction.

## Primary TTS

Rime.

## Realtime Transport

LiveKit.

## Initial STT

Streaming speech-to-text.

## Reasoning

LLM with tool calling.

## Backend

Python / FastAPI.

## State

Redis or an equivalent session-state mechanism.

## Initial Tool

Mock train-search tool with configurable delay.

## Success Metrics

- interruption-to-audio-stop latency
- recovery time
- stale-result rejection rate
- recovery success rate
- final-response correctness