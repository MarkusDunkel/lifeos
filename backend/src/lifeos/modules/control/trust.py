"""Can the owner trust an actionable? Did every process before it run, and what did the
process that made it take into account?

- The light of an actionable is the light of the process that makes it; when a process
  further upstream is late or failing, the actionable is at least yellow: it may be based on
  outdated information.
- For each input of the making process: when the input was last updated, and how much new
  material arrived since the process last ran (not yet considered).
"""

from __future__ import annotations

from typing import Any

from lifeos.modules.control.catalog import Catalog, Node

OFF, OK, WARN, FAIL = "off", "ok", "warn", "fail"
RANK = {OFF: 0, OK: 1, WARN: 2, FAIL: 3}


def _worst(levels: list[str]) -> str:
    return max(levels, key=RANK.__getitem__, default=OFF)


def actionable_trust(
    node: Node,
    catalog: Catalog,
    health: dict[str, Any],  # process id → ProcessHealth
    inputs: dict[str, dict[str, Any]],  # "<process>:<node>" → {newSince, considered, total}
    generators: dict[str, dict[str, Any]],  # process id → details of its last run
) -> dict[str, Any]:
    producers = [h for h in health.values() if node.id in h.outputs and h.enabled]
    if not producers:
        return {"level": OFF, "reason": "not set up", "producers": [], "basedOn": [],
                "upstream": []}  # fmt: skip

    # every enabled process upstream of the producers (the foundation does not count)
    upstream: dict[str, Any] = {}
    queue = [i for p in producers for i in p.inputs]
    seen: set[str] = set()
    while queue:
        node_id = queue.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        for h in health.values():
            if node_id in h.outputs and h.enabled and not h.foundation and h.id not in upstream:
                upstream[h.id] = h
                queue += h.inputs

    level = _worst([p.level for p in producers])
    late = [u for u in upstream.values() if u.level in (WARN, FAIL)]
    if level in (WARN, FAIL):
        bad = next(p for p in producers if p.level == level)
        reason = f"{bad.label}: {bad.reason}"
    elif late:
        level = WARN
        reason = "may be based on outdated input: " + ", ".join(
            f"{u.label} ({u.reason})" for u in late
        )
    else:
        reason = "every process before it ran as expected"

    based_on = []
    for p in producers:
        for node_id in p.inputs:
            source = catalog.node(node_id)
            if source is None:
                continue
            writers = [h for h in health.values() if node_id in h.outputs and h.enabled]
            updated = max((w.last_success for w in writers if w.last_success), default=None)
            entry = {
                "id": node_id,
                "label": source.label,
                "kind": source.kind,
                "local": source.local,
                "level": _worst([w.level for w in writers]) if writers else OK,
                "updated": updated,
                "by": p.id,
                "byLabel": p.label,
                "lastRun": p.last_success,
            }
            entry.update(inputs.get(f"{p.id}:{node_id}", {}))
            based_on.append(entry)

    return {
        "level": level,
        "reason": reason,
        "producers": [p.id for p in producers],
        "upstream": sorted(upstream),
        "basedOn": based_on,
        "generators": {p.id: generators[p.id] for p in producers if p.id in generators},
    }
