#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""schem.py -- 客户需求 → 原理图网表 的生成与校验器

和 rules.py 的分工：rules.py 管 PCB 工艺数值，schem.py 管电路结构与原理图数值。
所有数值来自 assets/constraints.json → schematic（唯一真源）；
模块电路来自 assets/circuit-blocks.json（15 个功能块）。

用法：
  python schem.py blocks                                   # 看模块库
  python schem.py spec --req examples/req.example.txt -o spec.json
  python schem.py spec --text "12V 输入，3.3V，2 路继电器，1 路 RS485" -o spec.json
  python schem.py calc led --vcc 3.3 --vf 2.0 --i-ma 5
  python schem.py calc crystal --cl 12 --cstray 3
  python schem.py calc i2c --vcc 3.3 --mode fast --cb-pf 200
  python schem.py calc divider --vin 12 --vout 3.3 --r-bottom 10k
  python schem.py calc ldo --vin 12 --vout 3.3 --iout 0.3
  python schem.py calc rs485-bias --vcc 5 --rt 120
  python schem.py calc usb-cc
  python schem.py calc tvs --vop 12
  python schem.py calc relay --vdrive 3.3 --coil-ma 70 --beta 100
  python schem.py calc buck --vin 12 --vout 5 --iout 1 --fsw-khz 500
  python schem.py netlist spec.json -o netlist.json [--report]
  python schem.py erc netlist.json [--min-severity WARN] [--json] [--strict]
  python schem.py bom netlist.json [-o bom.csv]
  python schem.py bind netlist.json readback.json -o netlist.bound.json
  python schem.py playbook netlist.json --js out.js [--allow-missing]

红线：本文件不产生任何具体料号（LCSC 编号）。料号必须由人选型确认后写进 netlist 的 lcsc 字段，
     否则 ERC 会报 SCH-BINDING，playbook 拒绝生成落笔代码。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = [HERE.parent / "assets", HERE / "assets"]


# ---------------------------------------------------------------- 基础设施

def load_asset(name: str) -> dict:
    for d in ASSETS:
        p = d / name
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit(f"找不到 assets/{name} —— 请确认 skill 目录完整")


def load_constraints() -> dict:
    return load_asset("constraints.json")


def load_chips() -> dict:
    return load_asset("chip-variants.json").get("variants", {})


def load_blocks() -> dict:
    return load_asset("circuit-blocks.json")["blocks"]


def dump(obj, path: str | None, as_json: bool = True):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
        print(f"已写入 {path}")
    elif as_json:
        print(text)


def parse_r(value) -> float | None:
    """把 '10k' / '4.7k' / '680' / '1M' 解析成欧姆。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("Ω", "").replace("ohm", "").replace("R", "")
    m = re.fullmatch(r"([0-9.]+)\s*([kKmM]?)", s)
    if not m:
        return None
    x = float(m.group(1))
    return x * {"": 1, "k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6}[m.group(2)]


def parse_c(value) -> float | None:
    """把 '100nF' / '1uF' / '4.7nF' / '22uF' / '10pF' 解析成法拉。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("µ", "u").replace("μ", "u")
    m = re.fullmatch(r"([0-9.]+)\s*([pnuμm]?)F", s, re.I)
    if not m:
        return None
    x = float(m.group(1))
    unit = m.group(2).lower().replace("μ", "u").replace("µ", "u")
    return x * {"": 1, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3}[unit]


def sub_str(s: str, ctx: dict) -> str:
    def rep(m):
        k = m.group(1)
        if k not in ctx:
            raise KeyError(f"参数 {k} 未定义（可用：{', '.join(sorted(ctx))}）")
        v = ctx[k]
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    return re.sub(r"\$\{([A-Za-z0-9_]+)\}", rep, s)


def sub_any(v, ctx: dict):
    if isinstance(v, str):
        m = re.fullmatch(r"\$\{([A-Za-z0-9_]+)\}", v)
        if m and m.group(1) in ctx:
            return ctx[m.group(1)]
        return sub_str(v, ctx)
    if isinstance(v, dict):
        return {k: sub_any(x, ctx) for k, x in v.items()}
    if isinstance(v, list):
        return [sub_any(x, ctx) for x in v]
    return v


PREFIX = {
    "IC": "U", "MCU": "U", "R": "R", "C": "C", "L": "L", "D": "D", "LED": "D",
    "TVS": "D", "Q": "Q", "Y": "Y", "J": "J", "F": "F", "K": "K", "SW": "SW",
    "BZ": "BZ", "TP": "TP", "FB": "FB", "BAT": "BT",
}

GND_ALIASES = ("GND", "AGND", "DGND", "PGND", "EARTH")


class Allocator:
    def __init__(self):
        self.n: dict[str, int] = {}

    def take(self, prefix: str) -> str:
        self.n[prefix] = self.n.get(prefix, 0) + 1
        return f"{prefix}{self.n[prefix]}"

    def peek(self, prefix: str) -> int:
        return self.n.get(prefix, 0)


# ---------------------------------------------------------------- 需求 → 设计规格

QTY_WORDS = ("路", "个", "位", "颗", "组", "通道", "片", "只", "套", "块")


def _qty_before(text: str, idx: int) -> int | None:
    """在关键词出现位置前 10 个字符内找数量，如「3 路继电器」→ 3。"""
    head = text[max(0, idx - 10):idx]
    m = re.search(r"(\d+)\s*(?:" + "|".join(QTY_WORDS) + r")?\s*(?:的|个)?\s*$", head)
    return int(m.group(1)) if m else None


def parse_requirement(text: str) -> dict:
    """按关键词把需求拆成功能块清单。只做结构识别，不猜参数。"""
    blocks = load_blocks()
    text_l = text.lower()
    picked: list[dict] = []
    for bid, b in blocks.items():
        hits, qty = 0, None
        for kw in b["keywords"]:
            start = 0
            while True:
                i = text_l.find(kw.lower(), start)
                if i < 0:
                    break
                hits += 1
                q = _qty_before(text_l, i)
                if q and qty is None:
                    qty = q
                start = i + len(kw)
        if hits:
            count = max(qty or 1, 1) if qty else 1
            picked.append({"type": bid, "count": count, "matched_keywords": hits})

    # 供电电压：取文本里出现的最大 V 值作为输入母线
    volts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*[vV]\b", text)]
    vin = max(volts) if volts else 12.0

    rails = [{"name": "VIN", "v": vin, "i_a": None, "from": "input"}]

    def rail_v_of(name: str) -> float:
        m = re.fullmatch(r"(\d)V(\d)", name, re.I)
        if m:
            return float(f"{m.group(1)}.{m.group(2)}")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)V", name, re.I)
        if m:
            return float(m.group(1))
        return 3.3

    producers, consumers = [], []
    for p in picked:
        b = blocks[p["type"]]
        default_rail = b["params"].get("rail", {}).get("default")
        if not default_rail or any(r["name"] == default_rail for r in rails):
            continue
        rec = {"name": default_rail, "v": rail_v_of(default_rail), "i_a": None,
               "from": "VIN" if b["category"] == "power" else "assumed"}
        rails.append(rec)
        (producers if b["category"] == "power" else consumers).append(rec["name"])

    types = {p["type"] for p in picked}
    if "usb_typec_ufp" in types and not any(r["name"] == "VBUS" for r in rails):
        rails.append({"name": "VBUS", "v": 5.0, "i_a": None, "from": "input"})

    review, questions = [], [
        "主控型号与封装？（决定引脚数与可用外设，netlist 里 MCU 的 pin 映射要按实际 datasheet 绑定）",
        "各路电源的实际最大电流？（决定 LDO 改不改 buck、线宽与铜厚）",
        "外部接口的连接器型号与针序？（决定 J 的封装与引脚定义）",
        "结构尺寸与安装方式？（原理图不受影响，但会反向约束器件选型与封装）",
        "工作温度范围与认证要求？（工业级/车规会改变器件降额与保护等级）",
    ]

    assumed = [r["name"] for r in rails if r.get("from") == "assumed"]
    if assumed:
        questions.insert(0, f"这些电源没有对应的稳压模块，电从哪来？{', '.join(assumed)}"
                            "（补一个 power_ldo / power_buck，或说明由外部直接供给）")
    if not any(p["type"].startswith("power_") and p["type"] != "power_protect" for p in picked):
        review.append("没有识别到稳压模块：若板上没有电源树，确认是否整板由外部稳压供电")
    if not any(p["type"] == "power_protect" for p in picked):
        review.append("没有识别到输入保护：工业现场建议至少加保险 + TVS（power_protect）")

    if {"relay_drive", "mosfet_load"} <= types:
        review.append("同时命中 relay_drive 与 mosfet_load：客户若要『继电器驱动电磁阀』，"
                      "MOSFET 块是多余的，删掉 mosfet_load 再生成网表")

    return {
        "project": "auto-from-requirement",
        "generated_by": "scripts/schem.py spec",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source_text": text.strip(),
        "rails": rails,
        "blocks": picked,
        "assumptions": [
            f"输入母线按文本中最大电压取 {vin}V；若实际是 24V/48V 系统必须改，TVS 与间距都会变",
            "未指定电流 → 各 rail 的 i_a 留空，ERC 会报 SCH-DATA 提示补算",
            "未指定主控 → mcu_min 的引脚映射保持功能名，落笔前必须绑定实际引脚号",
        ],
        "review": review,
        "open_questions": questions,
        "_next": "填完 rails 的 i_a 与各块 params 后跑 netlist；料号确认后补 lcsc 再跑 playbook",
    }


def cmd_spec(args) -> int:
    if args.req:
        text = Path(args.req).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        raise SystemExit("需要 --req <文件> 或 --text \"需求描述\"")
    spec = parse_requirement(text)
    if args.project:
        spec["project"] = args.project
    dump(spec, args.output)
    return 0


# ---------------------------------------------------------------- 计算器

def _out(o: dict, as_json: bool, fmt) -> int:
    print(json.dumps(o, ensure_ascii=False, indent=2) if as_json else fmt(o))
    return 0


def cmd_calc(args) -> int:
    c = load_constraints()["schematic"]
    kind = args.kind

    if kind == "led":
        tbl = c["led"]["vf_table_v"]
        vf = args.vf if args.vf is not None else tbl.get((args.color or "red").lower(), 2.0)
        i = (args.i_ma if args.i_ma is not None else c["led"]["i_default_ma"]) / 1000.0
        r = (args.vcc - vf) / i
        p_r = i * i * r
        o = {
            "kind": "led", "input": {"vcc_v": args.vcc, "vf_v": vf, "i_ma": i * 1000},
            "formula": "R=(Vcc−Vf)/I，Pr=I²R",
            "r_ohm": round(r, 1), "r_nearest": _nearest_r(r),
            "power_r_mw": round(p_r * 1000, 2),
            "verdict": "OK" if r > 0 else "INVALID",
            "note": [f"Vf={vf}V 取 {args.color or 'red'} 典型值，实际以 datasheet 为准",
                     "电阻功耗超过 100mW 要换更大封装或减小电流"],
        }
        return _out(o, args.json, lambda x: "\n".join([
            f"LED 限流：Vcc={x['input']['vcc_v']}V Vf={x['input']['vf_v']}V I={x['input']['i_ma']:.1f}mA",
            f"结论：{x['verdict']}",
            f"限流电阻：{x['r_ohm']}Ω → 取标准值 {x['r_nearest']}Ω",
            f"电阻功耗：{x['power_r_mw']}mW",
            "说明：" + "；".join(x["note"])]))

    if kind == "crystal":
        cl = args.cl if args.cl is not None else c["crystal"]["cl_default_pf"]
        cs = args.cstray if args.cstray is not None else c["crystal"]["cstray_pf_2layer"]
        cap = 2 * (cl - cs)
        o = {
            "kind": "crystal", "input": {"cl_pf": cl, "cstray_pf": cs},
            "formula": "C1=C2=2×(CL−Cstray)",
            "load_cap_pf": round(cap, 1),
            "verdict": "OK" if cap > 0 else "INVALID",
            "note": ["Cstray 含 PCB 走线与芯片引脚电容：2 层取 3pF，4 层以上取 5pF",
                     "算出的值取最近的 E12/E24 标称值；两侧电容必须同值"],
        }
        return _out(o, args.json, lambda x: "\n".join([
            f"晶振负载电容：CL={x['input']['cl_pf']}pF Cstray={x['input']['cstray_pf']}pF",
            f"结论：{x['verdict']}",
            f"C1=C2={x['load_cap_pf']}pF",
            "说明：" + "；".join(x["note"])]))

    if kind == "i2c":
        mode = c["i2c"]["modes"][args.mode]
        cb = args.cb_pf or c["i2c"]["cb_default_pf"]
        vol, iol = c["i2c"]["vol_v"], c["i2c"]["iol_a"]
        r_min = (args.vcc - vol) / iol
        r_max = (mode["tr_max_ns"] * 1e-9) / (0.847 * cb * 1e-12)
        cands = [r for r in c["i2c"]["preferred_series_ohm"] if r_min <= r <= r_max]
        o = {
            "kind": "i2c", "input": {"vcc_v": args.vcc, "mode": args.mode, "freq_hz": mode["freq_hz"],
                                     "cb_pf": cb, "tr_max_ns": mode["tr_max_ns"]},
            "formula": "Rmin=(Vcc−VOL)/IOL；Rmax=tr/(0.847·Cb)",
            "r_min_ohm": round(r_min), "r_max_ohm": round(r_max),
            "candidates_ohm": cands,
            "recommended_ohm": cands[0] if cands else None,
            "verdict": "OK" if cands else ("TIGHT" if r_min <= r_max else "IMPOSSIBLE"),
            "note": [f"VOL={vol}V / IOL={iol * 1000:.0f}mA 决定下限（总线不能被拉不低）",
                     f"tr≤{mode['tr_max_ns']}ns 决定上限（上升沿不够陡信号会烂）",
                     "候选区为空说明总线电容太大或速率太高：降速、缩短总线、或换主动上拉"],
        }
        return _out(o, args.json, lambda x: "\n".join([
            f"I2C 上拉：Vcc={x['input']['vcc_v']}V {x['input']['mode']}({x['input']['freq_hz']}Hz) Cb={x['input']['cb_pf']}pF",
            f"结论：{x['verdict']}",
            f"允许区间：{x['r_min_ohm']}Ω ~ {x['r_max_ohm']}Ω",
            f"推荐：{x['recommended_ohm']}Ω（候选 {x['candidates_ohm']}）",
            "说明：" + "；".join(x["note"])]))

    if kind == "divider":
        rb = parse_r(args.r_bottom) or 10000
        rt = rb * (args.vin - args.vout) / args.vout
        rth = (rt * rb) / (rt + rb)
        i_ua = args.vin / (rt + rb) * 1e6
        lim = c["adc"]["max_source_ohm"]
        o = {
            "kind": "divider", "input": {"vin_v": args.vin, "vout_target_v": args.vout, "r_bottom_ohm": rb},
            "formula": "Rtop=Rbottom×(Vin−Vout)/Vout；Rth=Rtop∥Rbottom",
            "r_top_ohm": round(rt), "r_top_nearest": _nearest_r(rt),
            "r_th_ohm": round(rth, 1), "divider_current_ua": round(i_ua, 2),
            "vout_actual_v": round(args.vin * rb / (rt + rb), 4),
            "verdict": "OK" if rth <= lim else "RTH_TOO_HIGH",
            "note": [f"SAR ADC 要求源阻抗 ≤{lim}Ω，当前 {rth:.0f}Ω",
                     f"分压电流 {i_ua:.1f}µA，建议 ≥{c['adc']['min_divider_current_ua']}µA 才有抗噪余量",
                     "分压点并 100nF 做采样电荷缓冲"],
        }
        return _out(o, args.json, lambda x: "\n".join([
            f"ADC 分压：{x['input']['vin_v']}V → {x['input']['vout_target_v']}V，下臂 {x['input']['r_bottom_ohm']}Ω",
            f"结论：{x['verdict']}",
            f"上臂：{x['r_top_ohm']}Ω → 标准值 {x['r_top_nearest']}Ω",
            f"源阻抗：{x['r_th_ohm']}Ω；分压电流 {x['divider_current_ua']}µA",
            "说明：" + "；".join(x["note"])]))

    if kind == "rc":
        r = parse_r(args.r) or 10000
        cap = parse_c(args.c) or 1e-7
        t = r * cap
        o = {"kind": "rc", "input": {"r_ohm": r, "c_f": cap},
             "formula": "τ=R×C；上电复位时间约 3~5τ",
             "tau_ms": round(t * 1000, 3), "t_3tau_ms": round(t * 3000, 2), "t_5tau_ms": round(t * 5000, 2),
             "verdict": "OK", "note": ["复位时间要覆盖电源上升时间；电源慢时把电容加大或改用专用复位芯片"]}
        return _out(o, args.json, lambda x: f"RC 时间常数 τ={x['tau_ms']}ms（3τ={x['t_3tau_ms']}ms，5τ={x['t_5tau_ms']}ms）")

    if kind == "ldo":
        ic = args.iout
        pd = (args.vin - args.vout) * ic
        ldo = c["ldo"]
        theta = ldo["theta_ja_default_c_per_w"]
        tj = pd * theta + ldo["ambient_c"]
        drop = ldo["dropout_v_default"]
        o = {
            "kind": "ldo", "input": {"vin_v": args.vin, "vout_v": args.vout, "iout_a": ic},
            "formula": "Pd=(Vin−Vout)×Iout；Tj=Pd×θja+Ta",
            "pd_w": round(pd, 3), "tj_est_c": round(tj, 1), "tj_max_c": ldo["tj_max_c"],
            "headroom_v": round(args.vin - args.vout, 2), "dropout_v_assumed": drop,
            "efficiency_pct": round(args.vout / args.vin * 100, 1),
            "verdict": ("TOO_HOT" if tj > ldo["tj_max_c"] else
                        ("MARGINAL" if pd > ldo["pd_warn_w"] else "OK")),
            "note": [f"θja 按 {theta}℃/W 保守估，实际查封装 datasheet",
                     f"压差 {args.vin - args.vout:.1f}V，需大于 dropout {drop}V 才稳得住",
                     f"效率仅 {args.vout / args.vin * 100:.1f}%，压差大时优先 buck"],
        }
        if tj > ldo["tj_max_c"] * 2:
            o["note"].append(f"结温 {tj:.0f}℃ 已远超上限，线性模型外推失真——这个组合物理上就不成立，直接改 buck")
        elif o["verdict"] != "OK":
            o["note"].append("结温超限或功耗偏大 → 改 buck、降电流、或加铜箔散热")
        return _out(o, args.json, lambda x: "\n".join([
            f"LDO 热校核：{x['input']['vin_v']}V→{x['input']['vout_v']}V @ {x['input']['iout_a']}A",
            f"结论：{x['verdict']}",
            f"功耗：{x['pd_w']}W；估算结温 {x['tj_est_c']}℃（上限 {x['tj_max_c']}℃）",
            f"效率：{x['efficiency_pct']}%",
            "说明：" + "；".join(x["note"])]))

    if kind == "rs485-bias":
        rt = args.rt or c["rs485"]["rt_ohm"]
        vmin = c["rs485"]["vab_idle_min_v"]
        rb_max = rt * (args.vcc / vmin - 1) / 2
        lo, hi = c["rs485"]["bias_ohm_range"]
        rec = min(rb_max, hi)
        o = {
            "kind": "rs485-bias", "input": {"vcc_v": args.vcc, "rt_ohm": rt},
            "formula": "Vab=Vcc×Rt/(2Rb+Rt) ≥ 200mV → Rb ≤ Rt×(Vcc/0.2−1)/2",
            "rb_max_ohm": round(rb_max), "recommend_ohm": round(rec),
            "range_from_table": [lo, hi],
            "verdict": "OK" if rb_max >= lo else "TIGHT",
            "note": ["偏置电阻越大越省电，但抗噪余量变小；两端节点只需一处偏置，别每个节点都加",
                     "A 上拉到 Vcc、B 下拉到 GND，方向反了会把空闲态判成 1"],
        }
        return _out(o, args.json, lambda x: "\n".join([
            f"RS485 失效保护偏置：Vcc={x['input']['vcc_v']}V Rt={x['input']['rt_ohm']}Ω",
            f"结论：{x['verdict']}",
            f"Rb 上限：{x['rb_max_ohm']}Ω → 推荐 {x['recommend_ohm']}Ω（表内区间 {x['range_from_table']}）",
            "说明：" + "；".join(x["note"])]))

    if kind == "usb-cc":
        usb = c["usb_c"]
        rd = usb["rd_ohm"]
        i = usb["vbus_min_v"] / rd * 1000
        o = {"kind": "usb-cc", "formula": "UFP 侧 CC 各接 Rd=5.1kΩ±5% 下拉",
             "rd_ohm": rd, "tolerance_pct": usb["rd_tol_pct"],
             "current_per_cc_ma": round(i, 3),
             "verdict": "OK",
             "note": ["CC1、CC2 必须各接一颗，缺一颗 source 不识别（线插上不来电）",
                      "只做受电不要放 Rp；放了会变成 DRP，行为不可预期",
                      "D+/D− 串 0~22Ω 并就近加 ESD"]}
        return _out(o, args.json, lambda x: "\n".join([
            "USB Type-C UFP：",
            f"Rd={x['rd_ohm']}Ω ±{x['tolerance_pct']}%，每颗 CC 上电流约 {x['current_per_cc_ma']}mA",
            "说明：" + "；".join(x["note"])]))

    if kind == "tvs":
        mult = c["tvs"]["vrwm_mult"]
        need = args.vop * mult
        std = [5, 6.5, 12, 15, 18, 24, 30, 33, 36, 48, 58, 70]
        pick = next((s for s in std if s >= need), None)
        o = {"kind": "tvs", "input": {"vop_v": args.vop},
             "formula": "VRWM ≥ 1.1×Vop",
             "vrwm_min_v": round(need, 2), "suggest_vrwm_v": pick,
             "verdict": "OK" if pick else "NO_STANDARD_PART",
             "note": ["VRWM 低于工作电压会在正常工作时漏电甚至热击穿",
                      "还要核对钳位电压 VC 低于被保护脚的绝对最大额定值"]}
        return _out(o, args.json, lambda x: "\n".join([
            f"TVS 选型：工作电压 {x['input']['vop_v']}V",
            f"结论：{x['verdict']}",
            f"VRWM ≥ {x['vrwm_min_v']}V → 建议 {x['suggest_vrwm_v']}V 档",
            "说明：" + "；".join(x["note"])]))

    if kind == "relay":
        rel = c["relay"]
        ic_ma = args.coil_ma * rel["ic_margin"] / 1000
        ib = ic_ma / args.beta * rel["base_overdrive"]
        rb = (args.vdrive - rel["vbe_v"]) / ib
        o = {"kind": "relay", "input": {"vdrive_v": args.vdrive, "coil_ma": args.coil_ma, "beta": args.beta},
             "formula": "Ic≥3×Icoil；Ib=Ic/(β)×3；Rb=(Vdrive−Vbe)/Ib",
             "ic_required_ma": round(ic_ma * 1000, 1),
             "ib_required_ma": round(ib * 1000, 3),
             "base_r_ohm": round(rb), "base_r_nearest": _nearest_r(rb),
             "diode_vr_min_v": round(args.vdrive * rel["diode_vr_mult"], 1),
             "diode_if_min_ma": round(args.coil_ma * rel["diode_if_mult"], 1),
             "verdict": "OK",
             "note": ["β 按最小值的 1/3 估（饱和深度），datasheet 的 hFE 是典型值不能直接用",
                      "续流二极管反向耐压 ≥2×电源，正向电流 ≥1.5×线圈电流",
                      "基极必须加下拉，防止上电瞬间 MCU 未初始化时误吸合"]}
        return _out(o, args.json, lambda x: "\n".join([
            f"继电器驱动：Vdrive={x['input']['vdrive_v']}V 线圈 {x['input']['coil_ma']}mA β={x['input']['beta']}",
            "结论：OK",
            f"三极管 Ic 需求 ≥{x['ic_required_ma']}mA，Ib ≥{x['ib_required_ma']}mA",
            f"基极电阻：{x['base_r_ohm']}Ω → 标准值 {x['base_r_nearest']}Ω",
            f"续流二极管：VR ≥{x['diode_vr_min_v']}V，If ≥{x['diode_if_min_ma']}mA",
            "说明：" + "；".join(x["note"])]))

    if kind == "buck":
        dI = 0.3 * args.iout
        fsw = args.fsw_khz * 1000
        L = (args.vin - args.vout) * args.vout / (args.vin * fsw * dI) * 1e6
        dV = 0.01 * args.vout
        cout = dI / (8 * fsw * dV) * 1e6
        o = {"kind": "buck", "input": {"vin_v": args.vin, "vout_v": args.vout, "iout_a": args.iout, "fsw_khz": args.fsw_khz},
             "formula": "L=(Vin−Vout)·Vout/(Vin·fsw·ΔI)，ΔI=0.3·Iout；Cout=ΔI/(8·fsw·ΔV)",
             "delta_i_a": round(dI, 3),
             "inductor_uh": round(L, 2), "inductor_isat_min_a": round(args.iout + dI / 2, 2),
             "cout_uf_min": round(cout, 2),
             "verdict": "OK" if args.vout < args.vin else "INVALID",
             "note": ["电感饱和电流 ≥ Iout+ΔI/2，否则大电流时感量塌陷",
                      "输出电容按纹波 1% 估，实际要叠加 ESR 与陶瓷降容",
                      f"占空比约 {args.vout / args.vin * 100:.1f}%"]}
        return _out(o, args.json, lambda x: "\n".join([
            f"Buck 参数：{x['input']['vin_v']}V→{x['input']['vout_v']}V @ {x['input']['iout_a']}A {x['input']['fsw_khz']}kHz",
            f"结论：{x['verdict']}",
            f"电感：{x['inductor_uh']}µH（饱和电流 ≥{x['inductor_isat_min_a']}A）",
            f"输出电容：≥{x['cout_uf_min']}µF",
            "说明：" + "；".join(x["note"])]))

    raise SystemExit(f"未知计算器 {kind}（可用 led/crystal/i2c/divider/rc/ldo/rs485-bias/usb-cc/tvs/relay/buck）")


def _nearest_r(x: float) -> float:
    """取最近的 E24 标称值（1.0~9.1）×10^n。"""
    if x <= 0:
        return 0.0
    e24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
           3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]
    exp = math.floor(math.log10(x))
    base = 10.0 ** exp
    cands = [v * base for v in e24] + [1.0 * base * 10]
    return min(cands, key=lambda v: abs(v - x))


# ---------------------------------------------------------------- 网表生成

TVS_STEPS = [5, 6.5, 12, 15, 18, 24, 30, 33, 36, 48, 58, 70]


def _tvs_vrwm(vop: float, mult: float = 1.1) -> float:
    """按 VRWM ≥ mult×工作电压 取下一个标准档，避免直接用 vop 标型号（24V 用 SMBJ24A 会漏电）。"""
    need = vop * mult
    for v in TVS_STEPS:
        if v >= need:
            return v
    return TVS_STEPS[-1]


def _apply_variant(part: dict, vid: str, v: dict) -> list[str]:
    """把芯片实例并进一个器件：料号、引脚号、块级布局约束。

    只填确定对得上的脚；对不上的一律不猜，交给 pin_no_partial 让引擎报 UNKNOWN。
    返回该实例的块级布局约束列表。
    """
    if v.get("blocked_by"):
        raise SystemExit(
            f"芯片 {vid} 不能套进块 {part['block']}：{v['blocked_by']}")
    logical = list(part["pins"])
    known = {k: str(vv) for k, vv in (v.get("pins") or {}).items() if k in logical}
    part["variant"] = vid
    part["variant_name"] = v.get("name") or vid
    if v.get("mpn"):
        part["mpn"] = v["mpn"]
    if v.get("package"):
        part["package"] = v["package"]
    if v.get("lcsc"):
        part["lcsc"] = v["lcsc"]
        part["lcsc_status"] = v.get("lcsc_status", "sourced")
    else:
        part["lcsc_status"] = v.get("lcsc_status", "need_select")
    if known:
        part["pin_no"] = known
    missing = [k for k in logical if k not in known]
    if missing:
        part["pin_no_partial"] = missing
    if v.get("caveats"):
        part["caveats"] = list(v["caveats"])
    src = {"origin": v.get("origin", ""), "source": v.get("source", ""), "verified": v.get("verified", "")}
    part["provenance"] = {k: x for k, x in src.items() if x}
    return list(v.get("pcb_layout") or [])


def build_netlist(spec: dict, chip_map: dict | None = None) -> dict:
    blocks = load_blocks()
    cons = load_constraints()["schematic"]
    variants = load_chips() if chip_map else {}
    alloc = Allocator()
    parts: list[dict] = []
    layout_cons: list[dict] = []
    assumptions = list(spec.get("assumptions", []))
    rail_v = {r["name"]: r.get("v") for r in spec.get("rails", [])}
    rail_i = {r["name"]: r.get("i_a") for r in spec.get("rails", [])}
    rail_v.setdefault("GND", 0.0)

    for bspec in spec.get("blocks", []):
        btype = bspec["type"]
        if btype not in blocks:
            raise SystemExit(f"未知模块 {btype}（可用：{', '.join(blocks)}）")
        blk = blocks[btype]
        count = int(bspec.get("count", 1))
        user_params = bspec.get("params", {})
        vid = (chip_map or {}).get(btype)
        if vid and vid not in variants:
            raise SystemExit(f"未知芯片实例 {vid}（可用：{', '.join(sorted(variants))}）")
        if vid and variants[vid].get("for_block") != btype:
            raise SystemExit(
                f"芯片 {vid} 是为块 {variants[vid].get('for_block')} 准备的，不能套进 {btype}")

        for idx in range(count):
            tag = blk["tag"] if count == 1 else f"{blk['tag']}{idx + 1}"
            ctx = {"tag": tag, "count": count, "index": idx + 1}
            for pk, pv in blk.get("params", {}).items():
                ctx[pk] = pv.get("default")
            for pk, v in user_params.items():
                ptype = blk.get("params", {}).get(pk, {}).get("type")
                if count > 1 and ptype == "net" and isinstance(v, str) and not v.startswith("#"):
                    v = f"{v}{idx + 1}"   # 3 路继电器给同一个 ctrl 会把三路并联，必须分号
                ctx[pk] = v
            # derived 必须排在用户参数之后：先拿默认值推出 15V，用户给 24V 就白推了
            for pk, pv in blk.get("params", {}).items():
                if pv.get("derived") == "tvs_from_vop" and isinstance(ctx.get("vop"), (int, float)):
                    ctx[pk] = _tvs_vrwm(ctx["vop"])

            rail = ctx.get("rail")
            if isinstance(rail, str) and rail not in rail_v and btype != "power_protect":
                raise SystemExit(
                    f"模块 {btype} 引用了未定义的电源 {rail}。请在 spec.rails 里声明，或改 params.rail。"
                    f"已声明：{', '.join(rail_v)}")

            for pt in blk["parts"]:
                if pt.get("optional") and not ctx.get("term", True):
                    continue
                ref = alloc.take(PREFIX.get(pt["kind"], "U"))
                pins = {}
                for pid, net in pt["pins"].items():
                    n = sub_str(net, ctx)
                    if n.startswith("#"):
                        n = f"{tag}_{n[1:]}"
                    pins[pid] = n
                part = {
                    "ref": ref,
                    "kind": pt["kind"],
                    "value": sub_any(pt["value"], ctx),
                    "block": btype,
                    "instance": tag,
                    "pins": pins,
                    "binding": pt.get("binding", "none"),
                    "lcsc": pt.get("lcsc"),
                }
                if pt.get("note"):
                    part["note"] = sub_str(pt["note"], ctx)
                if pt.get("optional"):
                    part["optional"] = True
                if vid and pt["kind"] == variants[vid].get("fills_kind"):
                    for c in _apply_variant(part, vid, variants[vid]):
                        layout_cons.append({
                            "block": btype, "instance": tag, "ref": ref,
                            "variant": vid, **{k: c.get(k) for k in
                                               ("rule", "target", "constraint", "value", "severity")},
                        })
                parts.append(part)

    # 汇总网络
    nets: dict[str, dict] = {}
    for p in parts:
        for pid, net in p["pins"].items():
            rec = nets.setdefault(net, {"pins": [], "class": "signal", "voltage": None})
            rec["pins"].append(f"{p['ref']}.{pid}")

    for name, rec in nets.items():
        if name in GND_ALIASES:
            rec["class"], rec["voltage"] = "ground", 0.0
        elif name in rail_v:
            rec["class"] = "power"
            rec["voltage"] = rail_v[name]
        elif name in ("VIN_IN", "VBUS"):
            rec["class"] = "power"
            rec["voltage"] = rail_v.get(name)

    nl = {
        "project": spec.get("project", "untitled"),
        "generated_by": "scripts/schem.py netlist",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "rails": spec.get("rails", []),
        "parts": parts,
        "nets": dict(sorted(nets.items())),
        "assumptions": assumptions,
        "open_questions": spec.get("open_questions", []),
        "stats": {
            "parts": len(parts),
            "nets": len(nets),
            "blocks": len(spec.get("blocks", [])),
            "binding_required": sum(1 for p in parts if p["binding"] == "required"),
            "binding_pending": sum(1 for p in parts if p["binding"] == "required" and not p.get("lcsc")),
            "pins_unmapped": sum(len(p.get("pin_no_partial") or []) for p in parts),
            "variants_applied": sum(1 for p in parts if p.get("variant")),
        },
        "layout_constraints": layout_cons,
    }
    return nl


def _parse_chip_map(items) -> dict:
    """--chip rs485=sp3485 或 --chip sp3485（后者按 variant 的 for_block 自动定位）。"""
    variants = load_chips()
    out = {}
    for raw in items or []:
        if "=" in raw:
            block, vid = raw.split("=", 1)
            out[block.strip()] = vid.strip()
            continue
        vid = raw.strip()
        if vid not in variants:
            raise SystemExit(f"未知芯片实例 {vid}（可用：{', '.join(sorted(variants))}）")
        blk = variants[vid].get("for_block")
        if not blk:
            raise SystemExit(f"芯片 {vid} 没有登记 for_block，请写成 --chip 块名={vid}")
        out[blk] = vid
    return out


def cmd_netlist(args) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    nl = build_netlist(spec, _parse_chip_map(getattr(args, "chip", None)))
    if args.output:
        dump(nl, args.output)
        if args.report:
            _print_report(nl)
    elif args.report:
        _print_report(nl)
    else:
        dump(nl, None)
    return 0


def _print_report(nl: dict) -> None:
    st = nl["stats"]
    print("\n=== 网表摘要 ===")
    print(f"器件 {st['parts']}（其中待绑定 {st['binding_pending']}/{st['binding_required']}）"
          f"，网络 {st['nets']}，功能块 {st['blocks']}")
    print("\n电源网络：")
    for name, rec in nl["nets"].items():
        if rec["class"] in ("power", "ground"):
            v = rec["voltage"]
            print(f"  {name:<12} {'%gV' % v if v is not None else '电压未定':>8}  {len(rec['pins'])} 个引脚")
    if nl.get("open_questions"):
        print("\n需要客户确认：")
        for q in nl["open_questions"]:
            print(f"  - {q}")
    print("\n下一步：python schem.py erc netlist.json")


# ---------------------------------------------------------------- ERC

def _nets_of(part: dict) -> dict[str, str]:
    return part["pins"]


def _net_voltage(nl: dict, net: str) -> float | None:
    rec = nl["nets"].get(net)
    return rec.get("voltage") if rec else None


def _railed(nl: dict, net: str) -> bool:
    return nl["nets"].get(net, {}).get("class") in ("power", "ground")


def _series_part(parts: list[dict], nets: dict, ref: str, net: str, kinds: tuple) -> dict | None:
    """判断 net 上是否有一个与 ref 串联的 kinds 类器件。
    只看『该网络恰好 2 个引脚』——共用地网络的电阻不算串联电阻。"""
    rec = nets.get(net) or {}
    members = rec.get("pins") or []
    if len(members) != 2:
        return None
    mine = [m for m in members if m.startswith(ref + ".")]
    if len(mine) != 1:
        return None
    other = members[0] if members[1] == mine[0] else members[1]
    oref = other.split(".")[0]
    for q in parts:
        if q["ref"] == oref and q.get("kind") in kinds:
            return q
    return None


def run_erc(nl: dict) -> list[dict]:
    cons = load_constraints()["schematic"]
    sev_tbl = cons["erc_severity"]
    findings: list[dict] = []

    def add(rule: str, target: str, msg: str, fix: str, sev: str | None = None):
        findings.append({
            "rule": rule,
            "severity": sev or sev_tbl.get(rule, "WARN"),
            "target": target,
            "message": msg,
            "fix": fix,
        })

    parts = nl["parts"]
    nets = nl["nets"]
    # 容忍手写/外部生成的网表：缺字段就补默认，并把电源类网络认出来
    rail_v = {r["name"]: r.get("v") for r in nl.get("rails", [])}
    for name, rec in nets.items():
        rec.setdefault("pins", [])
        rec.setdefault("voltage", None)
        rec.setdefault("class", "signal")
        if name in GND_ALIASES:
            rec["class"], rec["voltage"] = "ground", 0.0
        elif name in rail_v and rec["class"] == "signal":
            rec["class"] = "power"
            rec["voltage"] = rail_v[name]
    for p in parts:
        p.setdefault("binding", "none")
        p.setdefault("pins", {})

    by_ref: dict[str, dict] = {}
    for p in parts:
        by_ref.setdefault(p["ref"], p)

    # 1 位号重复
    seen = set()
    for p in parts:
        if p["ref"] in seen:
            add("SCH-ERC-DUP-REF", p["ref"], "位号重复，网表/PCB 会错位", "重新分配位号")
        seen.add(p["ref"])

    # 2 引脚冲突（同一器件同一引脚挂两个网络，仅当手工改过 netlist 才会出现）
    pin_seen: dict[str, str] = {}
    for p in parts:
        for pid, net in p["pins"].items():
            k = f"{p['ref']}.{pid}"
            if k in pin_seen and pin_seen[k] != net:
                add("SCH-ERC-PIN-CONFLICT", k,
                    f"同一引脚被分到两个网络（{pin_seen[k]} / {net}）", "删掉错误的连接")
            pin_seen[k] = net

    # 3 单点网络
    mcu_pending = any(p["kind"] == "MCU" and p.get("binding") == "required" and not p.get("lcsc")
                      for p in parts)
    for name, rec in nets.items():
        if len(rec["pins"]) == 1 and rec["class"] == "signal":
            if mcu_pending and re.search(r"(_CTRL|_TX|_RX|SDA|SCL)$", name):
                add("SCH-ERC-SINGLE", name,
                    f"{name} 只连了 {rec['pins'][0]}，另一端应在 MCU 上，但主控还没绑定",
                    "选定主控后把该网络连到具体 GPIO，再重跑 ERC", "INFO")
            else:
                add("SCH-ERC-SINGLE", name,
                    f"网络只有 1 个引脚（{rec['pins'][0]}），等于悬空",
                    "接到对端，或改成测试点并标 TP_ 前缀")

    # 4 网络命名
    pat = re.compile(cons["net_naming"]["allowed_regex"])
    bad_names = [n for n in nets if not pat.match(n) or n in cons["net_naming"]["forbidden_names"]]
    for n in bad_names:
        add("SCH-NET-NAME", n, "网络名含非法字符或长度超限", "只保留字母数字与 _ + - / .")

    # 5 地种类
    grounds = {n for n in nets if n in GND_ALIASES}
    if len(grounds) > cons["net_naming"]["max_ground_kinds_warn"]:
        add("SCH-GND-KINDS", "/".join(sorted(grounds)),
            f"板上有 {len(grounds)} 种地，单点连接位置容易出错",
            "确认每种地的分割与单点汇合位置，并在图注里写明")

    # 6 去耦
    for p in parts:
        if p["kind"] not in ("IC", "MCU"):
            continue
        for pid, net in p["pins"].items():
            if not _railed(nl, net) or nl["nets"][net]["class"] == "ground":
                continue
            ok = False
            for q in parts:
                if q["kind"] != "C":
                    continue
                qnets = set(q["pins"].values())
                if net in qnets and any(n in grounds for n in qnets):
                    cap = parse_c(q["value"]) or 0
                    if cap >= cons["decoupling"]["cap_per_power_pin_f"] * 0.9:
                        ok = True
            if not ok:
                add("SCH-PWR-DECOUPLE", f"{p['ref']}.{pid}",
                    f"{p['ref']} 的 {pid} 脚接 {net}，但该网络没有 ≥100nF 的去耦电容",
                    f"在 {net} 与该 IC 的地之间加 100nF（X7R），PCB 上贴脚放")

    # 7 bulk
    for name, rec in nets.items():
        if rec["class"] != "power" or name in ("VIN_IN", "VBUS"):
            continue
        tot = 0.0
        for q in parts:
            if q["kind"] != "C":
                continue
            qnets = set(q["pins"].values())
            if name in qnets and any(n in grounds for n in qnets):
                tot += parse_c(q["value"]) or 0
        if tot < cons["decoupling"]["bulk_per_rail_f"]:
            add("SCH-PWR-BULK", name,
                f"{name} 上总电容 {tot * 1e6:.1f}µF，低于 {cons['decoupling']['bulk_per_rail_f'] * 1e6:.0f}µF",
                "加一颗 10µF 以上陶瓷/钽电容做储能")

    # 8 输入保护
    vin_like = [n for n in nets if n in ("VIN", "VIN_IN", "VBUS")]
    if vin_like:
        has_fuse = any(p["kind"] == "F" for p in parts)
        has_tvs = any(p["kind"] == "TVS" and any(n in vin_like for n in p["pins"].values())
                      for p in parts)
        if not (has_fuse or has_tvs):
            add("SCH-PWR-PROTECT", ",".join(vin_like),
                "电源入口没有保险丝也没有 TVS", "加自恢复保险 + SMBJ 系列 TVS，或说明为何不需要")

    # 9 I2C 上拉
    for name in nets:
        if not re.search(r"(SDA|SCL)$", name):
            continue
        ok = False
        for q in parts:
            if q["kind"] != "R":
                continue
            qn = set(q["pins"].values())
            if name in qn and any(nl["nets"].get(x, {}).get("class") == "power" for x in qn):
                ok = True
        if not ok:
            add("SCH-I2C-PULLUP", name, f"{name} 没有上拉到电源的电阻", "按 schem.py calc i2c 算值后加上拉")

    # 10 MCU 复位 / 时钟
    for p in parts:
        if p["kind"] != "MCU":
            continue
        rst = next((n for pid, n in p["pins"].items() if "RST" in pid.upper()), None)
        if rst:
            has_r = any(q["kind"] == "R" and rst in q["pins"].values()
                        and any(nl["nets"].get(x, {}).get("class") == "power" for x in q["pins"].values())
                        for q in parts)
            has_c = any(q["kind"] == "C" and rst in q["pins"].values()
                        and any(n in grounds for n in q["pins"].values())
                        for q in parts)
            if not (has_r and has_c):
                add("SCH-MCU-RESET", rst, f"{p['ref']} 的复位网络 {rst} 缺上拉或对地电容",
                    "10k 上拉到电源 + 100nF 到地，上电才有确定复位")

        xi = next((n for pid, n in p["pins"].items() if "XTAL_IN" in pid.upper() or "OSC_IN" in pid.upper()), None)
        xo = next((n for pid, n in p["pins"].items() if "XTAL_OUT" in pid.upper() or "OSC_OUT" in pid.upper()), None)
        if xi and xo and xi != xo:
            has_y = any(q["kind"] == "Y" and {xi, xo} <= set(q["pins"].values()) for q in parts)
            if not has_y:
                add("SCH-MCU-CLOCK", f"{p['ref']}",
                    "MCU 时钟脚存在但没接晶振（用内部 RC 时请把这两个脚从网表里去掉）",
                    "补晶振 + 两颗负载电容，或用 schem.py calc crystal 算完再加")

    # 11 晶振负载电容
    for p in parts:
        if p["kind"] != "Y":
            continue
        pn = list(p["pins"].values())
        caps = []
        for net in pn:
            for q in parts:
                if q["kind"] == "C" and net in q["pins"].values() and any(n in grounds for n in q["pins"].values()):
                    caps.append((net, q))
        if len({n for n, _ in caps}) < 2:
            add("SCH-XTAL-CL", p["ref"], "晶振两侧负载电容不全，起振裕量不够",
                "两侧各一颗，按 C=2×(CL−Cstray) 取值")
        else:
            vals = [parse_c(q["value"]) for _, q in caps]
            if None not in vals and max(vals) > 0 and (max(vals) - min(vals)) / max(vals) > 0.2:
                add("SCH-XTAL-CL", p["ref"], f"两侧负载电容不等（{vals}）", "两侧必须同值，否则频偏与起振困难")

    # 12 USB Type-C CC
    for p in parts:
        if p["kind"] != "J":
            continue
        cc = {pid: net for pid, net in p["pins"].items() if pid.upper().startswith("CC")}
        if not cc:
            continue
        for pid, net in cc.items():
            ok = None
            for q in parts:
                if q["kind"] != "R":
                    continue
                qn = set(q["pins"].values())
                if net in qn and any(n in grounds for n in qn):
                    ok = parse_r(q["value"])
            if ok is None:
                add("SCH-USB-CC", f"{p['ref']}.{pid}",
                    f"{pid} 没有 5.1k 下拉，source 不会供电", "CC1/CC2 各接 5.1kΩ±5% 到 GND", "FAIL")
            elif abs(ok - cons["usb_c"]["rd_ohm"]) / cons["usb_c"]["rd_ohm"] > 0.05:
                add("SCH-USB-CC", f"{p['ref']}.{pid}",
                    f"{pid} 下拉是 {ok:.0f}Ω，不是 5.1kΩ",
                    "UFP 只能 5.1kΩ；Rp 会让设备变成 DRP", "FAIL")

    # 13 USB ESD
    usb_nets = [n for n in nets if n in ("USB_DP", "USB_DN")]
    if usb_nets:
        guarded = {n for p in parts if p["kind"] == "TVS" for n in p["pins"].values()}
        for n in usb_nets:
            if n not in guarded:
                add("SCH-USB-ESD", n, f"{n} 没有 ESD 保护", "就近加 USBLC6 类 ESD 阵列")

    # 14 RS485 终端 / 偏置
    a, b = "RS485_A", "RS485_B"
    if a in nets and b in nets:
        term = [q for q in parts if q["kind"] == "R"
                and {a, b} <= set(q["pins"].values())
                and abs((parse_r(q["value"]) or 0) - cons["rs485"]["rt_ohm"]) < 13]
        if not term:
            declared = any(p.get("note", "").find("中间节点") >= 0 for p in parts)
            add("SCH-RS485-TERM", f"{a}/{b}",
                "A/B 之间没有 120Ω 终端", "总线两端各加一颗 120Ω；中间节点不要加",
                None if not declared else "INFO")
        bias_hi = any(q["kind"] == "R" and a in q["pins"].values()
                      and any(nl["nets"].get(x, {}).get("class") == "power" for x in q["pins"].values())
                      for q in parts)
        bias_lo = any(q["kind"] == "R" and b in q["pins"].values()
                      and any(n in grounds for n in q["pins"].values()) for q in parts)
        if not (bias_hi and bias_lo):
            add("SCH-RS485-BIAS", f"{a}/{b}",
                "缺失效保护偏置（A 上拉 / B 下拉），空闲时总线状态不定",
                "A 经 680Ω 上拉、B 经 680Ω 下拉；整条总线只需一处")

    # 15 CAN 终端
    if "CAN_H" in nets and "CAN_L" in nets:
        direct = any(q["kind"] == "R" and {"CAN_H", "CAN_L"} <= set(q["pins"].values())
                     and abs((parse_r(q["value"]) or 0) - 120) < 13 for q in parts)
        mid = [n for n in nets if n.startswith("CAN_") and n not in ("CAN_H", "CAN_L")]
        split = sum(1 for q in parts if q["kind"] == "R"
                    and "CAN_H" in q["pins"].values() and any(n in mid for n in q["pins"].values()))
        if not (direct or split >= 1):
            add("SCH-CAN-TERM", "CAN_H/CAN_L", "CAN 总线没有终端电阻",
                "两端各 120Ω，或分裂终端 2×60Ω + 4.7nF")

    # 16 LED 限流（必须是串联在 LED 回路里的电阻，共用地不算）
    for p in parts:
        if p["kind"] != "LED":
            continue
        leads = [p["pins"].get(x) for x in ("A", "K") if p["pins"].get(x)]
        if not any(_series_part(parts, nets, p["ref"], n, ("R",)) for n in leads):
            add("SCH-LED-RES", p["ref"],
                "LED 没有串联限流电阻（只共用地网络不算，必须串在回路里）",
                "按 (Vcc−Vf)/I 加电阻，见 schem.py calc led", "FAIL")

    # 17 感性负载续流
    for p in parts:
        if p["kind"] not in ("K", "BZ"):
            continue
        coil = [n for pid, n in p["pins"].items() if pid.upper() in ("A1", "A2", "1", "2")]
        coil = coil[:2]
        if len(coil) < 2:
            continue
        has_d = any(q["kind"] == "D" and set(coil) <= set(q["pins"].values()) for q in parts)
        if not has_d:
            add("SCH-RELAY-FLYBACK", p["ref"],
                "线圈两端没有反并联续流二极管，关断瞬间会打穿驱动管",
                "二极管阴极接电源侧、阳极接驱动侧", "FAIL")

    # 18 ADC 源阻抗
    for name, rec in nets.items():
        if rec["class"] != "signal":
            continue
        rs = [q for q in parts if q["kind"] == "R" and name in q["pins"].values()]
        if len(rs) != 2:
            continue
        vals = [parse_r(q["value"]) for q in rs]
        if None in vals:
            continue
        rth = (vals[0] * vals[1]) / (vals[0] + vals[1])
        has_c = any(q["kind"] == "C" and name in q["pins"].values()
                    and any(n in grounds for n in q["pins"].values()) for q in parts)
        if rth > cons["adc"]["max_source_ohm"] and not has_c:
            add("SCH-ADC-IMP", name,
                f"分压源阻抗 {rth:.0f}Ω 超 {cons['adc']['max_source_ohm']}Ω 且无缓冲电容",
                "降低分压阻值或并 100nF")

    # 19 TVS 工作电压校核
    for p in parts:
        if p["kind"] != "TVS":
            continue
        m = re.search(r"SMBJ(\d+(?:\.\d+)?)", str(p["value"]))
        if not m:
            continue
        vrwm = float(m.group(1))
        for net in p["pins"].values():
            v = _net_voltage(nl, net)
            if v and v > 1:
                if vrwm < v * cons["tvs"]["vrwm_mult"]:
                    add("SCH-TVX-VRWM", f"{p['ref']}@{net}",
                        f"TVS VRWM={vrwm}V 低于工作电压 {v}V 的 1.1 倍",
                        f"换 VRWM ≥ {v * 1.1:.1f}V 的型号", "FAIL")

    # 20 绑定状态
    pending = [p["ref"] for p in parts if p.get("binding") == "required" and not p.get("lcsc")]
    if pending:
        add("SCH-BINDING", ",".join(pending[:12]) + ("…" if len(pending) > 12 else ""),
            f"{len(pending)} 个关键器件还没选型（没有 lcsc 编号）",
            "逐个确认型号后填 netlist 的 lcsc 字段，再跑 playbook")

    # 21 数据缺口
    missing_v = [n for n, r in nets.items() if r["class"] == "power" and r.get("voltage") is None]
    if missing_v:
        add("SCH-DATA", ",".join(missing_v),
            "这些电源网络没有电压信息，TVS/间距/爬电都无法校核",
            "在 spec.rails 里补 v 字段")

    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["rule"], f["target"]))
    return findings


def cmd_erc(args) -> int:
    nl = json.loads(Path(args.netlist).read_text(encoding="utf-8"))
    findings = run_erc(nl)
    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    floor = order.get(args.min_severity, 2)
    # FAIL 最重要：只显示「严重度不低于 floor」的项，即 order <= floor
    shown = [f for f in findings if order.get(f["severity"], 3) <= floor]
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("FAIL", "WARN", "INFO")}

    if args.json:
        print(json.dumps({"counts": counts, "findings": shown}, ensure_ascii=False, indent=2))
    else:
        print(f"检查结果：FAIL {counts['FAIL']} / WARN {counts['WARN']} / INFO {counts['INFO']}")
        for f in shown:
            mark = {"FAIL": "✗", "WARN": "!", "INFO": "i"}[f["severity"]]
            print(f"[{f['severity']}] {mark} {f['rule']:<22} {f['target']}")
            print(f"        {f['message']}")
            if f["fix"]:
                print(f"        → {f['fix']}")
        if counts["FAIL"] == 0 and counts["WARN"] == 0:
            print("未发现硬性问题。INFO 项仍需人工确认，INFO 不等于通过。")
        else:
            print("\n提示：INFO 表示数据不足或已声明例外，不等于通过。")

    if args.strict:
        return 1 if counts["FAIL"] or counts["WARN"] else 0
    return 1 if counts["FAIL"] else 0


# ---------------------------------------------------------------- BOM

def cmd_bom(args) -> int:
    nl = json.loads(Path(args.netlist).read_text(encoding="utf-8"))
    groups: dict[tuple, list[str]] = {}
    for p in nl["parts"]:
        key = (p["kind"], str(p["value"]), p.get("lcsc") or "")
        groups.setdefault(key, []).append(p["ref"])

    rows = []
    for (kind, value, lcsc), refs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        rows.append({
            "kind": kind,
            "value": value,
            "qty": len(refs),
            "designators": " ".join(refs),
            "lcsc": lcsc,
            "status": "已选型" if lcsc else ("待绑定" if kind in ("IC", "MCU", "Y", "TVS", "K", "Q", "BZ") else "通用件"),
        })

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["kind", "value", "qty", "designators", "lcsc", "status"])
            w.writeheader()
            w.writerows(rows)
        print(f"已写入 {args.output}（{len(rows)} 行）")
    else:
        print(f"{'种类':<6}{'参数':<16}{'数量':<5}{'位号':<24}{'LCSC':<10}状态")
        for r in rows:
            print(f"{r['kind']:<6}{r['value']:<16}{r['qty']:<5}{r['designators']:<24}{r['lcsc']:<10}{r['status']}")
    pending = [r for r in rows if r["status"] == "待绑定"]
    if pending:
        print(f"\n待绑定 {len(pending)} 类：料号必须人选定，脚本不猜。")
    return 0


# ---------------------------------------------------------------- 绑定（EDA 引脚回读）

def cmd_bind(args) -> int:
    nl = json.loads(Path(args.netlist).read_text(encoding="utf-8"))
    rb = json.loads(Path(args.readback).read_text(encoding="utf-8"))
    if isinstance(rb, list):
        rb = {item["ref"]: item for item in rb}

    bound, unmatched, not_given, conflicts = 0, [], [], []
    for p in nl["parts"]:
        info = rb.get(p["ref"])
        if not info:
            not_given.append(p["ref"])
            continue
        # 芯片实例已经声明过引脚表时，回读结果必须与它一致 —— 不一致说明库里选错了器件
        declared = dict(p.get("pin_no") or {}) if p.get("variant") else {}
        pins = info.get("pins") or []
        name_map = {}
        for pin in pins:
            name = str(pin.get("name") or pin.get("pinName") or "").strip()
            no = str(pin.get("n") or pin.get("pinNumber") or "").strip()
            if name and no:
                name_map[name] = no
        pin_no = {}
        for pid in p["pins"]:
            if pid in name_map:
                pin_no[pid] = name_map[pid]
            elif any(str(pin.get("n")) == pid for pin in pins):
                pin_no[pid] = pid
        if len(pin_no) == len(p["pins"]):
            p["pin_no"] = pin_no
            bound += 1
            if declared:
                diff = {k: {"variant": declared[k], "readback": pin_no[k]}
                        for k in declared
                        if k in pin_no and declared[k] != pin_no[k]}
                if diff:
                    conflicts.append({"ref": p["ref"], "variant": p.get("variant"), "diff": diff})
        else:
            missing = [pid for pid in p["pins"] if pid not in pin_no]
            unmatched.append({"ref": p["ref"], "missing_pin_ids": missing,
                              "available_pin_names": sorted(name_map)[:20]})

    nl["bind_status"] = {"bound": bound, "mismatch": unmatched,
                         "not_in_readback": not_given, "pin_conflicts": conflicts}
    dump(nl, args.output)
    if unmatched:
        print(f"\n{len(unmatched)} 个器件引脚对不上，需人工核对（功能名 ↔ 实际引脚名）：")
        for u in unmatched[:5]:
            print(f"  {u['ref']}: 缺 {u['missing_pin_ids']}；库里可用的脚名 {u['available_pin_names']}")
    if conflicts:
        print(f"\n{len(conflicts)} 个器件的引脚号与芯片实例表不一致 —— 多半是库里选了别的型号或别的封装：")
        for c in conflicts[:5]:
            print(f"  {c['ref']}（实例 {c['variant']}）")
            for pin, v in list(c["diff"].items())[:6]:
                print(f"     {pin}: 实例表说 {v['variant']} 脚，回读是 {v['readback']} 脚")
        print("  先确认库器件是否就是这台型号；确属换料就更新 chip-variants.json，别让两边长期不一致。")
    print(f"已绑定 {bound} 个器件；回读里没有的 {len(not_given)} 个保持未绑定"
          + ("（可分批 bind）" if not_given else ""))
    return 0 if (not unmatched and not conflicts) else 2


# ---------------------------------------------------------------- 落笔 playbook

JS_TEMPLATE = r"""/* jlc-pcb-layout · 原理图落笔脚本
 * 由 scripts/schem.py 生成，请勿手改；要改就改 netlist 再重新生成。
 * 用法：嘉立创EDA 专业版 → 菜单「高级 → 运行脚本」→ 粘贴本文件 → F9
 *      或：lceda-pro invoke --session <id> --ext-uuid eda --code "$(cat this.js)"
 *
 * 做的事：按网表放器件 → 读回每个引脚真实坐标 → 每个引脚拉一段带 net 的短线（同名即连通）
 * 不做的事：整理走线走向、自动分页、画图框。逻辑连通优先，美观另做。
 */
const PARTS = __PARTS__;
const NETS = __NETS__;
const POWERS = __POWERS__;
const CFG = { stub: 20, cols: __COLS__, gapX: 70, gapY: 55, originX: 60, originY: 120 };

(async function () {
  const log = [];
  const say = (m) => { console.log(m); log.push(m); };

  // 兼容层：v3.2.x 用小写 lib_Device 且 create 是同步的；v3.3+ 大写且返回 Promise。
  // 先同步调用，只有返回 thenable 才 await —— 否则在旧版里加 await 会静默失败。
  const LD = eda.lib_Device || eda.LIB_Device;
  function callSyncOrAsync(fn, args) {
    const r = fn.apply(null, args);
    return (r && typeof r.then === 'function') ? r : Promise.resolve(r);
  }
  function pick(obj, key) {
    if (!obj) return undefined;
    const g = obj['getState_' + key];
    return (typeof g === 'function') ? g.call(obj) : obj[key];
  }

  if (!LD) { say('FAIL: 找不到器件库模块（lib_Device / LIB_Device 都不存在）'); return log; }

  const info = await eda.dmt_Project.getCurrentProjectInfo();
  const page = info.data[0].schematic.page[0].uuid;
  await eda.dmt_EditorControl.openDocument(page);
  await new Promise(r => setTimeout(r, 2500));
  say('page: ' + page);

  // 1) 放器件
  const IDS = {};
  let i = 0;
  for (const p of PARTS) {
    const x = CFG.originX + (i % CFG.cols) * CFG.gapX;
    const y = CFG.originY + Math.floor(i / CFG.cols) * CFG.gapY;
    i++;
    let devs = [];
    try {
      devs = await callSyncOrAsync(LD.getByLcscIds, [[p.lcsc]]);
      if (!devs || !devs.length) devs = await callSyncOrAsync(LD.search, [p.lcsc]);
    } catch (e) { say('search fail ' + p.ref + ': ' + e); }
    if (!devs || !devs.length) { say('MISS ' + p.ref + ' ' + p.lcsc); continue; }
    const comp = await callSyncOrAsync(eda.sch_PrimitiveComponent.create,
      [devs[0], x, y, undefined, 0, false, true, true]);
    if (!comp) { say('PLACE_FAIL ' + p.ref); continue; }
    const id = pick(comp, 'PrimitiveId');
    IDS[p.ref] = id;
    const snap = pick(await callSyncOrAsync(eda.sch_PrimitiveComponent.get, [id]), 'OtherProperty');
    await callSyncOrAsync(eda.sch_PrimitiveComponent.modify, [id, { designator: p.ref, otherProperty: snap }]);
    say('placed ' + p.ref + ' ' + p.value + ' @' + x + ',' + y);
  }

  // 2) 读回引脚坐标（唯一可信来源，禁止推算）
  const PINS = {};
  for (const ref of Object.keys(IDS)) {
    const raw = await callSyncOrAsync(eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId, [IDS[ref]]);
    PINS[ref] = (raw || []).map(function (p) {
      return { n: String(pick(p, 'PinNumber') || p.pinNumber || ''),
               name: String(pick(p, 'PinName') || p.pinName || ''),
               x: pick(p, 'X'), y: pick(p, 'Y') };
    });
  }

  function findPin(ref, pid) {
    const list = PINS[ref] || [];
    const byNo = list.find(function (p) { return p.n === pid; });
    if (byNo) return byNo;
    return list.find(function (p) { return p.name === pid; }) || null;
  }

  // 3) 每个引脚一段带 net 的短线：网络名相同即逻辑连通
  let wires = 0, miss = 0;
  for (const net of Object.keys(NETS)) {
    for (const m of NETS[net]) {
      const pin = findPin(m[0], m[1]);
      if (!pin) { miss++; continue; }
      const w = await callSyncOrAsync(eda.sch_PrimitiveWire.create,
        [[[pin.x, pin.y, pin.x + CFG.stub, pin.y]], net, null, 2, null]);
      if (w) wires++; else miss++;
    }
  }
  say('wires ' + wires + ' / missed ' + miss);

  // 4) 电源网络补一个网络标识，方便人眼睛读图
  let flags = 0;
  for (const net of POWERS) {
    const members = NETS[net] || [];
    if (!members.length) continue;
    const pin = findPin(members[0][0], members[0][1]);
    if (!pin) continue;
    const kind = net === 'GND' ? 'Ground' : 'Power';
    const f = await callSyncOrAsync(eda.sch_PrimitiveComponent.createNetFlag,
      [kind, net, pin.x + CFG.stub, pin.y, 0, false]);
    if (f) flags++;
  }
  say('flags ' + flags);

  await eda.sch_Document.save();
  say('saved. 等 8~10 秒再跑 sch_Drc.check(true,false,true)');

  return { placed: Object.keys(IDS).length, wires: wires, missedPins: miss, flags: flags, log: log };
})();
"""


def cmd_playbook(args) -> int:
    nl = json.loads(Path(args.netlist).read_text(encoding="utf-8"))
    missing = [p["ref"] for p in nl["parts"] if not p.get("lcsc")]
    if missing and not args.allow_missing:
        print("拒绝生成落笔脚本：以下器件没有 lcsc 料号 ——")
        print("  " + ", ".join(missing))
        print("\n落笔前必须选定型号。编料号会直接造成批量买错料。")
        print("确认型号后填进 netlist 的 lcsc 字段；确实要先看布局时加 --allow-missing。")
        return 2

    parts_js = [
        {"ref": p["ref"], "value": str(p["value"]), "lcsc": p.get("lcsc") or "", "kind": p["kind"]}
        for p in nl["parts"]
    ]
    nets_js = {}
    for name, rec in nl["nets"].items():
        members = []
        for pin in rec["pins"]:
            ref, _, pid = pin.partition(".")
            part = next((x for x in nl["parts"] if x["ref"] == ref), None)
            if part and part.get("pin_no", {}).get(pid):
                pid = part["pin_no"][pid]
            members.append([ref, pid])
        nets_js[name] = members

    powers = [n for n, r in nl["nets"].items() if r["class"] == "power"] + ["GND"]
    powers = [n for n in dict.fromkeys(powers) if n in nl["nets"]]

    js = (JS_TEMPLATE
          .replace("__PARTS__", json.dumps(parts_js, ensure_ascii=False, indent=2))
          .replace("__NETS__", json.dumps(nets_js, ensure_ascii=False, indent=2))
          .replace("__POWERS__", json.dumps(powers, ensure_ascii=False))
          .replace("__COLS__", str(max(4, min(10, int(math.ceil(math.sqrt(len(parts_js) + 1))))))))

    if args.js:
        Path(args.js).write_text(js, encoding="utf-8")
        print(f"已写入 {args.js}")
    if args.output:
        Path(args.output).write_text(js, encoding="utf-8")

    meta = {
        "project": nl["project"],
        "parts": len(parts_js),
        "nets": len(nets_js),
        "unbound_count": len(missing),
        "unbound_parts": missing[:20],
        "steps": [
            "openDocument(page) + 等 2500ms",
            "逐个 lib_Device 搜索 → sch_PrimitiveComponent.create(lib,x,y,undefined,0,false,true,true)",
            "modify(id,{designator,otherProperty}) 一次写回位号与属性",
            "getAllPinsByPrimitiveId 读回真实引脚坐标",
            "每个引脚 sch_PrimitiveWire.create([[x,y,x+20,y]], net)",
            "电源网络 createNetFlag",
            "sch_Document.save()",
            "等 8~10s → sch_Drc.check(true,false,true)",
        ],
        "caveat": [
            "只保证逻辑连通（网络名相同即连通），不保证走线好看；整理走向是人的活",
            "addIntoPcb 固定传 true，否则同步到 PCB 会得到 0 个器件",
            "引脚按 pinNumber 匹配，匹配不上时回退到 pinName；两套都对不上会记 missed",
        ],
    }
    if args.output:
        meta_path = args.output + ".meta.json"
    elif args.js:
        meta_path = args.js + ".meta.json"
    else:
        meta_path = None
    dump(meta, meta_path)
    if meta_path:
        print(f"落笔脚本 {meta['parts']} 器件 / {meta['nets']} 网络，"
              f"未选型 {meta['unbound_count']} 个（这些不会放置）")
    return 0


# ---------------------------------------------------------------- blocks

def cmd_chips(args) -> int:
    variants = load_chips()
    rows = []
    for vid, v in sorted(variants.items()):
        if getattr(args, "for_block", None) and v.get("for_block") != args.for_block:
            continue
        rows.append({
            "id": vid,
            "for_block": v.get("for_block"),
            "name": v.get("name"),
            "package": v.get("package"),
            "lcsc": v.get("lcsc"),
            "lcsc_status": v.get("lcsc_status"),
            "pins": len(v.get("pins") or {}),
            "pins_partial": bool(v.get("pins_partial")),
            "layout_rules": len(v.get("pcb_layout") or []),
            "blocked_by": v.get("blocked_by"),
            "origin": v.get("origin"),
        })
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print(f"块 {args.for_block} 暂无芯片实例。用 `chips` 看全部。")
        return 0
    print(f"{len(rows)} 个芯片实例\n")
    for r in rows:
        flag = "✗ 不可套用" if r["blocked_by"] else ("部分引脚" if r["pins_partial"] else "引脚完整")
        lc = r["lcsc"] or "—（需选型）"
        print(f"{r['id']:<18} → {r['for_block']:<16} {r['name']}")
        print(f"   {r['package'] or '?':<18} LCSC {lc:<14} {r['pins']} 脚 {flag} · {r['layout_rules']} 条布局约束")
        if r["blocked_by"]:
            print(f"   ! {r['blocked_by'][:110]}…")
    print("\n引脚完整 + 有 LCSC 才是可直接落笔的组合；标「需选型」的必须先填料号。")
    return 0


def cmd_blocks(args) -> int:
    blocks = load_blocks()
    if args.json:
        dump({k: {"title": v["title"], "category": v["category"], "keywords": v["keywords"],
                  "params": {pk: pv.get("default") for pk, pv in v.get("params", {}).items()},
                  "parts": len(v["parts"]), "erc": v.get("erc", [])}
              for k, v in blocks.items()}, None)
        return 0
    cat = {}
    for bid, b in blocks.items():
        cat.setdefault(b["category"], []).append((bid, b))
    for c, items in cat.items():
        print(f"\n[{c}]")
        for bid, b in items:
            print(f"  {bid:<18}{b['title']:<22}{len(b['parts'])} 器件  ERC: {', '.join(b.get('erc', [])) or '-'}")
    print(f"\n共 {len(blocks)} 个功能块。用法：schem.py netlist spec.json，spec 里写 blocks:[{{type,count,params}}]")
    return 0


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="客户需求 → 原理图网表 的生成与校验器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("blocks", help="列出功能块库")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_blocks)

    p = sub.add_parser("spec", help="需求文本 → 设计规格")
    p.add_argument("--req", help="需求文本文件")
    p.add_argument("--text", help="直接给需求文本")
    p.add_argument("--project")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_spec)

    p = sub.add_parser("calc", help="原理图数值计算")
    p.add_argument("kind", choices=["led", "crystal", "i2c", "divider", "rc", "ldo",
                                    "rs485-bias", "usb-cc", "tvs", "relay", "buck"])
    p.add_argument("--json", action="store_true")
    p.add_argument("--vcc", type=float, default=3.3)
    p.add_argument("--vf", type=float)
    p.add_argument("--color", default="red")
    p.add_argument("--i-ma", dest="i_ma", type=float)
    p.add_argument("--cl", type=float)
    p.add_argument("--cstray", type=float)
    p.add_argument("--mode", default="fast", choices=["standard", "fast", "fast_plus"])
    p.add_argument("--cb-pf", dest="cb_pf", type=float)
    p.add_argument("--vin", type=float, default=12)
    p.add_argument("--vout", type=float, default=3.3)
    p.add_argument("--r-bottom", dest="r_bottom", default="10k")
    p.add_argument("--r", default="10k")
    p.add_argument("--c", default="100nF")
    p.add_argument("--iout", type=float, default=0.3)
    p.add_argument("--rt", type=float)
    p.add_argument("--vop", type=float, default=12)
    p.add_argument("--vdrive", type=float, default=3.3)
    p.add_argument("--coil-ma", dest="coil_ma", type=float, default=70)
    p.add_argument("--beta", type=float, default=100)
    p.add_argument("--fsw-khz", dest="fsw_khz", type=float, default=500)
    p.set_defaults(func=cmd_calc)

    p = sub.add_parser("chips", help="列出芯片实例（型号/料号/引脚/布局约束）")
    p.add_argument("--for-block", help="只看某个拓扑块可用的芯片")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_chips)

    p = sub.add_parser("netlist", help="设计规格 → 网表")
    p.add_argument("--chip", action="append", metavar="块=型号",
                   help="套用芯片实例，如 --chip rs485=sp3485；可重复")
    p.add_argument("spec")
    p.add_argument("-o", "--output")
    p.add_argument("--report", action="store_true")
    p.set_defaults(func=cmd_netlist)

    p = sub.add_parser("erc", help="网表 → 电气规则检查")
    p.add_argument("netlist")
    p.add_argument("--min-severity", dest="min_severity", default="INFO",
                   choices=["FAIL", "WARN", "INFO"])
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true", help="WARN 也算不通过")
    p.set_defaults(func=cmd_erc)

    p = sub.add_parser("bom", help="网表 → BOM 骨架")
    p.add_argument("netlist")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_bom)

    p = sub.add_parser("bind", help="把 EDA 回读的引脚号并进网表")
    p.add_argument("netlist")
    p.add_argument("readback")
    p.add_argument("-o", "--output", required=True)
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("playbook", help="网表 → 落笔 JS 脚本")
    p.add_argument("netlist")
    p.add_argument("--js")
    p.add_argument("-o", "--output")
    p.add_argument("--allow-missing", dest="allow_missing", action="store_true")
    p.set_defaults(func=cmd_playbook)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
