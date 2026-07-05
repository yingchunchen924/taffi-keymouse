from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


Event = Dict[str, Any]


@dataclass
class Macro:
    id: int
    name: str
    description: str
    tags: str
    favorite: bool
    folder_id: int
    created_at: str
    updated_at: str
    screen_width: int
    screen_height: int
    event_count: int


@dataclass
class MacroFolder:
    id: int
    name: str
    created_at: str
    updated_at: str


@dataclass
class ReferenceImage:
    id: int
    macro_id: int
    name: str
    file_path: str
    created_at: str
    width: int
    height: int


@dataclass
class RunRecord:
    id: int
    macro_id: int
    started_at: str
    ended_at: Optional[str]
    status: str
    loops: int
    message: str


@dataclass
class MacroPayload:
    macro: Macro
    events: List[Event]
