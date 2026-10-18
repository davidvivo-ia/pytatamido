"""Grabación y reproducción de combates.

La simulación es determinista (mismo seed + mismas entradas → mismo combate), así que una
repetición es sólo el seed, los luchadores y una máscara de botones por frame.
"""
from __future__ import annotations

import json
import os
import time
from typing import List, Optional

REPLAY_DIR = "replays"


def save_replay(match, mode: str) -> Optional[str]:
    if match.replay is not None or not match.input_log:
        return None
    os.makedirs(REPLAY_DIR, exist_ok=True)
    w = match.winner.fd["id"] if match.winner is not None else None
    data = dict(version=1, seed=match.seed, mode=mode, controls=match.controls,
                p1=match.f[0].fd["id"], p2=match.f[1].fd["id"], winner=w,
                score=[match.f[0].wins, match.f[1].wins], frames=len(match.input_log),
                inputs=_rle(match.input_log), date=time.strftime("%Y-%m-%d %H:%M"))
    name = os.path.join(REPLAY_DIR, f"{time.strftime('%Y%m%d_%H%M%S')}_{data['p1']}_vs_{data['p2']}.json")
    with open(name, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return name


def load_replay(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    d["inputs"] = _unrle(d["inputs"])
    return d


def list_replays() -> List[str]:
    if not os.path.isdir(REPLAY_DIR):
        return []
    return sorted((os.path.join(REPLAY_DIR, f) for f in os.listdir(REPLAY_DIR) if f.endswith(".json")), reverse=True)


def _rle(log):
    out = []
    prev = None
    n = 0
    for m1, m2 in log:
        if (m1, m2) == prev:
            n += 1
        else:
            if prev is not None:
                out.append([prev[0], prev[1], n])
            prev, n = (m1, m2), 1
    if prev is not None:
        out.append([prev[0], prev[1], n])
    return out


def _unrle(rle):
    out = []
    for m1, m2, n in rle:
        out.extend([(m1, m2)] * n)
    return out
