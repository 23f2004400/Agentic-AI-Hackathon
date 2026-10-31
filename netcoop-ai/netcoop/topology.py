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


class TopologyError(ValueError):
    pass


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # By convention `a` is the upstream/parent end and `b` is the downstream/child
    # end -- used by the directed queries (downstream_of, common_upstream,
    # is_wireless) below. The undirected queries (neighbors, medium_between,
    # common_neighbor) ignore this convention and treat every link as symmetric.
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

    # ---- directed hierarchy queries (parent = link.a, child = link.b) -----

    def _require_known(self, device: str) -> None:
        if device not in self.known_devices():
            raise TopologyError(f"unknown device {device!r}")

    def parent_of(self, device: str) -> str | None:
        self._require_known(device)
        for link in self.links:
            if link.b == device:
                return link.a
        return None

    def downstream_of(self, node: str) -> set[str]:
        """All devices reachable downstream (direct and transitive children) of `node`."""
        self._require_known(node)
        direct = {link.b for link in self.links if link.a == node}
        result = set(direct)
        for child in direct:
            result |= self.downstream_of(child)
        return result

    def ancestors_of(self, device: str) -> list[str]:
        """Upstream chain from `device` to the root, nearest first."""
        self._require_known(device)
        chain: list[str] = []
        seen: set[str] = set()
        current = device
        while True:
            parent = self.parent_of(current)
            if parent is None or parent in seen:
                break
            chain.append(parent)
            seen.add(parent)
            current = parent
        return chain

    def common_upstream(self, devices: list[str]) -> str | None:
        """Nearest ancestor shared by every device in `devices`, or None if there isn't one."""
        for d in devices:
            self._require_known(d)
        if not devices:
            return None
        ancestor_sets = [set(self.ancestors_of(d)) for d in devices]
        common = ancestor_sets[0]
        for s in ancestor_sets[1:]:
            common &= s
        if not common:
            return None
        for node in self.ancestors_of(devices[0]):  # nearest-first order
            if node in common:
                return node
        return None

    def is_wireless(self, device: str) -> bool:
        """True if `device`'s upstream link is wireless (e.g. an access point client)."""
        self._require_known(device)
        return any(link.b == device and link.medium == "wireless" for link in self.links)


def load_topology(path: str | Path) -> Topology:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    topo = Topology(**data)
    declared = set(topo.devices)
    if declared:
        referenced = {link.a for link in topo.links} | {link.b for link in topo.links}
        unknown = referenced - declared
        if unknown:
            raise TopologyError(f"topology.json references undeclared devices: {sorted(unknown)}")
    return topo
