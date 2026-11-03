# -*- coding: utf-8 -*-
"""seen_store.py — seen_ids.json 的唯一写入实现。

去重是两阶段的：
  1. scout.py 抓取时只把新条目写进 raw_latest.json 的 `new_ids`，不落去重库；
  2. check_report.py 校验通过（= 日报真的写出来了）后才调用本模块提交。

这样翻译 / 渲染 / 校验任一步失败，条目都不会被提前标成「已见」而消失 30 天。
（只想取数据、不产日报时，可用 `scout.py --commit-now` 显式跳过第一阶段。）

文件格式始终是 {"<id>": "YYYY-MM-DD"}；旧版纯 ID 列表会在读写时自动迁移。
"""

import json
from datetime import datetime
from pathlib import Path


def load(path: Path, default_date: str | None = None) -> dict:
    """读取去重库，返回 {id: "YYYY-MM-DD"}。文件不存在/损坏时返回空字典。"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    fallback = default_date or datetime.now().strftime("%Y-%m-%d")
    if isinstance(data, list):            # 旧格式：纯 ID 列表
        return {str(i): fallback for i in data}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    return {}


def save(path: Path, data: dict):
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def commit(path: Path, ids, date_str: str, ttl_days: int = 30) -> tuple[int, int]:
    """把 ids 以 date_str 写入去重库，返回 (新增条数, 库内总数)。幂等。

    顺手剪掉超过 ttl_days 天的旧记录（读的时候 scout.py 也会再过滤一次），
    避免文件无限增长。
    """
    p = Path(path)
    data = load(p, default_date=date_str)
    if ttl_days and ttl_days > 0:
        base = _to_date(date_str) or datetime.now().date()
        data = {k: v for k, v in data.items() if _fresh(v, base, ttl_days)}
    added = 0
    for i in ids:
        i = str(i or "").strip()
        if not i:
            continue
        if i not in data:
            added += 1
        data[i] = date_str
    save(p, data)
    return added, len(data)


def _to_date(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _fresh(value, base, ttl_days: int) -> bool:
    d = _to_date(value)
    if d is None:          # 日期不可解析 → 保留，交给上层判断
        return True
    return (base - d).days <= ttl_days
