#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan.py -- 布局坐标计划生成器

把"板框 + 功能块 + 器件尺寸"变成"每颗器件摆在哪"，输出可直接驱动 EDA 的坐标表。
设计原则：确定性、可复现、可审阅。不追求最优，追求"有据可依、能落笔、能复查"。

用法：
  python plan.py assets/plan.example.json -o my_plan.json
  python plan.py my_plan_in.json --emit-steps steps.json --emit-sh --out sh
  python plan.py my_plan_in.json --report           # 只打印块占用与密度

坐标约定：输出单位 mil，y 向上（与嘉立创EDA PCB 数据模型一致），原点在板框左下角。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

MIL_PER_MM = 39.37007874015748


def mm2mil(v: float) -> float:
    return v * MIL_PER_MM


def snap(v: float, grid: float) -> float:
    return round(v / grid) * grid


class Rect:
    __slots__ = ("x0", "y0", "x1", "y1", "name")

    def __init__(self, x0, y0, x1, y1, name=""):
        self.x0, self.y0, self.x1, self.y1, self.name = x0, y0, x1, y1, name

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.y1 - self.y0

    @property
    def area(self):
        return self.w * self.h

    def inflate(self, m: float) -> "Rect":
        return Rect(self.x0 - m, self.y0 - m, self.x1 + m, self.y1 + m, self.name)

    def intersects(self, o: "Rect") -> bool:
        return not (self.x1 <= o.x0 or o.x1 <= self.x0 or self.y1 <= o.y0 or o.y1 <= self.y0)

    def contains(self, o: "Rect") -> bool:
        return self.x0 <= o.x0 and self.x1 >= o.x1 and self.y0 <= o.y0 and self.y1 >= o.y1

    def to_list(self):
        return [round(self.x0, 1), round(self.y0, 1), round(self.x1, 1), round(self.y1, 1)]


def part_dims(p: dict, grid: float) -> tuple[float, float]:
    w = mm2mil(float(p["w_mm"]))
    h = mm2mil(float(p["h_mm"]))
    rot = int(p.get("rotation", 0)) % 180
    if rot == 90:
        w, h = h, w
    return snap(w, grid), snap(h, grid)


def apply_heights(parts: list[dict], heights: dict, stats: dict) -> None:
    """把实测高度覆盖到器件上，并在器件上留 height_source 记号。

    两趟布局的第二趟：第一趟用 spec 里的估算 h_mm 排完，从 EDA 回读符号真实高度，
    用 --heights 喂回来重排。EDA 符号高度常与封装名义尺寸不符（尤其是带屏蔽罩、
    带散热片、带排针座的），只靠估算会在密集区叠件，而这种叠件 check.py 只能事后报。
    """
    for p in parts:
        rec = heights.get(p["designator"])
        val, src = None, "measured"
        if isinstance(rec, dict):
            val = rec.get("h_mm", rec.get("height_mm"))
            src = rec.get("source") or "measured"
        elif rec is not None:
            val = rec
        try:
            h = float(val)
        except (TypeError, ValueError):
            h = None
        if h is None or h <= 0:
            stats["estimated"] += 1
            stats["missing"].append(p["designator"])
            p["_h_src"] = "estimated"
        else:
            p["h_mm"] = h
            p["_h_src"] = src
            stats["measured"] += 1


def shelf_pack(parts: list[dict], x0: float, y0: float, width: float, gap: float, grid: float):
    """把一组器件按"高→低、大→小"排成货架，返回 (placements, used_height)。"""
    ordered = sorted(parts, key=lambda p: (-part_dims(p, grid)[1], -part_dims(p, grid)[0], p["designator"]))
    placements = []
    cx, cy, row_h = x0, y0, 0.0
    for p in ordered:
        w, h = part_dims(p, grid)
        if cx + w > x0 + width + 1e-6 and cx > x0:
            cy += row_h + gap
            cx = x0
            row_h = 0.0
        placements.append({
            "designator": p["designator"],
            "x": snap(cx + w / 2, grid),
            "y": snap(cy + h / 2, grid),
            "w": w,
            "h": h,
            "rotation": int(p.get("rotation", 0)) % 360,
            "layer": int(p.get("layer", 1)),
            "block": p.get("_block", ""),
            "fixed": bool(p.get("fixed", False)),
            "height_source": p.get("_h_src", "estimated"),
        })
        cx += w + gap
        row_h = max(row_h, h)
    used_h = (cy + row_h - y0) if placements else 0.0
    return placements, used_h


def candidate_positions(usable: Rect, step: float, edge: str):
    """按"靠哪条边"的偏好生成候选左上角坐标序列。"""
    xs = []
    x = usable.x0
    while x <= usable.x1 - step + 1e-6:
        xs.append(x)
        x += step
    ys = []
    y = usable.y0
    while y <= usable.y1 - step + 1e-6:
        ys.append(y)
        y += step

    cx, cy = (usable.x0 + usable.x1) / 2, (usable.y0 + usable.y1) / 2
    if edge == "left":
        key = lambda t: (t[0], t[1])
    elif edge == "right":
        key = lambda t: (-t[0], t[1])
    elif edge == "bottom":
        key = lambda t: (t[1], t[0])
    elif edge == "top":
        key = lambda t: (-t[1], t[0])
    else:  # center
        key = lambda t: (math.hypot(t[0] - cx, t[1] - cy), t[1])
    return sorted(((a, b) for b in ys for a in xs), key=key)


def build_plan(spec: dict, heights: dict | None = None) -> dict:
    heights = heights or {}
    hstats = {"measured": 0, "estimated": 0, "missing": []}
    opts = spec.get("options", {})
    grid = float(opts.get("grid_mil", 25))
    edge_band = float(opts.get("edge_band_mil", 80))
    gutter = float(opts.get("gutter_mil", 60))
    part_gap = float(opts.get("part_gap_mil", 40))
    fill = float(opts.get("area_fill_factor", 1.8))
    scan_step = float(opts.get("scan_step_mil", 100))

    board = spec["board"]
    bw, bh = mm2mil(float(board["width_mm"])), mm2mil(float(board["height_mm"]))
    usable = Rect(edge_band, edge_band, bw - edge_band, bh - edge_band, "usable")
    if usable.w <= 0 or usable.h <= 0:
        raise SystemExit("板框太小：可用区域为负，请检查 width_mm/height_mm 或 edge_band_mil")

    placements: list[dict] = []
    obstacles: list[Rect] = []
    block_report = []
    clamped = []

    # 1) 锚点：结构件、连接器、安装孔，位置由人给，直接锁死并当障碍物
    for a in spec.get("anchors", []):
        if heights.get(a["designator"]) is not None:
            apply_heights([a], heights, hstats)
        w = snap(mm2mil(float(a["w_mm"])), grid)
        h = snap(mm2mil(float(a["h_mm"])), grid)
        if int(a.get("rotation", 0)) % 180 == 90:
            w, h = h, w
        cx, cy = snap(mm2mil(float(a["x_mm"])), grid), snap(mm2mil(float(a["y_mm"])), grid)
        # 25mil 网格对齐可能把锚点推出板框，这里夹回框内（只动吸附造成的溢出，不改原始坐标意图）
        cx2 = min(max(cx, w / 2), bw - w / 2)
        cy2 = min(max(cy, h / 2), bh - h / 2)
        if (cx2, cy2) != (cx, cy):
            clamped.append(a["designator"])
        cx, cy = round(cx2, 1), round(cy2, 1)
        placements.append({
            "designator": a["designator"], "x": cx, "y": cy,
            "w": w, "h": h, "rotation": int(a.get("rotation", 0)) % 360,
            "layer": int(a.get("layer", 1)), "block": a.get("block", "anchor"),
            "fixed": True, "height_source": a.get("_h_src", "given"),
        })
        obstacles.append(Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, a["designator"]).inflate(gutter / 2))

    # 2) 估算每个功能块的尺寸（用同一套货架算法在目标宽度下试排）
    aspect = usable.w / usable.h if usable.h else 1.4
    blocks = []
    for b in spec.get("blocks", []):
        parts = [dict(p, _block=b["name"]) for p in b["parts"]]
        apply_heights(parts, heights, hstats)
        area = sum(part_dims(p, grid)[0] * part_dims(p, grid)[1] for p in parts) * fill
        widest = max([part_dims(p, grid)[0] for p in parts] + [0])
        inner_w = max(snap(math.sqrt(area * aspect), grid), widest)
        inner_w = min(inner_w, usable.w - part_gap)
        _, used_h = shelf_pack(parts, 0, 0, inner_w, part_gap, grid)
        blocks.append({
            "name": b["name"], "parts": parts,
            "rect": Rect(0, 0, inner_w + part_gap, max(used_h + part_gap, part_gap * 2), b["name"]),
            "edge": b.get("edge", "center"),
            "priority": int(b.get("priority", 5)),
            "gutter": float(b.get("gutter_mil", gutter)),
        })

    # 3) 先按优先级（小=先），再按面积（大=先）落块
    blocks.sort(key=lambda b: (b["priority"], -b["rect"].area))

    for blk in blocks:
        r = blk["rect"]
        placed = False
        for (x, y) in candidate_positions(usable, scan_step, blk["edge"]):
            cand = Rect(x, y, x + r.w, y + r.h, blk["name"])
            if not usable.contains(cand):
                continue
            if any(cand.inflate(blk["gutter"]).intersects(o) for o in obstacles):
                continue
            blk["rect"] = cand
            obstacles.append(cand.inflate(blk["gutter"] / 2))
            placed = True
            break
        if not placed:
            # 放不下就把块缩到全板可用区并标记，让人来裁决
            blk["rect"] = Rect(usable.x0, usable.y0, usable.x1, usable.y1, blk["name"])
            blk["overflow"] = True

        sub, used_h = shelf_pack(blk["parts"], blk["rect"].x0 + part_gap / 2,
                                 blk["rect"].y0 + part_gap / 2,
                                 blk["rect"].w - part_gap, part_gap, grid)
        placements.extend(sub)
        block_report.append({
            "block": blk["name"], "priority": blk["priority"], "edge": blk["edge"],
            "rect_mil": blk["rect"].to_list(), "parts": len(sub),
            "used_h_mil": round(used_h, 1),
            "overflow": bool(blk.get("overflow", False)),
        })

    # 4) 去重（同一器件只保留第一次），并按网格收尾
    seen = {}
    for p in placements:
        seen.setdefault(p["designator"], p)
    final = sorted(seen.values(), key=lambda p: (p["block"], p["designator"]))

    total_area = sum(p["w"] * p["h"] for p in final)
    out_of_board = []
    for p in final:
        if (p["x"] - p["w"] / 2 < -1e-6 or p["y"] - p["h"] / 2 < -1e-6
                or p["x"] + p["w"] / 2 > bw + 1e-6 or p["y"] + p["h"] / 2 > bh + 1e-6):
            out_of_board.append(p["designator"])

    return {
        "meta": {
            "unit": "mil",
            "y_axis": "up",
            "origin": "板框左下角",
            "board_mil": [round(bw, 1), round(bh, 1)],
            "usable_mil": usable.to_list(),
            "grid_mil": grid,
            "edge_band_mil": edge_band,
            "gutter_mil": gutter,
            "generator": "jlc-pcb-layout/scripts/plan.py",
            "note": "本文件是坐标的唯一真源。落笔后如手工微调，请回写此文件，避免下次重排把改动冲掉。",
        },
        "blocks": block_report,
        "components": final,
        "summary": {
            "count": len(final),
            "footprint_area_mil2": round(total_area, 0),
            "board_area_mil2": round(bw * bh, 0),
            "area_utilization_pct": round(total_area / (bw * bh) * 100, 1),
            "overflow_blocks": [b["block"] for b in block_report if b["overflow"]],
            "out_of_board": out_of_board,
            "anchors_clamped": clamped,
            "height_source": {
                "measured": hstats["measured"],
                "estimated": hstats["estimated"],
                "estimated_designators": sorted(set(hstats["missing"])),
                "note": "estimated 表示用的是 spec 里的估算 h_mm。两趟布局：第一趟出计划 → "
                        "从 EDA 回读符号真实高度 → plan.py --heights 重排第二趟。"
                        "仍为 estimated 的器件，密集区叠件风险没有排除。",
            },
        },
    }


def emit_steps(plan: dict, project: str | None) -> dict:
    steps = []
    if project:
        steps.append({"run": f"doc switch --project {project}", "note": "先切到目标 PCB 文档"})
    for c in plan["components"]:
        if c["fixed"]:
            continue
        steps.append({
            "action": "pcb.component.modify",
            "payload": {
                "designator": c["designator"],
                "x": c["x"],
                "y": c["y"],
                "rotation": c["rotation"],
                "layer": c["layer"],
            },
        })
    return {
        "meta": {
            "project": project or "",
            "note": "easyeda apply 之前先 --dry-run。payload 键名以 `easyeda actions` 输出的 schema 为准；"
                    "若 connector 要求 primitiveId 而非 designator，先跑一步 pcb.components.list 并 capture。",
            "source_plan": "由 plan.py 生成，坐标单位 mil / y-up",
        },
        "steps": steps,
    }


def emit_sh(plan: dict) -> str:
    lines = ["#!/usr/bin/env bash",
             "# 由 plan.py 生成；参数名请用 `easyeda actions` 核对后再执行",
             "set -euo pipefail", ""]
    for c in plan["components"]:
        if c["fixed"]:
            continue
        lines.append(
            f"easyeda pcb component modify --designator {c['designator']} "
            f"--x {c['x']:.0f} --y {c['y']:.0f} --rotation {c['rotation']} --layer {c['layer']}")
    lines.append("")
    lines.append("easyeda pcb components list --include-bbox   # 回读校验")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="布局坐标计划生成器")
    ap.add_argument("spec", help="输入规格 JSON")
    ap.add_argument("-o", "--out", default="plan.json", help="输出计划 JSON")
    ap.add_argument("--emit-steps", metavar="FILE", help="另外输出 easyeda apply playbook")
    ap.add_argument("--emit-sh", action="store_true", help="另外输出 shell 命令脚本")
    ap.add_argument("--project", default=None, help="目标工程名（写进 playbook）")
    ap.add_argument("--heights", metavar="FILE",
                    help="第二趟：实测高度 JSON {位号: h_mm} 或 {位号: {h_mm, source}}")
    ap.add_argument("--emit-heights", metavar="FILE",
                    help="输出待回读高度的清单（第一趟跑完、去 EDA 回读前用）")
    ap.add_argument("--report", action="store_true", help="只打印块占用报告")
    args = ap.parse_args(argv)

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    heights = {}
    if args.heights:
        heights = json.loads(Path(args.heights).read_text(encoding="utf-8"))
        if not isinstance(heights, dict):
            raise SystemExit(f"{args.heights} 应是 {{位号: 高度}} 或 {{位号: {{h_mm, source}}}} 的对象")
    plan = build_plan(spec, heights)

    if args.emit_heights:
        items = [{
            "designator": c["designator"],
            "w_mil": c["w"],
            "h_mil_estimate": c["h"],
            "height_source": c.get("height_source", "estimated"),
            "action": "从 EDA 回读符号实际高度，填进 h_mm 后作为 --heights 输入",
        } for c in plan["components"]]
        Path(args.emit_heights).write_text(json.dumps({
            "_note": "第一趟布局后的待回读清单。回读方式：对每个器件取 EDA 符号 bbox 高度，"
                     "单位换成 mm 后填 h_mm。全部回读完毕再用 plan.py --heights 重排。",
            "items": items,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"待回读高度清单已写入 {args.emit_heights}（{len(items)} 颗）")

    if args.report:
        for b in plan["blocks"]:
            flag = "  ⚠ 溢出" if b["overflow"] else ""
            print(f"{b['block']:<12} edge={b['edge']:<6} parts={b['parts']:<3} "
                  f"rect={b['rect_mil']}{flag}")
        s = plan["summary"]
        print(f"\n器件数 {s['count']}，占板面积 {s['area_utilization_pct']}%，"
              f"溢出块 {s['overflow_blocks'] or '无'}")
        hs = s["height_source"]
        if hs["estimated"]:
            print(f"高度：实测 {hs['measured']} / 估算 {hs['estimated']}（估算位号 "
                  f"{len(hs['estimated_designators'])} 个）—— 叠件风险未排除")
            print("  → 回读符号真实高度后跑第二趟：plan.py <spec> --heights heights.json")
        else:
            print(f"高度：{hs['measured']} 颗全部为实测")
        return 0

    Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"计划已写入 {args.out}（{plan['summary']['count']} 颗器件，"
          f"占板 {plan['summary']['area_utilization_pct']}%）")

    if plan["summary"]["overflow_blocks"]:
        print(f"⚠ 有块放不下，已合并到全板区域：{plan['summary']['overflow_blocks']}")
        print("  → 缩小块内器件、减层数、加板面积，或调整 blocks[].priority 后重跑")
    if plan["summary"]["out_of_board"]:
        print(f"⚠ 有器件超出板框（多为锚点坐标给错）：{plan['summary']['out_of_board']}")
    hs = plan["summary"]["height_source"]
    if hs["estimated"]:
        print(f"高度：实测 {hs['measured']} / 估算 {hs['estimated']} —— 第二趟未做，叠件风险未排除")
        print("  → plan.py <spec> --emit-heights h.json，回读后 --heights h.json 重排")

    if args.emit_steps:
        Path(args.emit_steps).write_text(
            json.dumps(emit_steps(plan, args.project), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"playbook 已写入 {args.emit_steps}（先 easyeda apply ... --dry-run）")
    if args.emit_sh:
        p = Path(args.out).with_suffix(".sh")
        p.write_text(emit_sh(plan), encoding="utf-8")
        print(f"shell 脚本已写入 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
