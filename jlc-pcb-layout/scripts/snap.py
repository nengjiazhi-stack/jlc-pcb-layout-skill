#!/usr/bin/env python3
"""把 EDA 回读的三份 JSON 拼成 check.py 要的 snapshot。

为什么必须是三个来源
--------------------
`components-list` 的输出里没有走线，`line-list` 里没有器件。早期文档从
`components-list` 的响应里取 `traces`（`comps.get("traces", [])`），拿到的一直是空列表，
于是 `check_traces()` 整段被跳过，3W / 直角 / 悬空 / 线宽一致性从来没跑过 ——
输出里只有一条 `FAB-DATA` UNKNOWN，很容易被当成"没问题"。

这个脚本的作用就是把"静默跳过"变成"硬报错"：没有 `--lines` 就退出码 2，
不生成一个看起来正常的空快照。

用法
----
    easyeda pcb components-list --include-bbox --include-pads --json > comps.json
    easyeda pcb line-list --json > lines.json
    easyeda pcb via-list  --json > vias.json
    easyeda pcb outline-get --json > outline.json

    python scripts/snap.py --components comps.json --lines lines.json \
        --vias vias.json --outline outline.json -o snapshot.json

字段名以 `easyeda actions` 的实际输出为准。这里只做常见别名归一化，
对不上就报出缺哪个字段、属于哪个器件，不猜。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WRAP_KEYS = ("data", "result", "items", "list", "records", "payload")
COORD_KEYS_X = ("x", "cx", "centerX", "posX")
COORD_KEYS_Y = ("y", "cy", "centerY", "posY")


def _load(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"找不到文件：{path}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} 不是合法 JSON：{e}")


def _rows(obj, *names):
    """从一个响应里挖出对象数组。容忍 {name:[...]} / {data:[...]} / 直接是数组。"""
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return []
    for k in names:
        v = obj.get(k)
        if isinstance(v, list):
            return v
    for k in WRAP_KEYS:
        v = obj.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for n in names:
                if isinstance(v.get(n), list):
                    return v[n]
            for w in WRAP_KEYS:
                if isinstance(v.get(w), list):
                    return v[w]
    return []


def _pick(row: dict, *names, default=None):
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return default


def _num(row: dict, names, where: str, what: str):
    v = _pick(row, *names)
    if v is None:
        raise SystemExit(f"{where} 缺少 {what}（试过字段名 {', '.join(names)}）")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise SystemExit(f"{where} 的 {what} 不是数字：{v!r}")


START_PAIRS = [
    ("x1", "y1"), ("x1_mil", "y1_mil"), ("startX", "startY"), ("start_x", "start_y"),
    ("fromX", "fromY"), ("from_x", "from_y"), ("beginX", "beginY"), ("sx", "sy"),
]
END_PAIRS = [
    ("x2", "y2"), ("x2_mil", "y2_mil"), ("endX", "endY"), ("end_x", "end_y"),
    ("toX", "toY"), ("to_x", "to_y"), ("finishX", "finishY"), ("ex", "ey"),
]
POINT_OBJS = {"start": ("from", "start", "p1", "begin"), "end": ("to", "end", "p2", "finish")}


def _point(row: dict, kind: str, where: str):
    """取一对坐标，支持 {startX,startY}、{x1,y1}、{from:{x,y}} 三种写法。"""
    for key in POINT_OBJS[kind]:
        d = row.get(key)
        if isinstance(d, dict) and d.get("x") is not None and d.get("y") is not None:
            return float(d["x"]), float(d["y"])
    pairs = START_PAIRS if kind == "start" else END_PAIRS
    for kx, ky in pairs:
        if row.get(kx) is not None and row.get(ky) is not None:
            try:
                return float(row[kx]), float(row[ky])
            except (TypeError, ValueError):
                raise SystemExit(f"{where} 的坐标不是数字：{row[kx]!r}, {row[ky]!r}")
    tried = [a for a, _ in pairs] + [f"{k}:{{x,y}}" for k in POINT_OBJS[kind]]
    raise SystemExit(f"{where} 缺少{('起点' if kind == 'start' else '终点')}坐标"
                     f"（试过 {', '.join(tried)}）")



def _bbox_of_outline(obj):
    pts = obj.get("points") if isinstance(obj, dict) else obj
    if isinstance(pts, dict):
        pts = pts.get("points")
    if not isinstance(pts, list) or len(pts) < 3:
        raise SystemExit("outline 里没有 points 数组（至少 3 个点）")
    xs, ys = [], []
    for p in pts:
        if isinstance(p, dict):
            xs.append(float(p["x"]))
            ys.append(float(p["y"]))
        else:
            xs.append(float(p[0]))
            ys.append(float(p[1]))
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def norm_components(raw):
    out, bad = [], []
    for i, c in enumerate(raw):
        ref = _pick(c, "designator", "ref", "refDes", "name")
        where = f"第 {i + 1} 个器件" + (f"（{ref}）" if ref else "")
        if not ref:
            bad.append(f"{where} 缺 designator")
            continue
        try:
            x = _num(c, COORD_KEYS_X, where, "x")
            y = _num(c, COORD_KEYS_Y, where, "y")
        except SystemExit as e:
            bad.append(str(e))
            continue
        w = _pick(c, "w", "width")
        h = _pick(c, "h", "height")
        bbox = _pick(c, "bbox")
        if isinstance(bbox, dict):
            w = w if w is not None else bbox.get("w", bbox.get("width"))
            h = h if h is not None else bbox.get("h", bbox.get("height"))
        pads = []
        for p in _pick(c, "pads", "pins") or []:
            if not isinstance(p, dict):
                continue
            px, py = _pick(p, *COORD_KEYS_X), _pick(p, *COORD_KEYS_Y)
            if px is None or py is None:
                continue
            pads.append({"x": float(px), "y": float(py),
                         "net": _pick(p, "net", "netName", "net_name") or ""})
        rec = {"designator": str(ref), "layer": int(_pick(c, "layer", "layerId", default=1)),
               "x": x, "y": y,
               "rotation": float(_pick(c, "rotation", "angle", default=0) or 0),
               "footprint": _pick(c, "footprint", "footprintName", "package")}
        if w is not None:
            rec["w"] = float(w)
        if h is not None:
            rec["h"] = float(h)
        if pads:
            rec["pads"] = pads
        if _pick(c, "bga") is not None:
            rec["bga"] = bool(_pick(c, "bga"))
        out.append(rec)
    if bad:
        raise SystemExit("器件映射失败：\n  " + "\n  ".join(bad[:10]))
    return out


def norm_lines(raw):
    out, bad, no_width = [], [], 0
    for i, t in enumerate(raw):
        net = _pick(t, "net", "netName", "net_name")
        where = f"第 {i + 1} 段走线" + (f"（{net}）" if net else "")
        if not net:
            bad.append(f"{where} 缺 net")
            continue
        try:
            x1, y1 = _point(t, "start", where)
            x2, y2 = _point(t, "end", where)
        except SystemExit as e:
            bad.append(str(e))
            continue
        width = _pick(t, "width", "lineWidth", "line_width", "w")
        if width is None:
            no_width += 1
            width = 0
        try:
            width = float(width)
        except (TypeError, ValueError):
            raise SystemExit(f"{where} 的线宽不是数字：{width!r}")
        out.append({"net": str(net), "layer": int(_pick(t, "layer", "layerId", default=1)),
                    "x1": x1, "y1": y1, "x2": x2, "y2": y2, "width": width})
    if bad:
        raise SystemExit("走线映射失败：\n  " + "\n  ".join(bad[:10]))
    return out, no_width


def norm_vias(raw):
    out = []
    for v in raw:
        x, y = _pick(v, *COORD_KEYS_X), _pick(v, *COORD_KEYS_Y)
        if x is None or y is None:
            continue
        out.append({"net": str(_pick(v, "net", "netName") or ""),
                    "x": float(x), "y": float(y),
                    "hole": float(_pick(v, "hole", "drill", "holeSize", default=0) or 0),
                    "diameter": float(_pick(v, "diameter", "padDiameter", default=0) or 0)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EDA 回读 → check.py 的 snapshot")
    ap.add_argument("--components", required=True, help="components-list 的输出")
    ap.add_argument("--lines", help="line-list 的输出（缺这份就不生成快照）")
    ap.add_argument("--vias", help="via-list 的输出（可选）")
    ap.add_argument("--outline", help="outline-get 的输出（优先用它算板框）")
    ap.add_argument("--width", type=float, help="板宽 mil（没有 outline 时必给）")
    ap.add_argument("--height", type=float, help="板高 mil（没有 outline 时必给）")
    ap.add_argument("--copper", type=int, default=2, help="铜层数，默认 2")
    ap.add_argument("--thickness", type=float, default=1.6, help="板厚 mm，默认 1.6")
    ap.add_argument("--allow-empty", action="store_true",
                    help="确实没有走线时才加：允许 traces 为空（check.py 会记 FAB-DATA UNKNOWN）")
    ap.add_argument("-o", "--output", help="输出路径，默认 stdout")
    args = ap.parse_args(argv)

    if not args.lines and not args.allow_empty:
        print("拒绝生成快照：没有提供 --lines（走线数据）。", file=sys.stderr)
        print("  components-list 不返回走线，必须单独导出：", file=sys.stderr)
        print("    easyeda pcb line-list --json > lines.json", file=sys.stderr)
        print("  若这块板确实还没布线，加 --allow-empty 显式声明；"
              "check.py 会记 FAB-DATA UNKNOWN，门禁判 INCOMPLETE。", file=sys.stderr)
        return 2

    comps = norm_components(_rows(_load(args.components), "components", "parts", "devices"))
    if not comps:
        raise SystemExit(f"{args.components} 里没有解析到任何器件")

    traces, no_width = [], 0
    if args.lines:
        traces, no_width = norm_lines(_rows(_load(args.lines), "lines", "traces", "tracks"))
    vias = norm_vias(_rows(_load(args.vias), "vias", "holes")) if args.vias else []

    if args.outline:
        _, _, bw, bh = _bbox_of_outline(_load(args.outline))
    elif args.width and args.height:
        bw, bh = args.width, args.height
    else:
        raise SystemExit("需要 --outline，或同时给 --width 与 --height（板框尺寸）。\n"
                         "  边距类规则（PL-FID-SPREAD / 板边带）依赖它，猜一个会误报。")

    snap = {"board": {"width_mil": round(bw, 1), "height_mil": round(bh, 1),
                      "copper_layers": args.copper,
                      "thickness_mm": args.thickness, "wave_solder": False},
            "components": comps, "traces": traces, "vias": vias}

    text = json.dumps(snap, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)

    nets = {t["net"] for t in traces if t["net"]}
    print(f"\nsnapshot {'已写入 ' + args.output if args.output else '已输出'}", file=sys.stderr)
    print(f"  板框 {bw:.0f} × {bh:.0f} mil / {args.copper} 层 / {args.thickness}mm", file=sys.stderr)
    print(f"  器件 {len(comps)} / 走线 {len(traces)}（网络 {len(nets)}） / 过孔 {len(vias)}", file=sys.stderr)
    if no_width:
        print(f"  ⚠ {no_width} 段走线没有 width 字段 → 3W 间距检查对它们失效", file=sys.stderr)
    if not traces:
        print("  ⚠ traces 为空：check.py 的走线判据（3W/直角/悬空/线宽）不会跑，"
              "门禁会判 INCOMPLETE", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
