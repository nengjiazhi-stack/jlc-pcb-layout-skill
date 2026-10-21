#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check.py -- PCB Layout 规范符合性检查

输入是一份"板子快照" JSON（可从 easyeda pcb components list --include-bbox/--include-pads
导出后加工，或直接喂 plan.py 生成的计划文件）。缺字段的检查项会降级为 INFO，不会假装通过。

用法：
  python check.py board.json
  python check.py plan.json --min-severity WARN --json

输入格式（字段能少就少，缺什么降级什么）：
{
  "board": {"width_mil": 2362, "height_mil": 1772, "copper_layers": 4,
            "thickness_mm": 1.6, "wave_solder": false, "outline": [[x,y],...]},
  "components": [
    {"designator": "U1", "layer": 1, "x": 1000, "y": 800, "rotation": 0,
     "w": 200, "h": 200, "footprint": "QFN-32", "bga": false,
     "pads": [{"num": "1", "net": "VDD", "x": 900, "y": 800}]}
  ],
  "holes": [{"x": 200, "y": 200, "keepout_mil": 348, "spec": "M3"}],
  "traces": [{"net": "VDD", "layer": 1, "x1": 900, "y1": 800, "x2": 900, "y2": 700,
              "width": 20}],
  "vias": [{"net": "GND", "x": 800, "y": 900, "hole": 12, "diameter": 25}]
}

规则出处：assets/constraints.json + references/*.md，输出里的 rule 字段可直接回查原文。
  布局/走线类规则号：TY-* / EM-* / FAB-* / BELL-*
  图面整洁度类规则号：AEST-ALIGN / AEST-PITCH / AEST-ROT / AEST-ANGLE
  （判据见 references/routing-aesthetics.md；阈值在 constraints.json → aesthetics）
"""
from __future__ import annotations

import argparse
import fnmatch
import heapq
import json
import math
import re
import sys
from pathlib import Path

MIL_PER_MM = 39.37007874015748

GND_RE = re.compile(r"^(A?GND|DGND|PGND|GNDA|GNDD|VSS|PE|EGND|0V)$", re.I)
PWR_RE = re.compile(r"^(VIN|VBUS|VSYS|VBAT|VCC|VDD\d*|AVDD|DVDD|PVDD|VOUT|VO|VREF|[+-]?\d+V\d*)$", re.I)
ANCHOR_PREFIX = ("J", "USB", "DC", "RJ", "CN", "MH", "H", "P")

SEV_ORDER = {"FAIL": 0, "WARN": 1, "UNKNOWN": 2, "INFO": 3}


def load_constraints() -> dict:
    for p in (Path(__file__).resolve().parent.parent / "assets" / "constraints.json",
              Path(__file__).resolve().parent / "constraints.json"):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise SystemExit("找不到 assets/constraints.json")


def is_anchor(d: str) -> bool:
    return d.upper().startswith(ANCHOR_PREFIX)


def is_connector(d: str) -> bool:
    """需要插拔操作空间的连接器（J/USB/DC/RJ/CN/P…）。

    与 is_anchor 的区别：锚点集合里含 H/MH（安装孔、定位孔），但安装孔不是
    插拔件，不需要 3mm 插拔净空 —— 把它们算进来会产生大量误报。
    """
    u = d.upper()
    if u.startswith(("H", "MH")):
        return False
    return u.startswith(("J", "USB", "DC", "RJ", "CN", "P"))


def prefix(d: str) -> str:
    m = re.match(r"^([A-Za-z]+)", d)
    return m.group(1).upper() if m else ""


class Report:
    def __init__(self):
        self.items = []

    def add(self, sev, rule, where, msg, fix=""):
        self.items.append({"severity": sev, "rule": rule, "where": where, "issue": msg, "fix": fix})

    def info(self, rule, where, msg, fix=""):
        self.add("INFO", rule, where, msg, fix)

    def warn(self, rule, where, msg, fix=""):
        self.add("WARN", rule, where, msg, fix)

    def fail(self, rule, where, msg, fix=""):
        self.add("FAIL", rule, where, msg, fix)

    def unknown(self, rule, where, msg, fix=""):
        """UNKNOWN = 这条没查成。测不到不等于通过，必须显式报出来。"""
        self.add("UNKNOWN", rule, where, msg, fix)

    def counts(self):
        c = {"FAIL": 0, "WARN": 0, "UNKNOWN": 0, "INFO": 0}
        for i in self.items:
            c[i["severity"]] += 1
        return c


def bbox(c):
    return (c["x"] - c["w"] / 2, c["y"] - c["h"] / 2, c["x"] + c["w"] / 2, c["y"] + c["h"] / 2)


def gap(a, b) -> float:
    """两个 AABB 的边缘间距（重叠为负）。"""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(bx0 - ax1, ax0 - bx1)
    dy = max(by0 - ay1, ay0 - by1)
    if dx < 0 and dy < 0:
        return max(dx, dy)
    return max(dx, dy)


def check_board(snap: dict, con: dict, rep: Report):
    board = snap.get("board", {})
    comps = snap.get("components", [])
    layers = int(board.get("copper_layers", 0) or 0)
    W = board.get("width_mil")
    H = board.get("height_mil")

    # ---- 1) 板框与出框
    if not W or not H:
        rep.unknown("TY-6.1.4", "board", "缺板框尺寸，边距与出框检查跳过",
                 "补 board.width_mil / height_mil 或先跑 pcb outline get")
    else:
        for c in comps:
            x0, y0, x1, y1 = bbox(c)
            if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
                rep.fail("FAB-EDGE-0", c["designator"],
                         "器件超出板框（会被铣掉或无法贴装）",
                         "按板框重排，或缩小编号/换小封装")

    # ---- 2) 板边净空
    if W and H:
        ec = con["placement"]["edge_clearance"]
        floor = ec["manufacturing_floor_mil"]
        default = round(1.0 * MIL_PER_MM, 1)          # 1mm 通用默认
        wave = round(ec["device_to_track_edge_mm"] * MIL_PER_MM, 1)
        need = wave if board.get("wave_solder") else default
        for c in comps:
            x0, y0, x1, y1 = bbox(c)
            d = min(x0, y0, W - x1, H - y1)
            if is_anchor(c["designator"]):
                if d < floor:
                    rep.warn("EM-5.4.12", c["designator"],
                             f"连接器/结构件边缘距板框 {d:.0f}mil < 制造地板 {floor:.0f}mil",
                             "确认本体可骑在板边；焊盘必须全部在框内")
                continue
            if d < floor:
                rep.fail("FAB-EDGE-1", c["designator"],
                         f"距板框仅 {d:.0f}mil，低于制造地板 {floor:.0f}mil（0.5mm）",
                         "内推器件；贴边会被 V-CUT/铣边伤到")
            elif d < need:
                rep.warn("EM-5.4.12", c["designator"],
                         f"距板框 {d:.0f}mil < 建议 {need:.0f}mil"
                         + ("（波峰焊轨道夹持边需 5mm）" if board.get("wave_solder") else "（通用 1mm）"),
                         "非必要不贴边；有夹具/轨道时按 5mm 留")

    # ---- 3) 同层 courtyard 间距
    by_layer = {}
    for c in comps:
        by_layer.setdefault(int(c.get("layer", 1)), []).append(c)
    for layer, lst in by_layer.items():
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = lst[i], lst[j]
                g = gap(bbox(a), bbox(b))
                if g < 12:
                    rep.fail("EM-5.4.11", f"{a['designator']}/{b['designator']}",
                             f"同层 courtyard 间距 {g:.0f}mil < 12mil（0.3mm 最小）",
                             "拉开到 ≥0.3mm；手工贴装需 ≥1.5mm")
                elif g < 25:
                    rep.warn("EM-5.4.11", f"{a['designator']}/{b['designator']}",
                             f"同层间距 {g:.0f}mil 偏小（推荐 ≥25mil/0.6mm）",
                             "留出返修与锡膏空间")

    # ---- 4) BGA 禁布区
    for c in comps:
        if not (c.get("bga") or (c.get("pads") and len(c["pads"]) >= 64)):
            continue
        need = round(con["placement"]["component_spacing"]["bga_keepout_mm"] * MIL_PER_MM, 1)
        for o in comps:
            if o is c:
                continue
            g = gap(bbox(c), bbox(o))
            if g < need:
                rep.warn("EM-5.4.10", f"{c['designator']} 周边",
                         f"{o['designator']} 距 BGA 仅 {g:.0f}mil < {need:.0f}mil 禁布区",
                         "BGA 四周留 3mm（最佳 5mm）供返修与扇出")
                break

    # ---- 5) 安装孔禁布
    for h in snap.get("holes", []):
        r = float(h.get("keepout_mil", 0))
        if r <= 0:
            continue
        hb = (h["x"] - r, h["y"] - r, h["x"] + r, h["y"] + r)
        for c in comps:
            # 安装孔自身的封装（H*/MH*）不算"侵入"
            if prefix(c["designator"]) in ("H", "MH") and \
                    abs(c["x"] - h["x"]) <= r and abs(c["y"] - h["y"]) <= r:
                continue
            if gap(hb, bbox(c)) < 0:
                rep.fail("EM-表5", f"{h.get('spec','孔')}@{h['x']:.0f},{h['y']:.0f}",
                         f"{c['designator']} 侵入安装孔禁布区（应 ≥ φ{2*r:.0f}mil）",
                         "按螺钉规格表留禁布圆（M3→φ8.6mm / M2.5→φ7.6mm）")

    # ---- 6) 去耦电容距离
    # 判定口径：每颗 C 只对它"最近的那颗 IC 电源脚"负责，避免把 A 芯片的电容
    # 拿去和 B 芯片比距离（那样满板都是误报）。
    ics = [c for c in comps if prefix(c["designator"]) == "U" and c.get("pads")]
    caps = [c for c in comps if prefix(c["designator"]) == "C" and c.get("pads")]
    if not ics:
        rep.unknown("TY-6.2.11", "去耦", "没有带 pads 的 U 器件，去耦检查跳过",
                 "导出时加 --include-pads")
    else:
        limit = con["placement"]["decoupling"]["four_layer_max_mil"] if layers >= 4 \
            else con["placement"]["decoupling"]["two_layer_max_mil"]
        for cap in caps:
            nets = {p.get("net", "") for p in cap["pads"]}
            if not (any(GND_RE.match(n) for n in nets) and any(PWR_RE.match(n) for n in nets)):
                continue
            best = None
            for ic in ics:
                pwr_pads = [p for p in ic["pads"] if PWR_RE.match(p.get("net", ""))]
                if not pwr_pads:
                    continue
                d = min(((cap["x"] - p["x"]) ** 2 + (cap["y"] - p["y"]) ** 2) ** 0.5
                        for p in pwr_pads)
                if best is None or d < best[0]:
                    best = (d, ic)
            if best and best[0] > limit:
                d, ic = best
                rep.warn("TY-6.2.11",
                         f"{cap['designator']} → {ic['designator']}（最近）",
                         f"去耦电容距最近电源脚 {d:.0f}mil > {limit:.0f}mil"
                         f"（{'4 层以上' if layers >= 4 else '2 层板'}阈值）",
                         "2 层板拉到 150mil 内；4 层以上关键是过孔压在 pad 旁，横向可放宽")

    # ---- 7) 晶振位置
    xtals = [c for c in comps if prefix(c["designator"]) in ("X", "Y")]
    if xtals and ics:
        limit = con["placement"]["crystal"]["body_to_mcu_max_mil"]
        for x in xtals:
            d = min(((x["x"] - ic["x"]) ** 2 + (x["y"] - ic["y"]) ** 2) ** 0.5 for ic in ics)
            if d > limit:
                rep.warn("TY-7.2.2", x["designator"],
                         f"晶振距最近 IC {d:.0f}mil > {limit:.0f}mil",
                         "晶振体中心应 ≤300mil，且下方铺地、不走线")

    # ---- 8) 朝向一致性
    groups = {}
    for c in comps:
        p = prefix(c["designator"])
        if p in ("R", "C"):
            key = (p, c.get("footprint", f"{c['w']}x{c['h']}"))
            groups.setdefault(key, set()).add(int(c.get("rotation", 0)) % 180)
    for (p, fp), rots in groups.items():
        if len(rots) > 2:
            rep.warn("TY-6.2.5", f"{p} 组({fp})",
                     f"同类器件出现 {len(rots)} 种朝向（{sorted(rots)}），超过 2 种",
                     "同型同封装统一到 0/90 两种朝向，利于贴装与检验")

    # ---- 8b) 有极性器件朝向：方向种类 ≤2，且两种必须正交
    pol_groups = {}
    for c in comps:
        p = prefix(c["designator"])
        is_pol = p in ("D", "LED", "Q") or (p == "C" and c.get("polarized"))
        if not is_pol:
            continue
        key = (p, c.get("footprint", f"{c['w']}x{c['h']}"))
        pol_groups.setdefault(key, set()).add(int(c.get("rotation", 0)) % 360)
    maxdir = con["placement"]["orientation"]["polarity_mark_directions_max"]
    for (p, fp), rots in pol_groups.items():
        if len(rots) <= 1:
            continue
        if len(rots) > maxdir:
            rep.warn("PL-ORI-POL", f"{p} 组({fp})",
                     f"有极性器件出现 {len(rots)} 种朝向 {sorted(rots)}，超过 {maxdir} 种",
                     "同一块板上极性标识方向不超过 2 个；超过会导致插件与检验误判")
        else:
            a, b = sorted(rots)
            if (b - a) % 180 != 90:
                rep.warn("PL-ORI-POL", f"{p} 组({fp})",
                         f"两种极性朝向 {sorted(rots)} 不正交（夹角 {(b - a) % 180}°）",
                         "出现 2 个极性方向时必须互相垂直（差 90°）")

    # ---- 8c) 同封装 IC 的 1 脚朝向一致性
    ic_rot = {}
    for c in comps:
        if prefix(c["designator"]) == "U":
            ic_rot.setdefault(c.get("footprint", f"{c['w']}x{c['h']}"),
                              set()).add(int(c.get("rotation", 0)) % 360)
    for fp, rots in ic_rot.items():
        if len(rots) > 1:
            rep.warn("PL-ORI-IC", f"U 组({fp})",
                     f"同封装 IC 有 {len(rots)} 种 1 脚朝向 {sorted(rots)}",
                     "IC 的 1 脚统一朝同一角落（通常左上），便于检验、调试与飞线")

    # ---- 8d) 器件高度与高矮混放
    hi = con["placement"]["height"]
    with_h = [c for c in comps if c.get("height_mm") is not None]
    if not with_h:
        rep.unknown("PL-HEIGHT", "height", "器件缺 height_mm → 高度与三维干涉检查跳过",
                 "导出时补 height_mm（封装库或 BOM 高度），否则无法判定高器件遮挡")
    else:
        for c in with_h:
            if float(c["height_mm"]) > hi["auto_place_max_mm"]:
                rep.warn("PL-HEIGHT-MAX", c["designator"],
                         f"高度 {float(c['height_mm']):.1f}mm > 标准贴片设备上限 "
                         f"{hi['auto_place_max_mm']}mm",
                         "超高件需手工放置或换封装，投板前先与板厂确认")
        need = hi["tall_to_small_clearance_mm"] * MIL_PER_MM
        hits = 0
        for c in with_h:
            if float(c["height_mm"]) < hi["tall_threshold_mm"]:
                continue
            for o in with_h:
                if o is c or int(o.get("layer", 1)) != int(c.get("layer", 1)):
                    continue
                if float(o["height_mm"]) >= hi["tall_threshold_mm"]:
                    continue
                g = gap(bbox(c), bbox(o))
                if 0 <= g < need:
                    hits += 1
                    if hits <= 6:
                        rep.warn("PL-HEIGHT-MIX", f"{c['designator']}/{o['designator']}",
                                 f"高器件({float(c['height_mm']):.1f}mm) 与矮件"
                                 f"({float(o['height_mm']):.1f}mm) 间距 {g:.0f}mil < {need:.0f}mil",
                                 "高器件形成波峰/回流阴影区并遮挡矮件；拉开 ≥2mm")
        if hits > 6:
            rep.warn("PL-HEIGHT-MIX", "全板", f"另有 {hits - 6} 处高矮混放未逐条列出",
                     "统一拉开高器件与矮件间距")

    # ---- 8e) 电感间距与正交（车规，board.automotive=true 才判定）
    if board.get("automotive"):
        au = con["placement"]["automotive"]
        inds = [c for c in comps if prefix(c["designator"]) == "L"]
        need = au["inductor_spacing_mm"] * MIL_PER_MM
        for i in range(len(inds)):
            for j in range(i + 1, len(inds)):
                a, b = inds[i], inds[j]
                if int(a.get("layer", 1)) != int(b.get("layer", 1)):
                    continue
                if gap(bbox(a), bbox(b)) < need:
                    rep.warn("PL-AUTO-IND", f"{a['designator']}/{b['designator']}",
                             f"两电感间距 < {au['inductor_spacing_mm']}mm（车规）",
                             "电感间距 ≥5mm，避免磁场耦合进采样与敏感回路")
                ra, rb = int(a.get("rotation", 0)) % 180, int(b.get("rotation", 0)) % 180
                if ra == rb:
                    rep.warn("PL-AUTO-IND", f"{a['designator']}/{b['designator']}",
                             f"两电感轴向同向（{ra}°），未正交",
                             "相邻电感轴线应互相垂直布置")

    # ---- 9) 走线层检查（有 traces 才跑）
    traces = snap.get("traces") or []
    vias = snap.get("vias") or []
    if traces:
        check_traces(snap, con, rep, traces, vias, comps)
    else:
        rep.unknown("FAB-DATA", "traces", "没有 traces 数据 → 3W/直角/悬空/线宽一致性检查跳过",
                    "导出 `pcb line list --json` 后用 scripts/snap.py 合并进快照；"
                    "这份快照不能用于交付")

    # ---- 10) 过孔孔厚比（有板厚才判定）
    if vias and board.get("thickness_mm"):
        rows = sorted(con["vias"]["board_thickness_to_min_drill_mil"],
                      key=lambda r: r["thickness_mm"])
        t = float(board["thickness_mm"])
        need = rows[-1]["min_drill_mil"]
        for r in rows:
            if t <= r["thickness_mm"]:
                need = r["min_drill_mil"]
                break
        for v in vias:
            hole = float(v.get("hole", 0) or 0)
            if hole <= 0:
                continue
            if hole < need:
                rep.warn("TY-6.3.1.2.5.1", f"via@{v.get('x', 0):.0f},{v.get('y', 0):.0f}",
                         f"孔径 {hole:.0f}mil < {t}mm 板厚要求的最小 {need:.0f}mil",
                         "加大孔径或减薄板厚；孔厚比过大会导致孔壁镀层断裂")
            if v.get("diameter") and float(v["diameter"]) - hole < 2 * 6:
                rep.warn("EM-5.6.5", f"via@{v.get('x', 0):.0f},{v.get('y', 0):.0f}",
                         f"环宽 {(float(v['diameter']) - hole) / 2:.1f}mil < 6mil",
                         "焊盘直径至少比孔径大 12~16mil")

    # ---- 11) 图面整洁度（"美观"里可自动判的那部分）
    check_aesthetics(con, rep, comps, traces)

    # ---- 11b) 重心平衡（重件偏移会让板子在振动/跌落时应力集中）
    if W and H:
        mech = con["placement"]["mechanical"]
        movers = [c for c in comps if prefix(c["designator"]) not in ("H", "MH")]
        if len(movers) >= 4:
            cx = sum(c["x"] for c in movers) / len(movers)
            cy = sum(c["y"] for c in movers) / len(movers)
            lim = mech["centroid_offset_max_ratio"]
            dxr = abs(cx - W / 2) / (W / 2)
            dyr = abs(cy - H / 2) / (H / 2)
            if dxr > lim or dyr > lim:
                rep.warn("PL-MECH-CG", "全板",
                         f"器件重心偏移 ({dxr:.0%}, {dyr:.0%})，超出半板尺寸的 {lim:.0%}",
                         "重心应接近板几何中心，不得偏移到板边缘 1/4 区域；重件靠近支撑点（螺丝孔）")

    # ---- 11c) 连接器插拔操作空间（可维护性）
    maint = con["placement"]["maintainability"]
    need = maint["connector_mating_clearance_mm"] * MIL_PER_MM
    hits = 0
    for c in comps:
        if not is_connector(c["designator"]):
            continue
        for o in comps:
            if o is c or int(o.get("layer", 1)) != int(c.get("layer", 1)):
                continue
            if prefix(o["designator"]) in ("H", "MH"):
                continue
            g = gap(bbox(c), bbox(o))
            if 0 <= g < need:
                hits += 1
                if hits <= 6:
                    rep.warn("PL-MAINT-CONN", f"{c['designator']} 周边",
                             f"{o['designator']} 距连接器仅 {g:.0f}mil < {need:.0f}mil 操作空间",
                             "经常插拔的连接器周围留 ≥3mm，否则插头/线缆插不进、也易撞掉邻件")
                break
    if hits > 6:
        rep.warn("PL-MAINT-CONN", "全板", f"另有 {hits - 6} 处连接器净空不足未逐条列出",
                 "统一拉开插拔件周围空间")

    # ---- 11d) 基准点（MARK）
    fid = snap.get("fiducials")
    fcfg = con.get("fiducial", {}).get("dfm", {})
    if fid is None:
        rep.unknown("PL-FID", "fiducials", "无 fiducials 数据 → 基准点检查跳过",
                 "从 EDA 导出基准点图元并并入 snapshot；拼板时基准点放在工艺边上")
    else:
        fmin = fcfg.get("count_min", 3)
        if len(fid) < fmin:
            rep.warn("PL-FID-N", "fiducials",
                     f"基准点 {len(fid)} 个 < 最少 {fmin} 个",
                     "每板/拼板至少 3 个：2 个对角 + 1 个定向，且非对称放置防 180° 转错")
        if len(fid) >= 2 and W and H:
            xs = [float(f.get("x", 0)) for f in fid]
            ys = [float(f.get("y", 0)) for f in fid]
            spread = fcfg.get("spread_min_ratio", 0.25)
            if (max(xs) - min(xs)) < spread * W or (max(ys) - min(ys)) < spread * H:
                rep.warn("PL-FID-SPREAD", "fiducials",
                         f"基准点分布跨距不足板尺寸的 {spread:.0%}",
                         "基准点尽量拉到板的对角，跨距越大对位精度越高")

    # ---- 12) 数据缺口
    gaps = []
    if not snap.get("components"):
        gaps.append("components 为空")
    if not any(c.get("pads") for c in comps):
        gaps.append("无 pads → 网络级检查（去耦/串扰/等长）全部不可用")
    if not board.get("outline") and not (W and H):
        gaps.append("无 outline → 边距/回流路径判断降级")
    if gaps:
        rep.unknown("FAB-DATA", "dataset", "；".join(gaps), "补齐后重跑；UNKNOWN 不能算通过")


def _pt_seg_dist(px, py, seg) -> float:
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    qx, qy = x1 + t * dx, y1 + t * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def _seg_seg_dist(a, b) -> float:
    d = min(_pt_seg_dist(a[0], a[1], b), _pt_seg_dist(a[2], a[3], b),
            _pt_seg_dist(b[0], b[1], a), _pt_seg_dist(b[2], b[3], a))
    if _seg_intersect(a, b):
        return 0.0
    return d


def _orient(ax, ay, bx, by, cx, cy) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _seg_intersect(a, b) -> bool:
    d1 = _orient(b[0], b[1], b[2], b[3], a[0], a[1])
    d2 = _orient(b[0], b[1], b[2], b[3], a[2], a[3])
    d3 = _orient(a[0], a[1], a[2], a[3], b[0], b[1])
    d4 = _orient(a[0], a[1], a[2], a[3], b[2], b[3])
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def _angle_between(v1, v2) -> float:
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return 180.0
    c = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _seg(r: dict):
    return (float(r["x1"]), float(r["y1"]), float(r["x2"]), float(r["y2"]))


# ------------------------------------------------------- 冗余走线（RT-*，只读几何判据）
# 重复段不短路、残段不开路，电气上"合法"，所以 ERC/DRC 一条都查不出来。
# 这里只判几何，不判意图：刻意保留的测试点桩、天线焊盘、蛇形等长必须在
# constraints.json → trace_redundancy.allow 里逐条登记，否则照报（并进交付物）。

def _seglen(s) -> float:
    return math.hypot(s[2] - s[0], s[3] - s[1])


def _red_cfg(con: dict) -> dict:
    cfg = dict(con.get("trace_redundancy") or {})
    for k, v in (("collinear_angle_deg", 3.0), ("collinear_tol_mil", 1.5),
                 ("dup_overlap_ratio", 0.8), ("dup_min_len_mil", 20.0),
                 ("stub_min_len_factor", 10.0), ("stub_min_len_mil", 200.0),
                 ("loop_min_perimeter_mil", 50.0),
                 ("detour_ratio", 1.8), ("detour_ratio_high_speed", 1.3)):
        cfg.setdefault(k, v)
    cfg.setdefault("high_speed_patterns", [])
    cfg.setdefault("allow", [])
    cfg.setdefault("severity", {})
    return cfg


def _waived(cfg, net, rule, measured=None):
    """allow 命中就返回登记理由，否则 None。

    带 max_len_mil 的登记只豁免到该长度，超过照报 —— 避免"登记一次永久静音"。
    """
    for a in cfg["allow"]:
        if not fnmatch.fnmatch(str(net), str(a.get("net", ""))):
            continue
        rules = a.get("rules") or []
        if rules and not any(fnmatch.fnmatch(rule, str(r)) for r in rules):
            continue
        cap = a.get("max_len_mil")
        if cap is not None and measured is not None and measured > float(cap):
            continue
        return a.get("reason") or "allow 登记"
    return None


def _dup_pairs(pairs, cfg):
    """同层同网络两段共线且投影重叠 → 重复铜。返回 (i, j, 重叠长, 长i, 长j)。"""
    ang_max = float(cfg["collinear_angle_deg"])
    tol = float(cfg["collinear_tol_mil"])
    ratio_min = float(cfg["dup_overlap_ratio"])
    min_len = float(cfg["dup_min_len_mil"])
    out = []
    for i in range(len(pairs)):
        sa = pairs[i][0]
        la = _seglen(sa)
        if la < min_len:
            continue
        ux, uy = (sa[2] - sa[0]) / la, (sa[3] - sa[1]) / la
        for j in range(i + 1, len(pairs)):
            sb = pairs[j][0]
            lb = _seglen(sb)
            if lb < min_len:
                continue
            sin = abs(ux * (sb[3] - sb[1]) - uy * (sb[2] - sb[0])) / lb
            if math.degrees(math.asin(min(1.0, sin))) > ang_max:
                continue
            d1 = abs((sb[0] - sa[0]) * uy - (sb[1] - sa[1]) * ux)
            d2 = abs((sb[2] - sa[0]) * uy - (sb[3] - sa[1]) * ux)
            if max(d1, d2) > tol:
                continue
            t0 = (sb[0] - sa[0]) * ux + (sb[1] - sa[1]) * uy
            t1 = (sb[2] - sa[0]) * ux + (sb[3] - sa[1]) * uy
            ov = min(max(t0, t1), la) - max(min(t0, t1), 0.0)
            if ov > 0 and ov / min(la, lb) >= ratio_min:
                out.append((i, j, ov, la, lb))
    return out


def _split_touch(pairs, tol: float = 3.0):
    """把端点落在别的段内部的段切开，让 T 接成为图上的真节点。

    不切的话，"从另一条线中段分出去的残段"在图上就是一段悬空的孤立线，
    分枝长度和自环都算不准。
    """
    segs = [p[0] for p in pairs]
    out = []
    for k, (s, w) in enumerate(pairs):
        cuts = []
        dx, dy = s[2] - s[0], s[3] - s[1]
        L2 = dx * dx + dy * dy
        if L2 <= 0:
            continue
        for j, t in enumerate(segs):
            if j == k:
                continue
            for (px, py) in ((t[0], t[1]), (t[2], t[3])):
                if _pt_seg_dist(px, py, s) > tol:
                    continue
                u = ((px - s[0]) * dx + (py - s[1]) * dy) / L2
                if 1e-6 < u < 1 - 1e-6:
                    cuts.append((u, px, py))
        cuts.sort()
        cx, cy = s[0], s[1]
        for (_u, px, py) in cuts:
            if math.hypot(px - cx, py - cy) > 0.5:
                out.append(((cx, cy, px, py), w))
            cx, cy = px, py
        if math.hypot(s[2] - cx, s[3] - cy) > 0.5:
            out.append(((cx, cy, s[2], s[3]), w))
    return out


def _shortest(adj, edges, src, dst, skip: int):
    """src→dst 的最短铜长（排除 skip 这条边）；用于算自环周长。"""
    dist = {src: 0.0}
    pq = [(0.0, src)]
    while pq:
        d, a = heapq.heappop(pq)
        if d > dist.get(a, 1e18) + 1e-9:
            continue
        if a == dst:
            return d
        for e in adj.get(a, []):
            if e == skip:
                continue
            b = edges[e][1] if edges[e][0] == a else edges[e][0]
            nd = d + edges[e][2]
            if nd < dist.get(b, 1e18) - 1e-9:
                dist[b] = nd
                heapq.heappush(pq, (nd, b))
    return None


def _mst_manhattan(pts) -> float:
    n = len(pts)
    if n < 2:
        return 0.0
    in_tree = [False] * n
    best = [1e18] * n
    best[0] = 0.0
    total = 0.0
    for _ in range(n):
        k = min((i for i in range(n) if not in_tree[i]), key=lambda i: best[i])
        in_tree[k] = True
        total += best[k]
        for i in range(n):
            if in_tree[i]:
                continue
            d = abs(pts[i][0] - pts[k][0]) + abs(pts[i][1] - pts[k][1])
            if d < best[i]:
                best[i] = d
    return total


def _stub_scan(nodes, adj, edges, anchored, cfg):
    """从每个自由端走到第一个岔路口或锚点，累计铜长。返回超过阈值的残段。

    长度按"从自由端一路走到第一个分叉"算，不是按单段算 —— 残段常常是两三段
    接起来的，只看单段会漏。
    """
    out = []
    visited = set()
    for k in sorted(adj):
        if len(adj[k]) != 1 or k in anchored:
            continue
        e0 = adj[k][0]
        if e0 in visited:
            continue
        total, cur, e, guard = 0.0, k, e0, 0
        while e is not None and guard < 4000:
            guard += 1
            visited.add(e)
            total += edges[e][2]
            cur = edges[e][1] if edges[e][0] == cur else edges[e][0]
            if cur in anchored or len(adj.get(cur, [])) != 2:
                break
            rest = [x for x in adj[cur] if x != e]
            e = rest[0] if rest else None
        need = max(float(cfg["stub_min_len_mil"]),
                   float(cfg["stub_min_len_factor"]) * max(1.0, edges[e0][3]))
        if total < need:
            continue
        want = nodes[k]
        tail = min((edges[e0][4][0:2], edges[e0][4][2:4]),
                   key=lambda q: (q[0] - want[0]) ** 2 + (q[1] - want[1]) ** 2)
        out.append((tail, total, need))
    return out


def check_redundancy(con, rep, traces, vias, comps) -> list:
    """RT-DUP-01 / RT-STUB-02 / RT-LOOP-03 / RT-DETOUR-04。

    返回被 RT-STUB 升级报出的端点坐标，供 TY-6.5.6-5 去重（同一个缺陷不报两遍）。
    """
    cfg = _red_cfg(con)
    end_tol = 22.0
    node_tol = 8.0
    sev = lambda r: cfg["severity"].get(r, "WARN")
    escalated = []
    waived = {}

    pad_pts, via_pts, loose_pads = {}, {}, []
    for c in comps:
        for p in c.get("pads") or []:
            if p.get("net"):
                pad_pts.setdefault(str(p["net"]), []).append((float(p["x"]), float(p["y"])))
            else:
                # 没有网络名的焊盘：导线落上去时判不了它属于谁，只能按"落到了焊盘"处理，
                # 不能按悬空报 —— 报错要能站得住，而不是靠猜。
                loose_pads.append((float(p["x"]), float(p["y"])))
    for v in vias:
        if v.get("net"):
            via_pts.setdefault(str(v["net"]), []).append((float(v["x"]), float(v["y"])))

    groups = {}
    for t in traces:
        groups.setdefault(str(t.get("net", "?")), []).append(t)

    # 残段判据靠"端点有没有落在焊盘/过孔上"。整份快照连一个焊盘坐标都没有时，
    # 它判不了 —— 这不叫通过，也不该编出 FAIL：缺数据已由 check_dataset 的
    # FAB-DATA UNKNOWN 报出，这里不重复记一条，避免同一件事算两次。
    can_stub = bool(pad_pts) or bool(via_pts)
    no_anchor_nets = []

    def note_waive(net, rule, why, measured, where):
        waived.setdefault(rule, []).append(f"{net}@{where}")
        rep.info("RT-ALLOW", f"net {net} @{where}",
                 f"{rule} 按登记豁免（量得 {measured:.0f}）：{why}",
                 "豁免项必须写进交付物的「允许保留的冗余」并附理由")

    for net in sorted(groups):
        anchors = pad_pts.get(net, []) + via_pts.get(net, [])
        by_layer = {}
        for t in groups[net]:
            by_layer.setdefault(int(t.get("layer", 1)), []).append(t)

        tree_len = 0.0
        for layer in sorted(by_layer):
            raw = [(_seg(t), float(t.get("width", 0) or 0)) for t in by_layer[layer]]

            # ---- RT-DUP-01 重复/重叠共线段
            drop = set()
            for (i, j, ov, la, lb) in _dup_pairs(raw, cfg):
                short_i = i if la <= lb else j
                if short_i in drop:
                    continue
                drop.add(short_i)
                where = (f"{raw[short_i][0][0]:.0f},{raw[short_i][0][1]:.0f}"
                         f"→{raw[short_i][0][2]:.0f},{raw[short_i][0][3]:.0f}")
                why = _waived(cfg, net, "RT-DUP-01", ov)
                if why:
                    note_waive(net, "RT-DUP-01", why, ov, where)
                    continue
                rep.add(sev("RT-DUP-01"), "RT-DUP-01", f"net {net} layer {layer}",
                        f"两段走线共线重叠 {ov:.0f}mil（占较短段 {ov / min(la, lb) * 100:.0f}%），"
                        f"位置 {where}：同一段铜画了两遍",
                        "删掉一段（先删后画）；重叠铜蚀刻后留残渣，阻抗也会突变")

            kept = [p for k, p in enumerate(raw) if k not in drop]
            if not kept:
                continue
            split = _split_touch(kept)

            nodes, adj, edges = [], {}, []

            def node_of(x, y):
                for k, (nx, ny) in enumerate(nodes):
                    if (x - nx) ** 2 + (y - ny) ** 2 <= node_tol ** 2:
                        return k
                nodes.append((x, y))
                return len(nodes) - 1

            for (s, w) in split:
                u, v = node_of(s[0], s[1]), node_of(s[2], s[3])
                if u == v:
                    continue
                e = len(edges)
                edges.append((u, v, _seglen(s), w, s))
                adj.setdefault(u, []).append(e)
                adj.setdefault(v, []).append(e)
            if not edges:
                continue

            anchored = set()
            for k, (nx, ny) in enumerate(nodes):
                if any((nx - ax) ** 2 + (ny - ay) ** 2 <= end_tol ** 2
                       for ax, ay in anchors + loose_pads):
                    anchored.add(k)

            # ---- RT-STUB-02 残段：从自由端走到第一个岔路口/锚点，长度超标就 FAIL
            if can_stub and anchors:
                for (tail, total, need) in _stub_scan(nodes, adj, edges, anchored, cfg):
                    why = _waived(cfg, net, "RT-STUB-02", total)
                    if why:
                        note_waive(net, "RT-STUB-02", why, total, f"{tail[0]:.0f},{tail[1]:.0f}")
                        escalated.append((float(tail[0]), float(tail[1])))
                        continue
                    rep.add(sev("RT-STUB-02"), "RT-STUB-02",
                            f"net {net} layer {layer} @{tail[0]:.0f},{tail[1]:.0f}",
                            f"残段（自由端）总长 {total:.0f}mil ≥ 阈值 {need:.0f}mil：末端没有焊盘、"
                            f"过孔或其它走线，是天线也是蚀刻残渣",
                            "删掉这段，或把它接到该接的脚上；只短于阈值的小尾巴按 TY-6.5.6-5 记 WARN")
                    escalated.append((float(tail[0]), float(tail[1])))
            elif can_stub:
                no_anchor_nets.append(net)

            # ---- RT-LOOP-03 同网络自环（TY-6.5.6-8）
            parent = list(range(len(nodes)))

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            loops = 0
            for e, (u, v, L, _w, s) in enumerate(edges):
                ru, rv = find(u), find(v)
                if ru != rv:
                    parent[ru] = rv
                    tree_len += L          # 只累计生成树：闭合边不重复算进总长
                    continue
                per = _shortest(adj, edges, u, v, e)
                if per is None:
                    continue
                per += L
                if per < float(cfg["loop_min_perimeter_mil"]):
                    continue
                loops += 1
                why = _waived(cfg, net, "RT-LOOP-03", per)
                if why:
                    note_waive(net, "RT-LOOP-03", why, per, f"{s[0]:.0f},{s[1]:.0f}")
                    continue
                if loops <= 3:
                    rep.add(sev("RT-LOOP-03"), "RT-LOOP-03",
                            f"net {net} layer {layer} @{s[0]:.0f},{s[1]:.0f}",
                            f"该网络形成闭合回路，周长 {per:.0f}mil（≥{cfg['loop_min_perimeter_mil']:.0f}mil）",
                            "断开回路（TY-6.5.6-8）：闭合铜环会拾取磁场，也扩大回流面积")
            if loops > 3:
                rep.add(sev("RT-LOOP-03"), "RT-LOOP-03", f"net {net} layer {layer}",
                        f"另有 {loops - 3} 处闭合回路未逐条列出", "统一断开")

        # ---- RT-DETOUR-04 绕行：生成树铜长 / 引脚曼哈顿最小生成树
        terms = pad_pts.get(net, [])
        if len(terms) >= 2 and tree_len > 0:
            ref = _mst_manhattan(terms)
            if ref > 0:
                ratio = tree_len / ref
                hs = any(fnmatch.fnmatch(net, p) for p in cfg["high_speed_patterns"])
                limit = float(cfg["detour_ratio_high_speed"] if hs else cfg["detour_ratio"])
                if ratio > limit:
                    why = _waived(cfg, net, "RT-DETOUR-04")
                    if why:
                        note_waive(net, "RT-DETOUR-04", why, ratio * ref, "全网络")
                    else:
                        rep.add(sev("RT-DETOUR-04"), "RT-DETOUR-04", f"net {net}",
                                f"实际铜长 {tree_len:.0f}mil 是最短可能的 {ratio:.2f} 倍"
                                f"（阈值 {limit:.1f}{'，高速网络' if hs else ''}）",
                                "重排引脚顺序或换层走捷径；绕远等于加大环路面积与串扰")

    if no_anchor_nets:
        shown = "、".join(sorted(set(no_anchor_nets))[:5])
        rep.unknown("RT-STUB-02", "全板",
                    f"{len(set(no_anchor_nets))} 个网络在这份快照里一个焊盘/过孔坐标都没有"
                    f"（{shown}），残段判据无法判断端点是否落在焊盘上 → 未判定",
                    "导出时加 --include-pads/--include-vias；若确实没有焊盘，这段铜就是孤立的")

    if waived:
        total = sum(len(v) for v in waived.values())
        detail = "；".join(f"{r} {len(v)} 处" for r, v in sorted(waived.items()))
        rep.info("RT-ALLOW", "全板", f"allow 登记豁免 {total} 处（{detail}）",
                 "逐条写进交付物；豁免不等于「没这问题」，而是有理由地留着")
    return escalated


def check_traces(snap, con, rep, traces, vias, comps):
    """走线层检查：悬空端、直角/锐角、3W、同网络线宽一致、异网短路、冗余走线。"""
    tol = 8.0            # 线段接头重合容差
    end_tol = 22.0       # 线头落点容差：走线通常停在焊盘边缘而不是中心
    escalated = check_redundancy(con, rep, traces, vias, comps)
    anchors = []
    for c in comps:
        for p in c.get("pads") or []:
            anchors.append((float(p["x"]), float(p["y"])))
    for v in vias:
        anchors.append((float(v["x"]), float(v["y"])))
    ends = [(float(t["x1"]), float(t["y1"])) for t in traces] + \
           [(float(t["x2"]), float(t["y2"])) for t in traces]
    segs = [_seg(t) for t in traces]

    # 悬空端（TY-6.5.6-5 开环检查）
    dangling = {}
    for t in traces:
        for (ex, ey) in ((float(t["x1"]), float(t["y1"])), (float(t["x2"]), float(t["y2"]))):
            # 已被 RT-STUB-02 升级为 FAIL 的长残段不再报 WARN：同一个缺陷不报两遍
            if any((ex - sx) ** 2 + (ey - sy) ** 2 <= 1.0 for sx, sy in escalated):
                continue
            hit = any((ex - ax) ** 2 + (ey - ay) ** 2 <= end_tol ** 2 for ax, ay in anchors)
            if not hit:
                hit = sum(1 for (ox, oy) in ends
                          if (ex - ox) ** 2 + (ey - oy) ** 2 <= tol ** 2) >= 2
            if not hit:
                # 落在另一段走线的体内（T 接）也算连上
                hit = any(_pt_seg_dist(ex, ey, s) <= 3 for s in segs
                          if s != (float(t["x1"]), float(t["y1"]), float(t["x2"]), float(t["y2"])))
            if not hit:
                dangling.setdefault(t.get("net", "?"), []).append((ex, ey))
    for net, pts in list(dangling.items())[:8]:
        rep.warn("TY-6.5.6-5", f"net {net}",
                 f"{len(pts)} 个线头悬空（首端 {pts[0][0]:.0f},{pts[0][1]:.0f}）",
                 "悬空线头是天线：接到焊盘/过孔，或删掉这段铜")

    # 直角/锐角（TY-6.5.6-12 倒角）
    bends = 0
    for i in range(len(traces)):
        a = traces[i]
        sa = _seg(a)
        for j in range(i + 1, len(traces)):
            b = traces[j]
            if int(a.get("layer", 1)) != int(b.get("layer", 1)):
                continue
            if a.get("net") != b.get("net"):
                continue
            sb = _seg(b)
            cand = []
            if abs(sa[2] - sb[0]) <= tol and abs(sa[3] - sb[1]) <= tol:
                cand.append((sa[2], sa[3], (sa[0] - sa[2], sa[1] - sa[3]),
                             (sb[2] - sb[0], sb[3] - sb[1])))
            if abs(sa[2] - sb[2]) <= tol and abs(sa[3] - sb[3]) <= tol:
                cand.append((sa[2], sa[3], (sa[0] - sa[2], sa[1] - sa[3]),
                             (sb[0] - sb[2], sb[1] - sb[3])))
            if abs(sa[0] - sb[0]) <= tol and abs(sa[1] - sb[1]) <= tol:
                cand.append((sa[0], sa[1], (sa[2] - sa[0], sa[3] - sa[1]),
                             (sb[2] - sb[0], sb[3] - sb[1])))
            if abs(sa[0] - sb[2]) <= tol and abs(sa[1] - sb[3]) <= tol:
                cand.append((sa[0], sa[1], (sa[2] - sa[0], sa[3] - sa[1]),
                             (sb[0] - sb[2], sb[1] - sb[3])))
            for (vx, vy, oa, ob) in cand:
                ang = _angle_between(oa, ob)
                if ang <= 95:
                    bends += 1
                    if bends <= 6:
                        rep.warn("TY-6.5.6-12", f"net {a.get('net')} @{vx:.0f},{vy:.0f}",
                                 f"转折角 {ang:.0f}°（≤90° 直角/锐角，酸角陷阱）",
                                 "改 45°（内角 135°）；高速线用圆弧")
    if bends > 6:
        rep.warn("TY-6.5.6-12", "全板", f"另有 {bends - 6} 处直角/锐角未逐条列出", "统一改 45°")

    # 3W / 异网短路（TY-6.5.6-18）
    shorts = 0
    w3 = 0
    for i in range(len(traces)):
        a = traces[i]
        sa = _seg(a)
        wa = float(a.get("width", 0) or 0)
        for j in range(i + 1, len(traces)):
            b = traces[j]
            if int(a.get("layer", 1)) != int(b.get("layer", 1)):
                continue
            if a.get("net") == b.get("net"):
                continue
            sb = _seg(b)
            wb = float(b.get("width", 0) or 0)
            d = _seg_seg_dist(sa, sb)
            if d <= 0.5:
                shorts += 1
                if shorts <= 5:
                    rep.fail("FAB-SHORT", f"{a.get('net')} × {b.get('net')}",
                             f"同层（layer {a.get('layer')}）走线相交 → 短路",
                             "改层或重新布线；交叉必须换层并打过孔")
                continue
            ang = _angle_between((sa[2] - sa[0], sa[3] - sa[1]), (sb[2] - sb[0], sb[3] - sb[1]))
            ang = min(ang, 180 - ang)
            need = 3 * max(wa, wb)
            if ang > 25 or need <= 0:
                continue
            # 平行才算 3W：用 a 的轴向投影判断两者是否"并排"（首尾相接的串行段不算）
            ax_, ay_ = sa[2] - sa[0], sa[3] - sa[1]
            la = math.hypot(ax_, ay_)
            if la == 0:
                continue
            ux, uy = ax_ / la, ay_ / la
            tb0 = (sb[0] - sa[0]) * ux + (sb[1] - sa[1]) * uy
            tb1 = (sb[2] - sa[0]) * ux + (sb[3] - sa[1]) * uy
            overlap = min(max(tb0, tb1), la) - max(min(tb0, tb1), 0.0)
            if overlap < max(20.0, 2 * max(wa, wb)):
                continue          # 首尾相接或只擦到一点，不算并排长距离平行
            # 垂直距离（平行段的真实间距）
            perp = min(abs((sb[0] - sa[0]) * uy - (sb[1] - sa[1]) * ux),
                       abs((sb[2] - sa[0]) * uy - (sb[3] - sa[1]) * ux))
            if perp < need:
                w3 += 1
                if w3 <= 6:
                    rep.warn("TY-6.5.6-18", f"{a.get('net')} ∥ {b.get('net')}",
                             f"平行段中心距 {perp:.0f}mil < 3W（{need:.0f}mil，"
                             f"重叠长度 {overlap:.0f}mil）",
                             "拉开到 3W，或中间插入接地隔离线（TY-6.5.6-2）")
    if w3 > 6:
        rep.warn("TY-6.5.6-18", "全板", f"另有 {w3 - 6} 处 3W 不足未逐条列出", "统一拉间距")

    # 同网络线宽一致（TY-6.5.6-6）
    widths = {}
    for t in traces:
        widths.setdefault(t.get("net", "?"), set()).add(round(float(t.get("width", 0) or 0), 1))
    for net, ws in widths.items():
        if len(ws) > 2:
            rep.info("TY-6.5.6-6", f"net {net}",
                     f"出现 {len(ws)} 种线宽 {sorted(ws)}",
                     "阻抗/载流网络应全程等宽；变宽段（连接器、BGA 出线）压到最短")


def _cluster_lines(items, fixed_axis: str, other_axis: str, band: float, gap_mult: float):
    """把器件聚成"行"或"列"。

    fixed_axis 是共享坐标（行看 y、列看 x），other_axis 是排列方向。
    两步：
      1. 按 fixed_axis 邻近分组（容差 band）；
      2. 组内沿 other_axis 排序，若相邻两件隔得远到"根本不算一排"（> gap_mult × 自身尺寸），整组作废。
    第 2 步是为了挡掉这类误判：板子左上角和左下角两颗电容 x 一样，它们不构成"一列"。
    """
    buckets = []
    for it in sorted(items, key=lambda c: float(c[fixed_axis])):
        for grp in buckets:
            if abs(float(it[fixed_axis]) - grp["anchor"]) <= band:
                grp["items"].append(it)
                break
        else:
            buckets.append({"anchor": float(it[fixed_axis]), "items": [it]})

    keep = []
    for grp in buckets:
        if len(grp["items"]) < 2:
            continue
        seq = sorted(grp["items"], key=lambda c: float(c[other_axis]))
        adjacent = True
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            dims = [float(v) for v in (a.get("w"), a.get("h"), b.get("w"), b.get("h"))
                    if v is not None and float(v) > 0]
            size = max(dims) if dims else 0.0
            limit = gap_mult * (size if size > 0 else 100.0)
            if abs(float(b[other_axis]) - float(a[other_axis])) > limit:
                adjacent = False
                break
        if adjacent:
            keep.append(grp)
    return keep


def check_aesthetics(con, rep, comps, traces):
    """图面整洁度：基准线对齐、同组间距一致性、正交朝向、45° 走线。

    判据来源 references/routing-aesthetics.md。这里只判"同组对象是否共享基准"，
    不判"好不好看"——留白比例、配色、丝印字体这类主观项刻意不做。
    """
    ast = con.get("aesthetics")
    if not ast:
        rep.unknown("AEST-DATA", "aesthetics", "constraints.json 没有 aesthetics 节，整洁度检查跳过")
        return

    al = ast.get("alignment", {})
    band = float(al.get("row_band_mil", 60))
    tol = float(al.get("center_line_tol_mil", 12))
    min_n = int(al.get("min_group_size", 3))
    share_min = float(al.get("aligned_share_min", 0.6))
    gap_mult = float(al.get("neighbor_gap_max_mult", 6))
    scope = set(al.get("scope_prefixes", ["R", "C", "L", "D", "Q", "FB"]))

    # 分组口径：同前缀 + 同封装。不同封装的尺寸本来就不同，共线与否另有标准。
    groups = {}
    for c in comps:
        p = prefix(c["designator"])
        if p not in scope:
            continue
        groups.setdefault((p, str(c.get("footprint"))), []).append(c)

    hit_align = 0
    for (p, fp), lst in groups.items():
        if len(lst) < min_n:
            continue
        for axis, label, other in (("y", "行", "x"), ("x", "列", "y")):
            for grp in _cluster_lines(lst, axis, other, band, gap_mult):
                items = grp["items"]
                if len(items) < min_n:
                    continue
                vals = [float(c[axis]) for c in items]
                med = sorted(vals)[len(vals) // 2]
                devs = [abs(v - med) for v in vals]
                if max(devs) <= tol:
                    continue
                # 只在"多数已经齐了、少数掉队"时判掉落 —— 两排交错的有意布局不该被误判
                aligned = sum(1 for d in devs if d <= tol) / len(devs)
                if aligned < share_min:
                    continue
                worst = items[devs.index(max(devs))]
                hit_align += 1
                if hit_align <= 6:
                    rep.warn("AEST-ALIGN", f"{p} 组({fp}) 第{label}",
                             f"{len(items)} 颗本应共线，{worst['designator']} 偏离基准线 "
                             f"{max(devs):.0f}mil（基准 {med:.0f}mil，容差 {tol:.0f}mil）",
                             f"把中心对齐到 {med:.0f}mil；基准取组内众数，别逐颗手调")
    if hit_align > 6:
        rep.warn("AEST-ALIGN", "全板", f"另有 {hit_align - 6} 组未对齐", "统一按组对齐")

    # 间距一致性：同一行/列内相邻中心距的离散度
    pc = ast.get("pitch", {})
    cv_warn = float(pc.get("cv_warn", 0.25))
    pc_n = int(pc.get("min_group_size", 4))
    hit_pitch = 0
    for (p, fp), lst in groups.items():
        if len(lst) < pc_n:
            continue
        for axis, label, other in (("y", "行", "x"), ("x", "列", "y")):
            for grp in _cluster_lines(lst, axis, other, band, gap_mult):
                items = sorted(grp["items"], key=lambda c: float(c[other]))
                if len(items) < pc_n:
                    continue
                gaps = [float(items[i + 1][other]) - float(items[i][other])
                        for i in range(len(items) - 1)]
                if not gaps:
                    continue
                mean = sum(gaps) / len(gaps)
                if mean <= 0:
                    continue
                sd = (sum((g - mean) ** 2 for g in gaps) / len(gaps)) ** 0.5
                if sd / mean <= cv_warn:
                    continue
                hit_pitch += 1
                if hit_pitch <= 5:
                    rep.warn("AEST-PITCH", f"{p} 组({fp}) 第{label}",
                             f"相邻中心距离散：{min(gaps):.0f}~{max(gaps):.0f}mil，"
                             f"均值 {mean:.0f}mil，变异系数 {sd / mean:.0%} > {cv_warn:.0%}",
                             "同组等距排列；间距差异要有理由（散热/串扰/返修空间），否则均匀化")
    if hit_pitch > 5:
        rep.warn("AEST-PITCH", "全板", f"另有 {hit_pitch - 5} 组间距不均", "统一阵列间距")

    # 正交朝向：非 90° 整数倍旋转会让贴装、AOI、丝印方向全歪
    step = float(ast.get("rotation", {}).get("allowed_step_deg", 90))
    odd = [c for c in comps if 0.5 < (float(c.get("rotation", 0) or 0) % step) < step - 0.5]
    for c in odd[:6]:
        rep.warn("AEST-ROT", c["designator"],
                 f"旋转 {float(c.get('rotation', 0)):g}° 不是 {step:g}° 整数倍",
                 "贴装与 AOI 需要正交朝向；光学/机械确有需要时记录理由")
    if len(odd) > 6:
        rep.warn("AEST-ROT", "全板", f"另有 {len(odd) - 6} 颗非正交旋转", "统一到 0/90/180/270")

    # 45° 走线：0/45/90 之外的角度，图面显乱且绕等长时误差难控
    ta = ast.get("trace_angle", {})
    step_a = float(ta.get("allowed_step_deg", 45))
    tol_a = float(ta.get("tolerance_deg", 4))
    ratio = float(ta.get("min_len_over_width", 3))
    offnet = {}
    for t in traces:
        s = _seg(t)
        dx, dy = s[2] - s[0], s[3] - s[1]
        L = math.hypot(dx, dy)
        w = float(t.get("width", 0) or 0)
        if L <= 0 or (w > 0 and L < ratio * w):
            continue
        a = abs(math.degrees(math.atan2(dy, dx))) % step_a
        dev = min(a, step_a - a)
        if dev > tol_a:
            offnet.setdefault(t.get("net", "?"), []).append(
                (dev, (s[0] + s[2]) / 2, (s[1] + s[3]) / 2))
    for net, lst in list(offnet.items())[:6]:
        dev, mx, my = max(lst, key=lambda x: x[0])
        rep.warn("AEST-ANGLE", f"net {net}",
                 f"{len(lst)} 段走线角度偏离 {step_a:g}° 栅格（最大偏差 {dev:.0f}°，"
                 f"位于 {mx:.0f},{my:.0f}）",
                 "改用 0/45/90；RF 与高速差分另按圆弧处理，并在此处注明")
    if len(offnet) > 6:
        rep.warn("AEST-ANGLE", "全板", f"另有 {len(offnet) - 6} 个网络存在任意角度走线",
                 "统一到 45° 栅格")

    # 采集缺口：这两项写得很死，但没有数据源，明确说不判定而不是假装通过
    rep.info("AEST-DATA", "silkscreen / dead-copper",
             f"丝印字高（≥{ast.get('silkscreen_limits', {}).get('char_height_min_mil', 25)}mil）、"
             f"丝印压焊盘、死铜 未判定 —— 快照里没有丝印图元与铺铜对象",
             "需要时从 EDA 导出丝印层/铺铜列表并入快照")


# ---------------------------------------------------------------- 块级布局约束

DIST_RE = re.compile(r"<=\s*([\d.]+)\s*(mm|mil)", re.I)


def _limit_mil(value):
    """从 "<=2mm" / "<= 150mil" 里取阈值，统一换成 mil。取不到返回 None。"""
    m = DIST_RE.search(value or "")
    if not m:
        return None
    v, unit = float(m.group(1)), m.group(2).lower()
    return v * MIL_PER_MM if unit == "mm" else v


def check_block_constraints(snap, nl, rep):
    """消费网表里带过来的块级布局约束（来自 chip-variants.json 的 pcb_layout）。

    只自动判能落到几何上的两条：去耦贴 IC、保护器件贴连接器。
    其余（散热铜、热回路、天线禁铜、偏置成对、极性丝印…）一律报 UNKNOWN 汇总，
    绝不静默当作满足 —— 这是本函数存在的全部意义。
    """
    cons = nl.get("layout_constraints") or []
    if not cons:
        rep.info("BLK-NA", "netlist",
                 "网表没有块级布局约束（未套芯片实例，或实例未带 pcb_layout）",
                 "用 `schem.py netlist --chip 块=型号` 生成带约束的网表")
        return

    by_ref = {str(c.get("designator", "")).upper(): c for c in snap.get("components", [])}
    inst: dict = {}
    for p in nl.get("parts", []):
        inst.setdefault((p.get("block"), p.get("instance")), []).append(p)

    auto_ok, unsupported = 0, {}

    def members(block, instance, kinds):
        out = []
        for p in inst.get((block, instance), []):
            if p.get("kind") in kinds and str(p.get("ref", "")).upper() in by_ref:
                out.append(by_ref[str(p["ref"]).upper()])
        return out

    for c in cons:
        rule, val = c.get("rule"), c.get("value")
        block, instance, ref = c.get("block"), c.get("instance"), str(c.get("ref", ""))
        where = f"{ref}/{rule}"
        limit = _limit_mil(val)
        sev = "FAIL" if c.get("severity") == "must" else "WARN"

        if rule == "decap-adjacency" and limit:
            target = by_ref.get(ref.upper())
            caps = members(block, instance, ("C",))
            if not target or not caps:
                rep.unknown("BLK-DECAP", where,
                            f"{'主器件' if not target else '去耦电容'}不在快照里，"
                            f"“{val}”这条无法判定",
                            "把该块的真实器件与位置导出到快照后再查")
                continue
            best = min(caps, key=lambda x: gap(bbox(x), bbox(target)))
            g = gap(bbox(best), bbox(target))
            if g > limit:
                rep.add(sev, "BLK-DECAP", where,
                        f"{best['designator']} 到 {ref} 间距 {g / MIL_PER_MM:.2f}mm > "
                        f"{limit / MIL_PER_MM:.1f}mm（{c.get('constraint', '')}）",
                        "去耦挪到电源脚旁边；这比“摆整齐”优先，电气 > 美观")
            else:
                auto_ok += 1
            continue

        if rule in ("esd-adjacency", "tvs-at-connector") and limit:
            conns = members(block, instance, ("J",))
            prot = members(block, instance, ("TVS", "D", "ESD"))
            if not conns or not prot:
                rep.unknown("BLK-ESD", where,
                            f"连接器或保护器件不在快照里，“{val}”这条无法判定",
                            "导出该块全部器件位置后再查")
                continue
            best = min(prot, key=lambda x: min(gap(bbox(x), bbox(j)) for j in conns))
            g = min(gap(bbox(best), bbox(j)) for j in conns)
            if g > limit:
                rep.add(sev, "BLK-ESD", where,
                        f"{best['designator']} 到连接器间距 {g / MIL_PER_MM:.2f}mm > "
                        f"{limit / MIL_PER_MM:.1f}mm（{c.get('constraint', '')}）",
                        "保护器件必须贴在连接器焊盘旁，信号先过保护再进板")
            else:
                auto_ok += 1
            continue

        unsupported.setdefault(rule or "?", 0)
        unsupported[rule or "?"] += 1

    if auto_ok:
        rep.info("BLK-AUTO", "layout_constraints", f"{auto_ok} 条带阈值的块级约束已自动判定通过")
    if unsupported:
        names = "、".join(f"{k}×{v}" for k, v in sorted(unsupported.items()))
        rep.unknown("BLK-MANUAL", "layout_constraints",
                    f"{sum(unsupported.values())} 条块级约束没有几何判据，只能人看图确认：{names}",
                    "不要因为程序没报错就认为这些约束已满足；要判就得补可见性/丝印/铜面数据")


def _run_erc(netlist_path):
    """调同包的 schem.py 跑 ERC，拿回 counts。失败返回 None，由 gate 报 UNKNOWN。"""
    import subprocess
    schem = Path(__file__).resolve().with_name("schem.py")
    if not schem.exists() or not Path(netlist_path).exists():
        return None
    try:
        r = subprocess.run([sys.executable, str(schem), "erc", str(netlist_path), "--json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return json.loads(r.stdout).get("counts")
    except Exception:
        return None


def normalize(snap: dict) -> dict:
    """兼容 plan.py 的计划文件：把 meta.board_mil 映射成 board，并补齐缺省字段。"""
    if "board" not in snap and "meta" in snap:
        bm = snap["meta"].get("board_mil") or [0, 0]
        snap["board"] = {"width_mil": bm[0], "height_mil": bm[1], "copper_layers": 0}
    for c in snap.get("components", []):
        c.setdefault("layer", 1)
        c.setdefault("rotation", 0)
        c.setdefault("footprint", f"{c.get('w')}x{c.get('h')}")
    return snap


def _merge_counts(dst, src):
    for k in ("FAIL", "WARN", "UNKNOWN", "INFO"):
        dst[k] = dst.get(k, 0) + (src or {}).get(k, 0)
    return dst


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PCB Layout 规范符合性检查")
    ap.add_argument("snapshot", help="板子快照 JSON（也接受 plan.py 的计划文件）")
    ap.add_argument("--min-severity", choices=["FAIL", "WARN", "UNKNOWN", "INFO"], default="INFO")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有 FAIL 时返回 1")
    ap.add_argument("--require-known", action="store_true",
                    help="有 UNKNOWN 时返回 2：检查没跑全不算通过")
    ap.add_argument("--netlist", help="网表 JSON：并入 ERC 与块级布局约束检查")
    ap.add_argument("--gate", action="store_true", help="输出单一门禁判定（BLOCKED/INCOMPLETE/REVIEW/PASS）")
    args = ap.parse_args(argv)

    snap = normalize(json.loads(Path(args.snapshot).read_text(encoding="utf-8")))
    con = load_constraints()
    rep = Report()
    check_board(snap, con, rep)

    if args.netlist:
        nl = json.loads(Path(args.netlist).read_text(encoding="utf-8"))
        check_block_constraints(snap, nl, rep)

    keep = [i for i in rep.items if SEV_ORDER[i["severity"]] <= SEV_ORDER[args.min_severity]]
    keep.sort(key=lambda i: (SEV_ORDER[i["severity"]], i["rule"]))
    counts = rep.counts()
    erc = _run_erc(args.netlist) if args.netlist else None

    if args.json:
        out = {"summary": counts, "items": keep}
        if args.netlist:
            out["erc"] = erc
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        icon = {"FAIL": "✗", "WARN": "!", "UNKNOWN": "?", "INFO": "·"}
        print(f"检查结果：FAIL {counts['FAIL']} / WARN {counts['WARN']} / "
              f"UNKNOWN {counts['UNKNOWN']} / INFO {counts['INFO']}\n")
        for i in keep:
            print(f"[{i['severity']:<7}] {icon[i['severity']]} {i['rule']:<14} {i['where']}")
            print(f"        {i['issue']}")
            if i["fix"]:
                print(f"        → {i['fix']}")
        if not keep:
            print("没有达到该严重度的条目。")
        print("\n提示：UNKNOWN = 这条没查成，不等于通过；INFO = 已知事实或已声明例外。")

    # 门禁汇总：单一判定，避免“一堆数字自己解读”
    verdict = None
    if args.gate:
        total = dict(counts)
        if args.netlist:
            if erc is None:
                total["UNKNOWN"] = total.get("UNKNOWN", 0) + 1
            else:
                _merge_counts(total, erc)
        if total.get("FAIL"):
            verdict = "BLOCKED"
        elif total.get("UNKNOWN") or (args.netlist and erc is None):
            verdict = "INCOMPLETE"
        elif total.get("WARN"):
            verdict = "REVIEW"
        else:
            verdict = "PASS"

        if not args.json:
            print("\n=== 门禁汇总 ===")
            print(f"{'ERC':<10} FAIL {erc.get('FAIL', 0) if erc else '—'} / "
                  f"WARN {erc.get('WARN', 0) if erc else '—'} / INFO {erc.get('INFO', 0) if erc else '—'}"
                  + ("" if erc else ("  （未提供网表，ERC 未跑）" if not args.netlist else "  （ERC 调用失败）")))
            print(f"{'PCB':<10} FAIL {counts['FAIL']} / WARN {counts['WARN']} / "
                  f"UNKNOWN {counts['UNKNOWN']} / INFO {counts['INFO']}")
            print(f"{'判定':<10} {verdict}")
            explain = {
                "BLOCKED": "有 FAIL，物理上不能投板。",
                "INCOMPLETE": "没有 FAIL，但有检查没跑成 —— 这不等于通过。",
                "REVIEW": "无 FAIL 无 UNKNOWN，WARN 需人看图确认。",
                "PASS": "全部检查跑完且无违规。",
            }[verdict]
            print(f"           {explain}")

    if verdict:
        return {"BLOCKED": 1, "INCOMPLETE": 2}.get(verdict, 0)
    if args.strict and counts["FAIL"] > 0:
        return 1
    if args.require_known and (counts["UNKNOWN"] > 0 or (args.netlist and erc is None)):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
