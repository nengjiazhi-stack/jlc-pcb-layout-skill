#!/usr/bin/env python3
"""按网络记 primitiveId 的账，收尾时对账 —— 专门抓"改线时叠画"。

为什么几何判据不够
------------------
`check.py` 的 RT-DUP-01 比的是铜的几何：两段共线重叠 80% 才报。但叠画有
三种形态它看不见：

  1. 新线只盖住旧线的一小截（重叠 30%）—— 判据放过，铜却多出来了；
  2. 旧线被新线推着改了个端点，两段刚好错开一个线宽 —— 几何上不重叠，
     电气上还是一根多余的辫子；
  3. 新线落在**别的网络**的旧线上（改网时忘了删）—— 一报就是短路，
     但那时已经烧板了。

这三种都有同一个动作特征：**该删的旧段没删**。几何看不出来，日志看得出来。

为什么要按 id 比、不按段数比
--------------------------
EDA 原生会在交点处把线切开：调 200 次 `line.add` 可能得到 202 段，一段
声明新增的线也可能变成两段。所以"段数 == 操作数"这个检查必然误报。
primitiveId 是稳定的：声明删的 id 还在，就是没删；声明加的 id 变成两个，
是切段，不是错误。

用法
----
    # 布线前：记基线
    easyeda pcb line-list --json > lines.pre.json
    python scripts/journal.py baseline --lines lines.pre.json -o journal.json

    # 每画/删一段，记一笔（id 取自 line.add 的返回值）
    python scripts/journal.py record --journal journal.json \
        --net VDD --delete 101 --add 205,206

    # 收尾：对账
    easyeda pcb line-list --json > lines.post.json
    python scripts/journal.py verify --journal journal.json --final lines.post.json

    # 不看账、只想看某个网络现在有哪些 id（手填 --delete 用）
    python scripts/journal.py ids --lines lines.post.json --net VDD

退出码：0 账平（可能有 INFO）／1 有 FAIL／2 跑不了（缺 id、没记 rounds）。

对账只做两件事
--------------
`RT-JRN-05` 声明删除的 id 仍在 → 旧段没删就画了新的（叠画源头）。
`RT-JRN-06` 该网络的 id 集合变了，但没有对应的记账（删的没登记、加的没登记）。

切段自动豁免：消失的 id 若仍被最终铜覆盖、或新增的 id 落在基线铜范围内，
判为原生切段，只记 INFO。豁免靠几何投影覆盖比，不看段数。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

# 与 snap.py 共用同一套字段别名，避免两处解析各说各话
sys.path.insert(0, str(Path(__file__).resolve().parent))
from snap import _load, _point, _pick, _rows  # noqa: E402

ID_KEYS = ("id", "primitiveId", "lineId", "uuid", "gid", "primitive_id")
ID_ALT = ("ID", "Id", "primitiveID")

DEFAULT_COVER_RATIO = 0.9
DEFAULT_SEVERITY = {"RT-JRN-05": "FAIL", "RT-JRN-06": "WARN"}


def _ids(row) -> str | None:
    v = _pick(row, *(ID_KEYS + ID_ALT))
    if v is None or v == "":
        return None
    return str(v).strip()


def _num(v):
    f = round(float(v), 3)
    return int(f) if f == int(f) else f


def load_segs(path: str, where: str):
    """→ ({net: {id: [x1,y1,x2,y2,layer]}}, 无 id 段数)

    没有 id 的段照样计数，但不会进账 —— 对账必须靠 id，缺一个就是缺一个。
    """
    raw = _rows(_load(path), "lines", "traces", "tracks")
    out, no_id = {}, 0
    for i, t in enumerate(raw):
        net = _pick(t, "net", "netName", "net_name")
        if not net:
            raise SystemExit(f"{where} 第 {i + 1} 段走线缺 net")
        sid = _ids(t)
        if sid is None:
            no_id += 1
            continue
        tag = f"{where} 第 {i + 1} 段（{net} / id {sid}）"
        x1, y1 = _point(t, "start", tag)
        x2, y2 = _point(t, "end", tag)
        out.setdefault(str(net), {})[sid] = [
            _num(x1), _num(y1), _num(x2), _num(y2),
            int(_pick(t, "layer", "layerId", default=1)),
        ]
    return out, no_id


# ------------------------------------------------------------------ 几何：切段豁免

def _cover_ratio(seg, others, tol: float = 1.5) -> float:
    """seg 有多少比例被 others 里的共线段"沿岸铺满"。

    切段产物会这样：一整段变成两段，各自都在原位置附近。所以只比共线段
    （方向夹角 ≤3°、两端到 seg 所在直线的垂距 ≤tol），把这些段投影到 seg
    的一维坐标上求区间并集长度 —— 覆盖率高就说明铜没丢，只是 id 变了。
    """
    x1, y1, x2, y2, _layer = seg
    L = math.hypot(x2 - x1, y2 - y1)
    if L <= 0:
        return 0.0
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    spans = []
    for o in others:
        if int(o[4]) != int(seg[4]):
            continue
        ox1, oy1, ox2, oy2 = o[0], o[1], o[2], o[3]
        oL = math.hypot(ox2 - ox1, oy2 - oy1)
        if oL <= 0:
            continue
        if abs(ux * (oy2 - oy1) - uy * (ox2 - ox1)) / oL > 0.053:
            continue
        if max(abs((ox1 - x1) * uy - (oy1 - y1) * ux),
               abs((ox2 - x1) * uy - (oy2 - y1) * ux)) > tol:
            continue
        t0 = (ox1 - x1) * ux + (oy1 - y1) * uy
        t1 = (ox2 - x1) * ux + (oy2 - y1) * uy
        spans.append((max(0.0, min(t0, t1)), min(L, max(t0, t1))))
    if not spans:
        return 0.0
    spans.sort()
    covered, cur_s, cur_e = 0.0, spans[0][0], spans[0][1]
    for s, e in spans[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            covered += max(0.0, cur_e - cur_s)
            cur_s, cur_e = s, e
    covered += max(0.0, cur_e - cur_s)
    return covered / L


# ------------------------------------------------------------------ 命令

def cmd_baseline(args) -> int:
    segs, no_id = load_segs(args.lines, args.lines)
    total = sum(len(v) for v in segs.values())
    if no_id:
        print(f"拒绝记基线：{no_id} 段走线没有 primitiveId。", file=sys.stderr)
        print("  line-list 的导出里必须带 id（primitiveId / uuid 之一）。", file=sys.stderr)
        print("  没有 id 就只能比段数，而 EDA 会在交点自动切段 —— 那个比法必然误报。", file=sys.stderr)
        print("  导出时确认：easyeda pcb line-list --json 的每条记录含 id 字段。", file=sys.stderr)
        return 2
    if total == 0:
        print("拒绝记基线：这份导出里没有任何走线。", file=sys.stderr)
        print("  若是干净的板子（还没布线），基线就是空的，可以直接写空 journal：", file=sys.stderr)
        print('    {"version": 1, "baseline": {}, "rounds": []}', file=sys.stderr)
        return 2

    doc = {
        "version": 1,
        "captured": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "baseline": segs,
        "rounds": [],
    }
    _emit(doc, args.output)
    print(f"基线已记：{len(segs)} 个网络 / {total} 段带 id 的走线", file=sys.stderr)
    print("  接下来每次 line.add / line.delete 之后，用 record 记一笔。", file=sys.stderr)
    return 0


def cmd_record(args) -> int:
    doc = _load_journal(args.journal)
    net = args.net
    if not net:
        raise SystemExit("record 需要 --net")
    deleted = _id_list(args.delete)
    added = _id_list(args.add)
    if not deleted and not added:
        raise SystemExit("record 至少要给 --delete 或 --add，否则这笔账没有内容")

    base = doc.setdefault("baseline", {}).get(net, {})
    foreign = [i for i in deleted if i not in base]
    if foreign:
        print(f"提示：{len(foreign)} 个 id 不在 {net} 的基线里（{', '.join(foreign[:5])}）——"
              f"可能是上一轮自己画的。照记，对账时会核。", file=sys.stderr)

    doc.setdefault("rounds", []).append(
        {"net": net, "deleted": deleted, "added": added, "note": args.note or ""})
    _emit(doc, args.journal)
    print(f"已记：{net} 删 {len(deleted)} / 加 {len(added)}", file=sys.stderr)
    return 0


def cmd_ids(args) -> int:
    segs, no_id = load_segs(args.lines, args.lines)
    if args.net:
        segs = {args.net: segs.get(args.net, {})} if args.net in segs else {}
        if not segs:
            print(f"{args.lines} 里没有网络 {args.net}", file=sys.stderr)
            return 2
    for net in sorted(segs):
        rows = segs[net]
        print(f"{net}  ({len(rows)} 段)")
        for sid, s in sorted(rows.items(), key=lambda kv: str(kv[0])):
            print(f"  {sid:<12} L{int(s[4])}  {s[0]:.0f},{s[1]:.0f} → {s[2]:.0f},{s[3]:.0f}"
                  f"  ({math.hypot(s[2] - s[0], s[3] - s[1]):.0f}mil)")
    if no_id:
        print(f"\n⚠ {no_id} 段没有 primitiveId，未列出（对账用不了它们）", file=sys.stderr)
    return 0


def reconcile(doc: dict, final: dict, cover: float = DEFAULT_COVER_RATIO,
              sev_fail: str = DEFAULT_SEVERITY["RT-JRN-05"],
              sev_warn: str = DEFAULT_SEVERITY["RT-JRN-06"]):
    """对账主体：→ (items, stats)。final 是 {net: {id: [x1,y1,x2,y2,layer]}}。

    不读文件也不打印，`check.py --journal` 就是直接 import 这个函数调的，
    所以别把 I/O 混进来。
    """
    baseline = doc.get("baseline") or {}
    rounds = doc.get("rounds") or []
    items = []
    stats = {"nets": 0, "declared_del": 0, "declared_add": 0, "split": 0, "missing_added": 0}

    def add(sev, rule, where, issue, fix=""):
        items.append({"severity": sev, "rule": rule, "where": where,
                      "issue": issue, "fix": fix})

    by_net = {}
    for r in rounds:
        by_net.setdefault(str(r.get("net", "")), []).append(r)

    for net in sorted(set(baseline) | set(final)):
        base = baseline.get(net) or {}
        fin = final.get(net) or {}
        rs = by_net.get(net, [])
        declared_del, declared_add = set(), set()
        for r in rs:
            declared_del |= {str(x) for x in (r.get("deleted") or [])}
            declared_add |= {str(x) for x in (r.get("added") or [])}
        stats["nets"] += 1
        stats["declared_del"] += len(declared_del)
        stats["declared_add"] += len(declared_add)

        # ---- RT-JRN-05 声明删除的 id 还在：旧段没删就画了新的
        alive = sorted(declared_del & set(fin))
        for sid in alive:
            s = fin[sid]
            add(sev_fail, "RT-JRN-05", f"net {net} id {sid}",
                f"这笔账声明删掉 {sid}，但最终导出里它还在"
                f"（L{int(s[4])} {s[0]:.0f},{s[1]:.0f}→{s[2]:.0f},{s[3]:.0f}）："
                f"旧段没删就在上面叠了新线",
                f"先把 {sid} 删掉（pcb.line.delete），再重画；叠画出来的铜不会自己合并，"
                f"几何判据也只抓得到重叠 ≥80% 的那种")

        # ---- 基线 id 消失，但账上没登记删除
        lost, gained = [], []
        for sid in sorted(set(base) - set(fin) - declared_del):
            s = base[sid]
            ratio = _cover_ratio(s, list(fin.values()))
            if ratio >= cover:
                stats["split"] += 1
                add("INFO", "RT-JRN-05", f"net {net} id {sid}",
                    f"基线段 {sid} 的 id 消失了，但原位置仍被最终铜覆盖 {ratio * 100:.0f}%："
                    f"原生切段（不是丢失）", "")
            else:
                lost.append((sid, s, ratio))

        # ---- 最终新增 id，但账上没登记新增
        for sid in sorted(set(fin) - set(base) - declared_add):
            s = fin[sid]
            ratio = _cover_ratio(s, list(base.values()))
            if ratio >= cover:
                stats["split"] += 1
                add("INFO", "RT-JRN-05", f"net {net} id {sid}",
                    f"新段 {sid} 落在基线铜的 {ratio * 100:.0f}% 覆盖范围内："
                    f"原生切段（不是新增铜），免登记", "")
            else:
                gained.append((sid, s, ratio))

        for kind, rows, hint in (
            ("消失", lost, "确认是误删就补一笔 record --delete；是重画就补 --delete + --add"),
            ("新增", gained, "每次 line.add 之后立刻 record --add；漏记的段即使几何干净，"
                            "也没法证明它是本流程画的"),
        ):
            for sid, s, ratio in rows[:3]:
                desc = (f"原位只剩 {ratio * 100:.0f}% 的铜" if kind == "消失"
                        else f"与基线铜重合 {ratio * 100:.0f}%")
                add(sev_warn, "RT-JRN-06", f"net {net} id {sid}",
                    f"{'基线段' if kind == '消失' else '新段'} {sid} 没有记账"
                    f"（L{int(s[4])} {s[0]:.0f},{s[1]:.0f}→{s[2]:.0f},{s[3]:.0f}；{desc}）："
                    f"{kind}的段账上没有对应记录", hint)
            if len(rows) > 3:
                add(sev_warn, "RT-JRN-06", f"net {net}",
                    f"另有 {len(rows) - 3} 段{kind}没有记账（未逐条列出）",
                    f"用 journal.py ids --lines <最终导出> --net {net} 对照，再补 record")

        # 账本声明新增的 id，在最终导出里根本找不到。多半是被后续轮次删了，或者
        # line.add 的返回值与导出 id 不是一回事（真坑）—— 都要让人知道。
        gone = sorted(declared_add - set(fin) - declared_del)
        if gone:
            stats["missing_added"] += len(gone)
            add("INFO", "RT-JRN-05", f"net {net}",
                f"账本声明新增的 {len(gone)} 个 id 在最终导出里不存在"
                f"（{', '.join(gone[:5])}{'…' if len(gone) > 5 else ''}）",
                "通常是被后续轮次删掉、或被原生切成新 id；若确认没删，说明 line.add "
                "返回的 id 与导出的 id 不一致 —— 对账会失真，改用导出里的 id 重记")

    tally = {"FAIL": 0, "WARN": 0, "UNKNOWN": 0, "INFO": 0}
    for i in items:
        tally[i["severity"]] += 1
    if tally["FAIL"] == 0 and tally["WARN"] == 0:
        add("INFO", "RT-JRN-OK", "全板",
            f"账目对得上：{stats['nets']} 个网络 / 声明删 {stats['declared_del']} / "
            f"声明加 {stats['declared_add']} / 切段豁免 {stats['split']}",
            "过程干净不等于几何合规 —— 之后还要跑 check.py 的几何判据")
    return items, stats


def cmd_verify(args) -> int:
    doc = _load_journal(args.journal)
    if not (doc.get("rounds") or []):
        print("对账不了：journal 里没有 rounds（一条操作记录都没有）。", file=sys.stderr)
        print("  只记了基线不算记账 —— 没有操作记录就无法区分「我画的」和「别人画的」。",
              file=sys.stderr)
        print("  布线时每步用 record 记一笔再跑 verify。", file=sys.stderr)
        return 2

    final, no_id = load_segs(args.final, args.final)
    if no_id:
        print(f"对账中止：最终导出里 {no_id} 段走线没有 primitiveId。", file=sys.stderr)
        print("  缺 id 就无法核对旧段删没删 —— 这不算通过。", file=sys.stderr)
        return 2

    items, stats = reconcile(doc, final, float(args.cover_ratio),
                             args.sev_fail, args.sev_warn)
    counts = {"FAIL": 0, "WARN": 0, "UNKNOWN": 0, "INFO": 0}
    for i in items:
        counts[i["severity"]] += 1

    if args.json:
        print(json.dumps({"summary": counts, "items": items, "stats": stats},
                         ensure_ascii=False, indent=2))
    else:
        icon = {"FAIL": "✗", "WARN": "!", "UNKNOWN": "?", "INFO": "·"}
        print(f"过程对账：FAIL {counts['FAIL']} / WARN {counts['WARN']} / "
              f"UNKNOWN {counts['UNKNOWN']} / INFO {counts['INFO']}\n")
        for i in items:
            print(f"[{i['severity']:<7}] {icon[i['severity']]} {i['rule']:<12} {i['where']}")
            print(f"        {i['issue']}")
            if i["fix"]:
                print(f"        → {i['fix']}")
        print(f"\n账目：{stats['nets']} 网络 / 声明删 {stats['declared_del']} / "
              f"声明加 {stats['declared_add']} / 切段豁免 {stats['split']} / "
              f"加而不见 {stats['missing_added']}")

    if args.output:
        Path(args.output).write_text(
            json.dumps({"summary": counts, "items": items, "stats": stats},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if counts["FAIL"] else 0


# ------------------------------------------------------------------ 辅助

def _id_list(text) -> list[str]:
    if not text:
        return []
    if isinstance(text, list):
        return [str(x).strip() for x in text if str(x).strip()]
    return [p.strip() for p in str(text).replace(" ", ",").split(",") if p.strip()]


def _load_journal(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"找不到 journal：{path}\n  先跑 journal.py baseline --lines <布线前的 line-list 导出>")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "baseline" not in doc:
        raise SystemExit(f"{path} 不是 journal 文件（缺 baseline 键）")
    return doc


def _dumps(obj, level: int = 0) -> str:
    """紧凑 JSON：短的标量数组写一行（坐标、id 列表），长数组和对象才换行。

    标准 indent=2 会把 [x1,y1,x2,y2,layer] 摊成 7 行，一段线占 9 行 ——
    journal 要能人眼看、能手改，摊开就没法看了。
    """
    pad, pad2 = "  " * level, "  " * (level + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        body = ",\n".join(f"{pad2}{json.dumps(k, ensure_ascii=False)}: {_dumps(v, level + 1)}"
                          for k, v in obj.items())
        return "{\n" + body + "\n" + pad + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if len(obj) <= 6 and all(not isinstance(x, (dict, list)) for x in obj):
            return "[" + ", ".join(_dumps(x, level + 1) for x in obj) + "]"
        body = ",\n".join(pad2 + _dumps(x, level + 1) for x in obj)
        return "[\n" + body + "\n" + pad + "]"
    return json.dumps(obj, ensure_ascii=False)


def _emit(doc, path):
    text = _dumps(doc) + "\n"
    if path:
        Path(path).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="primitiveId journal：布线过程记账与对账")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("baseline", help="布线前记基线（per-net id + 几何）")
    b.add_argument("--lines", required=True, help="布线前的 line-list 导出")
    b.add_argument("-o", "--output", help="journal 输出路径，默认 stdout")
    b.set_defaults(func=cmd_baseline)

    r = sub.add_parser("record", help="记一笔操作（每次 add/delete 后追加）")
    r.add_argument("--journal", required=True)
    r.add_argument("--net", required=True)
    r.add_argument("--delete", help="删掉的 id，逗号分隔")
    r.add_argument("--add", help="新增的 id，逗号分隔（取自 line.add 返回值）")
    r.add_argument("--note", help="备注，例如「VDD 绕开 C7」")
    r.set_defaults(func=cmd_record)

    v = sub.add_parser("verify", help="收尾对账：基线 + 账本 vs 最终导出")
    v.add_argument("--journal", required=True)
    v.add_argument("--final", required=True, help="布线后的 line-list 导出")
    v.add_argument("--cover-ratio", type=float, default=DEFAULT_COVER_RATIO,
                   help=f"切段豁免的覆盖阈值，默认 {DEFAULT_COVER_RATIO}")
    v.add_argument("--json", action="store_true")
    v.add_argument("-o", "--output", help="报告另存为 JSON")
    v.add_argument("--sev-fail", default=DEFAULT_SEVERITY["RT-JRN-05"],
                   help=f"RT-JRN-05 的级别，默认 {DEFAULT_SEVERITY['RT-JRN-05']}")
    v.add_argument("--sev-warn", default=DEFAULT_SEVERITY["RT-JRN-06"],
                   help=f"RT-JRN-06 的级别，默认 {DEFAULT_SEVERITY['RT-JRN-06']}")
    v.set_defaults(func=cmd_verify)

    i = sub.add_parser("ids", help="列出某个网络的 id（手填 --delete 时用）")
    i.add_argument("--lines", required=True)
    i.add_argument("--net")
    i.set_defaults(func=cmd_ids)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
