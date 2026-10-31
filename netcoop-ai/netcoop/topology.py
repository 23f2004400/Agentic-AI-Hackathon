"""Declarative network topology: devices, links, and simple adjacency queries.

Topology is optional input. Agents that don't have one must not assume full
connectivity between devices; they report scope limited to the reporting
device instead.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

Medium = Literal["wired", "wireless"]


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a: str
    b: str
    medium: Medium = "wired"


class Topology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    devices: list[str] = []
    links: list[Link] = []

    def known_devices(self) -> set[str]:
        ids = set(self.devices)
        for link in self.links:
            ids.add(link.a)
            ids.add(link.b)
        return ids

    def neighbors(self, device: str) -> set[str]:
        out: set[str] = set()
        for link in self.links:
            if link.a == device:
                out.add(link.b)
            elif link.b == device:
                out.add(link.a)
        return out

    def medium_between(self, device_a: str, device_b: str) -> Medium | None:
        for link in self.links:
            if {link.a, link.b} == {device_a, device_b}:
                return link.medium
        return None

    def is_wireless_only(self, device: str) -> bool:
        links = [link for link in self.links if device in (link.a, link.b)]
        return bool(links) and all(link.medium == "wireless" for link in links)

    def common_neighbor(self, devices: list[str]) -> str | None:
        """A single device adjacent to every device in `devices` (candidate shared upstream node)."""
        if not devices:
            return None
        common = self.neighbors(devices[0])
        for d in devices[1:]:
            common &= self.neighbors(d)
        common -= set(devices)
        return next(iter(common)) if len(common) == 1 else None


def load_topology(path: str | Path) -> Topology:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Topology(**data)
