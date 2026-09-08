# VoiceFlow Submission

## Project

VoiceFlow — Interruption-Safe Realtime Voice Agent

## Repository

This package contains the complete source repository used for the demonstration.

## Demo

See:

`demo/VoiceFlow_Demo.mp4`

## Main Demonstration

The demo shows:

- normal voice interaction
- tool execution
- user interruption during in-flight processing
- request version invalidation
- stale result rejection
- corrected Rime speech

## Primary Voice Provider

Rime

Model: `mistv3`  
Speaker: `astra`  
Language: `eng`  
Format: `pcm`  
Sample rate: `16000 Hz`  
Endpoint: `https://users.rime.ai/v1/rime-tts`

## Verified Build

Commit:

`1b17352`

Automated verification:

- 108 pytest tests passed
- 6/6 voice interruption smoke tests passed
- 8/8 browser E2E checks passed
- production build passed
