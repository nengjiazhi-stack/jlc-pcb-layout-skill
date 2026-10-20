#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules.py -- 嘉立创 PCB Layout 规范计算器

所有数值来自 assets/constraints.json（唯一真源，出处见该文件 _meta.sources）。
用法：
  python rules.py width --current 3 --copper 1oz
  python rules.py clearance --voltage 300 --side primary --input-range 150-300
  python rules.py via --thickness 1.6
  python rules.py layers --pins 320 --area-mm2 2500
  python rules.py spacing --trace 0.2
  python rules.py impedance --type microstrip --w 0.2 --h 0.2 --er 4.3
每个子命令都支持 --json。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

MIL_PER_MM = 39.37007874015748
HERE = Path(__file__).resolve().parent
CONSTRAINT_CANDIDATES = [
    HERE.parent / "assets" / "constraints.json",
    HERE / "constraints.json",
]


def load_constraints() -> dict:
    for p in CONSTRAINT_CANDIDATES:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit("找不到 assets/constraints.json —— 请确认 skill 目录完整")


def mm(x: float) -> str:
    return f"{x:.2f}mm"


def mil(x: float) -> str:
    return f"{x:.1f}mil"


# ---------------------------------------------------------------- width

def ipc2221_width_mil(current_a: float, delta_t: float, copper_oz: float, external: bool = True) -> float:
    """IPC-2221 载流反解线宽（mil）。用于超出规范表范围时的外推。"""
    k = 0.048 if external else 0.024
    area_mil2 = (current_a / (k * (delta_t ** 0.44))) ** (1 / 0.725)
    thickness_mil = copper_oz * 1.378
    return area_mil2 / thickness_mil


def cmd_width(args) -> int:
    c = load_constraints()["current_capacity"]
    copper = 2 if args.copper.lower().startswith("2") else 1
    table = c["current_a_70um"] if copper == 2 else c["current_a_35um_conservative"]
    widths = c["width_mm"]

    need = args.current
    note = []
    if args.pour:
        need = need / c["derate_ratio_pour_as_trace"]
        note.append(f"以铜皮作导线，按注 i 降额 50%：等效需求 {need:.2f}A")

    pick = None
    for i, cap in enumerate(table):
        if cap >= need:
            pick = i
            break

    out = {
        "input": {"current_a": args.current, "copper": f"{copper}oz", "pour_as_trace": bool(args.pour), "delta_t_c": args.delta_t},
        "source": "TY-18/19/21 载流表（1oz 取表内双栏的小值，保守）",
    }
    if pick is None:
        w_ipc = ipc2221_width_mil(need, args.delta_t, copper)
        out["verdict"] = "EXTRAPOLATED"
        out["width_min_mm"] = round(w_ipc / MIL_PER_MM, 3)
        out["width_min_mil"] = round(w_ipc, 1)
        out["note"] = note + [
            f"超出规范表上限（{table[-1]}A），改用 IPC-2221 公式外推，实际须做温升实测或加铜/汇流条"]
    else:
        out["verdict"] = "OK"
        out["width_min_mm"] = widths[pick]
        out["width_min_mil"] = round(widths[pick] * MIL_PER_MM, 1)
        out["recommended_mm"] = widths[pick]
        out["capacity_a"] = table[pick]
        if pick + 1 < len(widths):
            out["next_tier_mm"] = widths[pick + 1]
        ipc_w = ipc2221_width_mil(need, args.delta_t, copper)
        out["ipc2221_mil"] = round(ipc_w, 1)
        out["note"] = note + [
            f"规范表给出 {widths[pick]}mm 可载 {table[pick]}A（Δt={args.delta_t}℃）；"
            f"IPC-2221 外层公式算得 {ipc_w:.0f}mil（{ipc_w / MIL_PER_MM:.2f}mm），两者取大"]
        if ipc_w / MIL_PER_MM > widths[pick]:
            out["recommended_mm"] = round(ipc_w / MIL_PER_MM, 3)
            out["note"].append("IPC 结果更大 → 推荐值采用 IPC 结果")

    print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else _fmt_width(out))
    return 0


def _fmt_width(o: dict) -> str:
    lines = [f"需求：{o['input']['current_a']}A / {o['input']['copper']} / ΔT={o['input']['delta_t_c']}℃"
             + ("（铜皮作导线）" if o["input"]["pour_as_trace"] else "")]
    lines.append(f"结论：{o['verdict']}")
    lines.append(f"最小线宽：{o['width_min_mm']}mm ({o['width_min_mil']}mil)")
    if "recommended_mm" in o:
        lines.append(f"推荐线宽：{o['recommended_mm']}mm")
    if "capacity_a" in o:
        lines.append(f"该档载流：{o['capacity_a']}A")
    if "next_tier_mm" in o:
        lines.append(f"上一档：{o['next_tier_mm']}mm")
    lines.append("说明：")
    for n in o["note"]:
        lines.append(f"  - {n}")
    return "\n".join(lines)


# ---------------------------------------------------------------- clearance

def _bracket(rows: list[dict], v: float, key: str) -> tuple[float, str]:
    """电压档取值：落在两档之间时取更保守（更大）的一档，并说明是外推。"""
    valid = [r for r in rows if r.get(key) is not None]
    if not valid:
        raise SystemExit(f"表内没有 {key} 的有效数据")
    for r in valid:
        if r["voltage"] >= v:
            if r["voltage"] == v:
                return r[key], "精确档位"
            return r[key], f"按 {r['voltage']}V 档取值（{v}V 无独立数值，取上一档保守）"
    return valid[-1][key], f"超出表格上限 {valid[-1]['voltage']}V，取最高档保守值，须另行评审"


def cmd_clearance(args) -> int:
    ec = load_constraints()["electrical_clearance"]
    side = args.side
    rng = args.input_range
    if side == "primary":
        rows = ec["primary_input_150_300v"] if rng.startswith("150") else ec["primary_input_300_600v"]
        label = f"一次侧（输入 {rng}V 档）"
    else:
        rows = ec["secondary"] if rng.startswith("150") else ec["secondary_300_600v_input"]
        label = f"二次侧（输入 {rng}V 档）"

    gap, gnote = _bracket(rows, args.voltage, "gap_mm")
    creep, cnote = _bracket(rows, args.voltage, "creep_mm")
    notes = [f"{label}"]
    for n in (gnote, cnote):
        if n and n not in notes:
            notes.append(n)
    notes.append("爬电距离 > 空气间隙时以爬电距离为准（沿面）")
    out = {
        "input": {"voltage": args.voltage, "side": side, "input_range": rng},
        "air_gap_mm": gap,
        "creepage_mm": creep,
        "air_gap_mil": round(gap * MIL_PER_MM, 1),
        "creepage_mil": round(creep * MIL_PER_MM, 1),
        "note": notes,
        "source": "TY-24~TY-42",
    }
    if args.voltage > 60 and side == "primary":
        out["note"].append("高压区须在丝印加高压警示与隔离带（EM-5.9.2 / 5.9.3）")
    print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else _fmt_simple(out))
    return 0


def _fmt_simple(o: dict) -> str:
    skip = {"input", "note", "source"}
    lines = [f"{k}: {v}" for k, v in o.items() if k not in skip]
    if o.get("note"):
        lines.append("说明：")
        lines += [f"  - {n}" for n in o["note"]]
    if o.get("source"):
        lines.append(f"出处：{o['source']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- via

def cmd_via(args) -> int:
    v = load_constraints()["vias"]
    rows = sorted(v["board_thickness_to_min_drill_mil"], key=lambda r: r["thickness_mm"])
    if args.thickness <= rows[0]["thickness_mm"]:
        min_drill = rows[0]["min_drill_mil"]
        basis = f"板厚 {rows[0]['thickness_mm']}mm 档"
    else:
        min_drill = rows[-1]["min_drill_mil"]
        basis = f"超表格上限，按 {rows[-1]['thickness_mm']}mm 档（{rows[-1]['min_drill_mil']}mil）保守取值"
        for r in rows:
            if r["thickness_mm"] >= args.thickness:
                min_drill = r["min_drill_mil"]
                basis = f"板厚 {r['thickness_mm']}mm 档"
                break

    drill = max(args.drill, min_drill)
    # 匹配优选系列
    series = sorted(v["preferred_series"], key=lambda r: r["drill_mil"])
    cand = [s for s in series if s["drill_mil"] >= drill] or [series[-1]]
    pick = cand[0]
    ratio = args.thickness * MIL_PER_MM / pick["drill_mil"]
    out = {
        "input": {"thickness_mm": args.thickness, "requested_drill_mil": args.drill},
        "min_drill_mil": min_drill,
        "basis": basis,
        "drill_mil": pick["drill_mil"],
        "pad_mil": pick["pad_mil"],
        "thermal_pad_mil": pick["thermal_mil"],
        "annular_ring_mil": round((pick["pad_mil"] - pick["drill_mil"]) / 2, 1),
        "hole_to_board_ratio": round(ratio, 2),
        "ratio_ok": ratio <= 6,
        "note": [],
        "source": "TY-45~TY-53 / EM-5.6.5 / EM-5.6.2",
    }
    if ratio > 6:
        out["note"].append(f"孔厚比 {ratio:.2f} 超过 EM 建议 1:6，嘉立创极限约 1:8，需工艺确认")
    if pick["drill_mil"] != args.drill:
        out["note"].append(f"请求 {args.drill}mil 不在优选系列或小于最小孔径，已上调到 {pick['drill_mil']}mil")
    out["note"].append("BGA 下方过孔用 12mil 孔径，且正反两面须盖阻焊（BELL-16）")
    print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else _fmt_simple(out))
    return 0


# ---------------------------------------------------------------- layers

def cmd_layers(args) -> int:
    ps = load_constraints()["plane_and_stackup"]
    area_in2 = args.area_mm2 / 645.16
    density = area_in2 / (args.pins / 14) if args.pins > 0 else 0.0

    table = sorted(ps["pin_density_to_layers"], key=lambda r: -r["density_min"])
    pick = table[-1]
    for r in table:
        if density >= r["density_min"]:
            pick = r
            break

    total = pick["total_layers"]
    reasons = [f"Pin 密度 = {area_in2:.2f} in² / ({args.pins}/14) = {density:.2f}"]
    trig = ps["multi_layer_trigger"]
    if args.clock_mhz and args.clock_mhz >= trig["clock_mhz"]:
        total = max(total, 4)
        reasons.append(f"时钟 {args.clock_mhz}MHz ≥ {trig['clock_mhz']}MHz → 至少 4 层（TY-63）")
    if args.rise_ns and args.rise_ns <= trig["rise_time_ns"]:
        total = max(total, 4)
        reasons.append(f"上升时间 {args.rise_ns}ns ≤ {trig['rise_time_ns']}ns → 至少 4 层（TY-63）")

    out = {
        "pin_density_in2_per_pin": round(density, 3),
        "table_row": pick,
        "recommended_total_layers": total,
        "recommended_signal_layers": pick["signal_layers"],
        "reasons": reasons,
        "source": "TY-11~TY-17 / TY-63",
    }
    if total > 8:
        out["reasons"].append(
            f"注意：{total} 层来自 1999 年以通孔插件为主的经验表，现代 HDI/BGA 板常显著低于此值；"
            "该数字只作上限参考，实际层数须结合封装 pitch、成本与打样能力权衡")
    print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else _fmt_simple(out))
    return 0


# ---------------------------------------------------------------- spacing

def cmd_spacing(args) -> int:
    tiers = load_constraints()["trace_width_tiers_bell"]["tiers"]
    tiers = sorted(tiers, key=lambda t: t["trace_mm"])
    pick = None
    for t in tiers:
        if t["trace_mm"] >= args.trace:
            pick = t
            break
    if pick is None:
        pick = tiers[-1]
    out = {
        "requested_trace_mm": args.trace,
        "matched_tier_mm": pick["trace_mm"],
        "line_to_line_mm": pick["line_line_mm"],
        "line_to_pad_mm": pick["line_pad_mm"],
        "line_to_via_mm": pick["line_via_mm"],
        "pad_to_pad_mm": pick["pad_pad_mm"],
        "pad_to_via_mm": pick["pad_via_mm"],
        "via_to_via_mm": pick["via_via_mm"],
        "via_pad_inner_mm": pick["via_pad_inner_mm"],
        "via_pad_outer_mm": pick["via_pad_outer_mm"],
        "via_hole_max_mm": pick["via_finished_hole_max_mm"],
        "note": ["0.12mm 档工艺难度高，非必要不用（BELL-17）"],
        "source": "BELL-16/17/18",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else _fmt_simple(out))
    return 0


# ---------------------------------------------------------------- impedance

def cmd_impedance(args) -> int:
    w = args.w * MIL_PER_MM
    h = args.h * MIL_PER_MM
    t = args.t * MIL_PER_MM
    s = (args.s or 0) * MIL_PER_MM
    er = args.er

    if args.type == "microstrip":
        z0 = 87 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))
    elif args.type == "stripline":
        z0 = 60 / math.sqrt(er) * math.log(4 * h / (0.67 * math.pi * (0.8 * w + t)))
    elif args.type == "diff-microstrip":
        z0 = 87 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))
        zdiff = 2 * z0 * (1 - 0.48 * math.exp(-0.96 * s / h))
        z0 = zdiff
    elif args.type == "diff-stripline":
        z0 = 60 / math.sqrt(er) * math.log(4 * h / (0.67 * math.pi * (0.8 * w + t)))
        z0 = 2 * z0 * (1 - 0.347 * math.exp(-2.9 * s / h))
    else:
        raise SystemExit("type 只支持 microstrip / stripline / diff-microstrip / diff-stripline")

    out = {
        "type": args.type,
        "z_ohm": round(z0, 1),
        "w_mm": args.w, "h_mm": args.h, "t_mm": args.t, "er": er,
        "note": [
            "经验公式（IPC-2141 简化式），误差约 ±10%，只用于初选叠层与线宽",
            "正式值必须用板厂阻抗计算器或场求解器复算，并在下单时标注阻抗控制层（TY-6.3.1.1）",
        ],
        "source": "stackup-and-impedance.md",
    }
    if args.target:
        out["target_ohm"] = args.target
        out["delta_pct"] = round((z0 - args.target) / args.target * 100, 1)
    print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else _fmt_simple(out))
    return 0


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="嘉立创 PCB Layout 规范计算器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("width", help="载流线宽")
    p.add_argument("--current", type=float, required=True, help="电流 A")
    p.add_argument("--copper", default="1oz", help="1oz / 2oz")
    p.add_argument("--pour", action="store_true", help="用铜皮作导线（降额 50%）")
    p.add_argument("--delta-t", type=float, default=10.0, help="允许温升 ℃，默认 10")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_width)

    p = sub.add_parser("clearance", help="电气间距")
    p.add_argument("--voltage", type=float, required=True)
    p.add_argument("--side", choices=["primary", "secondary"], default="secondary")
    p.add_argument("--input-range", choices=["150-300", "300-600"], default="150-300")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_clearance)

    p = sub.add_parser("via", help="过孔尺寸")
    p.add_argument("--thickness", type=float, required=True, help="板厚 mm")
    p.add_argument("--drill", type=float, default=12.0, help="期望孔径 mil")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_via)

    p = sub.add_parser("layers", help="层数选择")
    p.add_argument("--pins", type=int, required=True, help="总管脚数")
    p.add_argument("--area-mm2", type=float, required=True)
    p.add_argument("--clock-mhz", type=float, default=None)
    p.add_argument("--rise-ns", type=float, default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_layers)

    p = sub.add_parser("spacing", help="线宽档位对应的间距")
    p.add_argument("--trace", type=float, required=True, help="走线宽度 mm")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_spacing)

    p = sub.add_parser("impedance", help="阻抗估算")
    p.add_argument("--type", required=True)
    p.add_argument("--w", type=float, required=True, help="线宽 mm")
    p.add_argument("--h", type=float, required=True, help="介质厚度 mm")
    p.add_argument("--t", type=float, default=0.035, help="铜厚 mm")
    p.add_argument("--s", type=float, default=None, help="差分间距 mm")
    p.add_argument("--er", type=float, default=4.3)
    p.add_argument("--target", type=float, default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_impedance)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
