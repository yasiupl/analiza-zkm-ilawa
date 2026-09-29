"""HTML report of a zkmplan run: the template gets the result as JSON."""
from __future__ import annotations

import json
import math
from pathlib import Path


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else round(x, 3)
    if hasattr(x, "item"):
        return _clean(x.item())
    return x


def build(result: dict, template: Path, out: Path) -> Path:
    data = _clean(result)
    for r in data.get("day_types", {}).values():       # the details of duties are only needed in the table
        for v in r.get("variants", {}).values():
            for k in ("breaks_before", "breaks_after"):
                v[k]["list"] = v[k].get("list", [])[:200]
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    html = template.read_text(encoding="utf-8").replace("/*__DATA__*/null", js)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
