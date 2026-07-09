from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DeviceOption:
    label: str
    device_name: str
    device_index: int
    sample_rate: int
    channels: int
    hostapi_name: str
    is_loopback: bool
