"""VoiceFlow Realistic Mock Train-Search Tool.

Requirements:
1. Typed search_trains tool.
2. Inputs: source, destination, date, optional time_constraint, optional class_constraint.
3. Default artificial latency: 3000 ms.
4. Asynchronous execution.
5. Strict version association and cancellation handling.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TrainItem(BaseModel):
    """A train service matching search criteria."""
    train_no: str
    name: str
    source: str
    destination: str
    departure: str  # Format: "HH:MM" (24h)
    arrival: str    # Format: "HH:MM" (24h)
    duration: str
    classes: List[str]
    availability: Dict[str, str] = Field(default_factory=dict)
    fares: Dict[str, float] = Field(default_factory=dict)


class TrainSearchParams(BaseModel):
    """Input parameters for search_trains."""
    source: str = Field(..., description="Origin city or station (e.g. 'Nagpur')")
    destination: str = Field(..., description="Destination city or station (e.g. 'Mumbai')")
    date: str = Field(default="tomorrow", description="Date of travel (e.g. 'tomorrow', '2026-09-05')")
    time_constraint: Optional[str] = Field(
        default=None, 
        description="Optional time filter (e.g. 'after 8 PM', 'after 20:00', 'morning', 'evening')"
    )
    class_constraint: Optional[str] = Field(
        default=None, 
        description="Optional travel class filter (e.g. '3A', '2A', '1A', 'SL')"
    )
    delay_ms: int = Field(default=3000, description="Configurable artificial latency in milliseconds (default: 3000)")


class TrainSearchResult(BaseModel):
    """Result of train search query."""
    source: str
    destination: str
    date: str
    total_found: int
    trains: List[TrainItem]
    applied_time_filter: Optional[str] = None
    applied_class_filter: Optional[str] = None


TRAIN_SEARCH_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_trains",
        "description": "Search available train services between origin and destination with optional filters.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Origin city or station (e.g. 'Nagpur')",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination city or station (e.g. 'Mumbai')",
                },
                "date": {
                    "type": "string",
                    "description": "Date of travel (e.g. 'tomorrow', '2026-09-05')",
                },
                "time_constraint": {
                    "type": "string",
                    "description": "Optional time filter (e.g. 'after 8 PM', 'after 20:00', 'morning', 'evening')",
                },
                "class_constraint": {
                    "type": "string",
                    "description": "Optional travel class filter (e.g. '3A', '2A', '1A', 'SL')",
                },
                "delay_ms": {
                    "type": "integer",
                    "description": "Artificial execution delay in milliseconds (default: 3000)",
                },
            },
            "required": ["source", "destination"],
        },
    },
}


# Comprehensive realistic mock dataset
TRAIN_DATABASE: List[TrainItem] = [
    # Nagpur to Mumbai (Central Railway Corridor)
    TrainItem(
        train_no="12290",
        name="CSMT Duronto Express",
        source="Nagpur",
        destination="Mumbai",
        departure="06:40",
        arrival="19:40",
        duration="13h 00m",
        classes=["1A", "2A", "3A"],
        availability={"1A": "AVL 6", "2A": "AVL 18", "3A": "AVL 42"},
        fares={"1A": 3200.0, "2A": 2100.0, "3A": 1450.0},
    ),
    TrainItem(
        train_no="12810",
        name="Howrah Mumbai CSMT Mail",
        source="Nagpur",
        destination="Mumbai",
        departure="14:00",
        arrival="04:25",
        duration="14h 25m",
        classes=["1A", "2A", "3A", "SL"],
        availability={"1A": "WL 2", "2A": "AVL 8", "3A": "AVL 24", "SL": "AVL 110"},
        fares={"1A": 2900.0, "2A": 1850.0, "3A": 1280.0, "SL": 490.0},
    ),
    TrainItem(
        train_no="12106",
        name="Vidarbha Express",
        source="Nagpur",
        destination="Mumbai",
        departure="17:00",
        arrival="07:00",
        duration="14h 00m",
        classes=["1A", "2A", "3A", "SL"],
        availability={"1A": "AVL 4", "2A": "AVL 12", "3A": "WL 5", "SL": "AVL 32"},
        fares={"1A": 3050.0, "2A": 1950.0, "3A": 1350.0, "SL": 510.0},
    ),
    TrainItem(
        train_no="12140",
        name="Sewagram Superfast Express",
        source="Nagpur",
        destination="Mumbai",
        departure="21:15",
        arrival="12:00",
        duration="14h 45m",
        classes=["2A", "3A", "SL"],
        availability={"2A": "AVL 14", "3A": "AVL 38", "SL": "AVL 95"},
        fares={"2A": 1780.0, "3A": 1220.0, "SL": 460.0},
    ),
    TrainItem(
        train_no="12860",
        name="Gitanjali Express",
        source="Nagpur",
        destination="Mumbai",
        departure="23:30",
        arrival="14:15",
        duration="14h 45m",
        classes=["2A", "3A", "SL"],
        availability={"2A": "WL 4", "3A": "AVL 16", "SL": "AVL 74"},
        fares={"2A": 1820.0, "3A": 1250.0, "SL": 475.0},
    ),
    # Mumbai to Nagpur
    TrainItem(
        train_no="12289",
        name="Nagpur Duronto Express",
        source="Mumbai",
        destination="Nagpur",
        departure="20:15",
        arrival="07:20",
        duration="11h 05m",
        classes=["1A", "2A", "3A"],
        availability={"1A": "AVL 8", "2A": "AVL 22", "3A": "AVL 55"},
        fares={"1A": 3200.0, "2A": 2100.0, "3A": 1450.0},
    ),
    TrainItem(
        train_no="12105",
        name="Vidarbha Express (Return)",
        source="Mumbai",
        destination="Nagpur",
        departure="19:05",
        arrival="08:55",
        duration="13h 50m",
        classes=["1A", "2A", "3A", "SL"],
        availability={"1A": "AVL 5", "2A": "AVL 15", "3A": "AVL 40", "SL": "AVL 80"},
        fares={"1A": 3050.0, "2A": 1950.0, "3A": 1350.0, "SL": 510.0},
    ),
    # Delhi to Mumbai
    TrainItem(
        train_no="12952",
        name="Mumbai Rajdhani Express",
        source="Delhi",
        destination="Mumbai",
        departure="16:55",
        arrival="08:35",
        duration="15h 40m",
        classes=["1A", "2A", "3A"],
        availability={"1A": "AVL 4", "2A": "AVL 20", "3A": "AVL 60"},
        fares={"1A": 4800.0, "2A": 3400.0, "3A": 2400.0},
    ),
    TrainItem(
        train_no="12954",
        name="August Kranti Tejas Rajdhani",
        source="Delhi",
        destination="Mumbai",
        departure="17:15",
        arrival="10:05",
        duration="16h 50m",
        classes=["1A", "2A", "3A"],
        availability={"1A": "AVL 2", "2A": "AVL 14", "3A": "AVL 45"},
        fares={"1A": 4700.0, "2A": 3350.0, "3A": 2350.0},
    ),
]


def parse_time_filter(time_str: Optional[str]) -> Optional[str]:
    """Parses natural language or standard time constraint to 24h 'HH:MM' lower bound."""
    if not time_str:
        return None

    cleaned = time_str.lower().strip()

    # Match "after 8 PM", "after 8pm", "after 8:00 pm"
    match_pm = re.search(r'(?:after\s*)?(\d{1,2})(?::(\d{2}))?\s*pm', cleaned)
    if match_pm:
        hour = int(match_pm.group(1))
        minute = int(match_pm.group(2) or 0)
        if hour < 12:
            hour += 12
        return f"{hour:02d}:{minute:02d}"

    # Match "after 8 AM", "after 8am"
    match_am = re.search(r'(?:after\s*)?(\d{1,2})(?::(\d{2}))?\s*am', cleaned)
    if match_am:
        hour = int(match_am.group(1))
        minute = int(match_am.group(2) or 0)
        if hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    # Match "after 20:00" or "20:00"
    match_24 = re.search(r'(?:after\s*)?(\d{2}):(\d{2})', cleaned)
    if match_24:
        return f"{match_24.group(1)}:{match_24.group(2)}"

    # Named periods
    if "evening" in cleaned or "night" in cleaned:
        return "18:00"
    if "afternoon" in cleaned:
        return "12:00"
    if "morning" in cleaned:
        return "06:00"

    return None


def search_trains_sync(params: TrainSearchParams) -> TrainSearchResult:
    """Synchronous core filter for train database."""
    src = params.source.lower().strip()
    dst = params.destination.lower().strip()

    # Route filtering
    matched = [
        t for t in TRAIN_DATABASE
        if src in t.source.lower() and dst in t.destination.lower()
    ]

    # Time filter
    min_time = parse_time_filter(params.time_constraint)
    if min_time:
        matched = [t for t in matched if t.departure >= min_time]

    # Class filter
    if params.class_constraint:
        cls_req = params.class_constraint.upper().strip()
        matched = [t for t in matched if cls_req in t.classes]

    return TrainSearchResult(
        source=params.source,
        destination=params.destination,
        date=params.date,
        total_found=len(matched),
        trains=matched,
        applied_time_filter=min_time,
        applied_class_filter=params.class_constraint,
    )


async def search_trains(params: TrainSearchParams) -> TrainSearchResult:
    """Asynchronous entry point with configurable latency."""
    delay_sec = max(0.0, params.delay_ms / 1000.0)
    if delay_sec > 0:
        await asyncio.sleep(delay_sec)
    return search_trains_sync(params)

