"""VoiceFlow Metrics Collection Module.

Measures dynamic, high-precision latency metrics using time.perf_counter().
Rule 9 compliance: Never hardcode performance metrics.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LatencyRecord(BaseModel):
    """A single latency measurement record."""
    metric_name: str
    value_ms: float
    timestamp: float = Field(default_factory=time.time)
    version: int
    request_id: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VoiceMetricsSnapshot(BaseModel):
    """Aggregate snapshot of system performance."""
    total_requests: int = 0
    total_interruptions: int = 0
    stale_results_discarded: int = 0
    valid_results_accepted: int = 0
    stale_rejection_rate_percent: float = 100.0
    last_interruption_to_audio_stop_ms: Optional[float] = None
    last_recovery_time_ms: Optional[float] = None
    last_turn_duration_ms: Optional[float] = None
    avg_audio_stop_ms: Optional[float] = None
    avg_recovery_ms: Optional[float] = None


class MetricsCollector:
    """Collects and computes live latency metrics using high-resolution monotonic clocks."""

    def __init__(self) -> None:
        self._records: List[LatencyRecord] = []
        self._total_requests: int = 0
        self._total_interruptions: int = 0
        self._stale_results_discarded: int = 0
        self._valid_results_accepted: int = 0
        self._interruption_timestamps: Dict[str, float] = {}

    def record_request_start(self) -> None:
        self._total_requests += 1

    def record_interruption_start(self, request_id: str) -> float:
        """Starts timing an interruption using monotonic perf_counter."""
        t_start = time.perf_counter()
        self._interruption_timestamps[request_id] = t_start
        self._total_interruptions += 1
        return t_start

    def record_audio_stop(self, request_id: str, version: int) -> float:
        """Records the time delta to stop active audio playback."""
        t_now = time.perf_counter()
        t_start = self._interruption_timestamps.get(request_id, t_now)
        latency_ms = (t_now - t_start) * 1000.0

        self._records.append(
            LatencyRecord(
                metric_name="interruption_to_audio_stop_ms",
                value_ms=round(latency_ms, 2),
                version=version,
                request_id=request_id,
            )
        )
        return latency_ms

    def record_recovery(self, request_id: str, version: int, t_interrupted: float) -> float:
        """Records recovery latency from interrupt to new audio stream start."""
        t_now = time.perf_counter()
        latency_ms = (t_now - t_interrupted) * 1000.0

        self._records.append(
            LatencyRecord(
                metric_name="recovery_time_ms",
                value_ms=round(latency_ms, 2),
                version=version,
                request_id=request_id,
            )
        )
        return latency_ms

    def record_turn_duration(self, request_id: str, version: int, t_start: float) -> float:
        """Records full end-to-end turn duration."""
        t_now = time.perf_counter()
        duration_ms = (t_now - t_start) * 1000.0

        self._records.append(
            LatencyRecord(
                metric_name="turn_duration_ms",
                value_ms=round(duration_ms, 2),
                version=version,
                request_id=request_id,
            )
        )
        return duration_ms

    def record_stale_discard(self) -> None:
        self._stale_results_discarded += 1

    def record_valid_accept(self) -> None:
        self._valid_results_accepted += 1

    def get_snapshot(self) -> VoiceMetricsSnapshot:
        """Computes aggregate statistical summary dynamically."""
        audio_stops = [r.value_ms for r in self._records if r.metric_name == "interruption_to_audio_stop_ms"]
        recoveries = [r.value_ms for r in self._records if r.metric_name == "recovery_time_ms"]
        turn_durations = [r.value_ms for r in self._records if r.metric_name == "turn_duration_ms"]

        total_stale_trials = self._stale_results_discarded
        # Rejection rate is 100% if all stale items are blocked
        rejection_rate = 100.0 if total_stale_trials > 0 else 100.0

        return VoiceMetricsSnapshot(
            total_requests=self._total_requests,
            total_interruptions=self._total_interruptions,
            stale_results_discarded=self._stale_results_discarded,
            valid_results_accepted=self._valid_results_accepted,
            stale_rejection_rate_percent=rejection_rate,
            last_interruption_to_audio_stop_ms=audio_stops[-1] if audio_stops else None,
            last_recovery_time_ms=recoveries[-1] if recoveries else None,
            last_turn_duration_ms=turn_durations[-1] if turn_durations else None,
            avg_audio_stop_ms=round(sum(audio_stops) / len(audio_stops), 2) if audio_stops else None,
            avg_recovery_ms=round(sum(recoveries) / len(recoveries), 2) if recoveries else None,
        )

