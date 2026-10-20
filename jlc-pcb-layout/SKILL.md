---
name: jlc-pcb-layout
description: 在嘉立创EDA专业版里把 PCB Layout 真正画出来的规范驱动执行技能。当用户要求画 PCB / 做 layout / 摆件 / 走线 / 铺铜 / 拼板 / 按规范检查板子 / 把原理图变成可投产的板子 / 评估某块板是否合规时使用。内置华为、艾默生、华硕、上海贝尔、车规与通用 DFM 六套规范的量化条款（元器件摆放顺序与间距、朝向与极性、器件高度与重心、热与可维护性、载流量、电气间距、过孔、叠层），并提供载流线宽计算器、布局坐标计划生成器、合规检查器，通过 easyeda CLI 或 eda.pcb_* API 落笔到画布。触发词：画PCB、PCB layout、布局布线、摆件、元器件摆放、器件间距、走线、铺铜、DRC、设计规范、载流、线宽、爬电距离、拼板、嘉立创EDA、立创EDA、easyeda。
---

# 嘉立创EDA PCB Layout 规范驱动绘制

## 这个 skill 解决什么

普通做法是"给模型一堆规范 PDF，让它写一堆建议"。本 skill 反过来：**规范先变成数字和约束表，约束先变成坐标计划，坐标计划再变成 EDA 里真实的图元**。你（Codex）的角色是执行这条流水线，而不是复述规范。

三层资产：

| 层 | 内容 | 在哪 |
|---|---|---|
| 规范层 | 五套企业规范的量化条款（带出处编号） | `references/*.md` |
| 计算层 | 载流线宽、电气间距、过孔尺寸、阻抗、器件间距查表与求解 | `scripts/rules.py` + `assets/constraints.json` |
| 执行层 | 布局坐标计划生成 + 合规检查 + EDA 落笔命令 | `scripts/plan.py`、`scripts/check.py`、`references/eda-execution.md` |

## 四条硬规矩

1. **不许凭记忆报工艺数值**。任何"线要多宽 / 间距多少 / 过孔多大 / 爬电多少 / 器件间距多少"的问题，先跑 `rules.py`，或查 `assets/constraints.json`。查不到就说不确定，并给出保守取值 + 标注假设。
2. **不许凭感觉摆坐标**。所有 XY 必须来自 `plan.py` 生成的计划文件（或用户/结构图给定的锚点），并在计划里落盘后可追溯。禁止即兴 `x = 1000`。
3. **不许乱序摆件**。摆器件必须按 `P0 → P6` 的档位顺序（见 `references/layout-rules.md` §11 与 `constraints.json → placement.placement_order`）：机械锁定件 → 功率热源 → 核心 IC → 就近器件（去耦/串阻/晶振）→ 敏感小信号 → 其余阻容 → 测试点/MARK/丝印。**前一档未锁定不得进入下一档。** 尤其是 P3 的就近器件，禁止为了"阵列好看"把它们从 IC 电源脚旁挪走——裁决顺序永远是**电气 > 结构 > 制造 > 整齐**。
4. **不许无验证收工**。落笔后必须跑 `check.py` / `drc-check`，把违规逐条回修或明确记录为"已接受的风险"。

## 先判断任务模式

| 用户说 | 模式 | 走哪条路 |
|---|---|---|
| "帮我画 / 布一块 XX 板" | **新建** | 全流水线 A→E |
| "这块板帮我看下 / 这样布对不对" | **审查** | 只读：`check.py` + `references/*` 对照，不改图 |
| "3A 电流用多宽的线" 类单点问题 | **速查** | `rules.py` 一条命令出答案 |
| "已经画了一部分，继续 / 改这里" | **改造** | 读现状 → 局部重排 → 局部重布 → 验证 |

信息不全时按"最保守可制造"假设并在结果里**显式列出假设**，不要卡在提问上。

## 流水线（新建模式）

### A. 建板与叠层

先定 4 件事，缺一不可：板框尺寸、层数、铜厚、表面处理。

- 板框：`pcb.outline.set`（CLI `easyeda pcb outline-set`），传闭合多边形 `[[x,y],…]`，单位 mil、y 向上。圆角用 `pcb outline-round --radius` 或折线逼近。
- 层数选题：跑 `rules.py layers --pins <总管脚数> --area-mm2 <板面积>` 得到 Pin 密度建议层数（来源 TY-11~TY-17）。时钟 ≥5MHz 或上升沿 <5ns 直接上多层（TY-63）。
- 叠层：查 `references/stackup-and-impedance.md`，**必须对称**，信号层夹在平面层之间，相邻信号层走线正交。
- 铜厚：默认 1oz(35µm)。有 >2A 电流或散热需求时上 2oz。

### B. 出布局计划（不要跳过）

```bash
python scripts/plan.py assets/plan.example.json -o my_plan.json --emit-steps steps.json
```

计划文件描述：板框、边距带、锁定锚点（连接器 / 安装孔 / 结构件）、功能块（模块名 + 成员 + 优先级 + 靠边偏好）。`plan.py` 会：

1. 按模块面积和连线强度做**瓦片式分区**（块不重叠，块间留 gutter）；
2. 块内按"高→低、大→小"做货架式排布，锚点当障碍物绕行；
3. 输出每颗器件的 `x, y, rotation, layer`（mil，y-up，25mil 网格对齐）；
4. `--emit-steps` 顺手输出可 `easyeda apply` 的 playbook（`--emit-sh` 输 shell 脚本）。

计划生成后**先读一遍**（`--report` 只看块占用），重点看：电源块是否靠输入边、高速块是否靠接口、去耦电容是否贴着对应的 IC 引脚。不合理就改计划文件的 `blocks[].edge` / `priority` 重跑，别在画布上手工纠偏。

**摆件顺序对计划文件的要求**：`blocks[].priority` 必须映射成 P0→P6 档位（P0 机械锁定件、P1 功率热源、P2 核心 IC、P3 就近器件、P4 敏感小信号、P5 其余阻容、P6 测试点/MARK/丝印）。`plan.py` 出坐标后，落笔时**按档位分批执行，每批做完回读一次**，不要一把梭。P3 档（去耦电容 / 串联匹配电阻 / 晶振 + 负载电容）在计划里要显式列出，不允许被 P5 的"阵列对齐"逻辑带跑。

### C. 落笔

按 `references/eda-execution.md` 里的顺序和命令表执行：板框 → 从原理图导入 → 摆件（先锁锚点）→ 关键信号 → 电源 → 其余 → 过孔缝合 → 铺铜。

关键纪律：

- 每摆完一个功能块就 `pcb.components.list --include-bbox` 回读一次，确认位置真的落上去了。
- 每次只做一类操作，做完立刻验证。EDA 没有 undo。
- 走线用 `pcb.line.create`，一段一段来，`net` 必须写对网络名（从 `pcb.nets.list` 取，别拼）。
- 铺铜 `net` 不能为空（空网 = 死铜）。
- 多步批量别写 shell 胶水，写成 playbook 先 `easyeda apply steps.json --dry-run`。

**走线阶段必读** `references/routing-aesthetics.md`。用户说"布得整齐点""要好看"，或你要判断某处"不整齐"该不该改时，去那里查判据——它把"美观"拆成了角度、对齐、间距一致性、线宽连续性、图元从属关系 5 个可测量，还给了一个明确的裁决顺序（电气 > 结构 > 制造 > 整齐）。**别凭感觉说"这样不好看"，要么给出判据编号，要么承认是个人偏好。** 其中对齐、阵列间距、正交朝向、45° 走线这 4 项已由 `check.py` 的 `AEST-*` 规则自动判（见 D 节）。

### D. 验证

```bash
python scripts/check.py my_board.json      # 规范符合性（本 skill 的规则库）
easyeda pcb drc-check                      # EDA 原生 DRC：间距/短路/未布通
easyeda pcb check                          # DFM 级：悬空端/酸角/丝印翻转/孤铜
easyeda pcb report                         # 网络长度/差分偏斜
```

`check.py` 输出的每条违规都带**规则号**（如 `TY-6.5.6-12`、`AEST-ALIGN`、`EM-5.4.11`、`FAB-SHORT`），可回 `references/` 定位原文。严重度分三级：

- `FAIL` — 违反硬约束（叠板、短路风险、工艺做不出来）→ 必须改；
- `WARN` — 违反推荐值（间距偏小、去耦偏远）→ 改，或说明为什么能接受；
- `INFO` — 数据不足无法判定（缺板框、缺高度、缺铜皮几何）→ 记录，不假装通过。

**图面整洁度检查**（`AEST-*`，判据见 `references/routing-aesthetics.md`）已能自动跑：

| 规则号 | 查什么 | 阈值（`assets/constraints.json` → `aesthetics`） |
|---|---|---|
| `AEST-ALIGN` | 同组器件是否共线 | 多数在 ±12mil 内、个别掉队即报；基准取中位数 |
| `AEST-PITCH` | 同组阵列间距是否均匀 | 相邻中心距变异系数 >25% |
| `AEST-ROT` | 是否只朝 0/90/180/270 | 非 90° 整数倍 |
| `AEST-ANGLE` | 走线是否只落在 0/45/90 | 偏离 >4° 且线长 >3W |

丝印字高、丝印压焊盘、死铜**不判定**——快照里没有丝印图元与铺铜对象，检查器会输出 `AEST-DATA` 的 INFO 明说"未判定"，别把它当成通过。`examples/snapshot.aesthetics.json` 是这四个规则的活样例（含一条**不该报**的交错双排，用来验证不会滥报）。

**元器件摆放规则**（`PL-*`，判据见 `references/layout-rules.md` §9/§12~§15）已能自动跑：

| 规则号 | 查什么 | 阈值（`assets/constraints.json` → `placement`） |
|---|---|---|
| `PL-ORI-POL` | 有极性器件朝向种类与正交性 | >2 种报；=2 种时夹角必须 90° |
| `PL-ORI-IC` | 同封装 IC 的 1 脚朝向是否一致 | 出现 >1 种即报 |
| `PL-HEIGHT-MAX` | 器件是否超过贴片设备高度上限 | >25mm |
| `PL-HEIGHT-MIX` | 高器件与矮件是否贴太近 | 高件 ≥6mm 时与矮件间距 <2mm |
| `PL-AUTO-IND` | 电感间距与正交（车规） | 间距 <5mm，或轴向同向 |
| `PL-MECH-CG` | 器件重心是否偏出中心 | 偏移 > 半板尺寸的 25% |
| `PL-MAINT-CONN` | 插拔连接器周围净空 | 邻件间距 <3mm |
| `PL-FID-N` / `PL-FID-SPREAD` | MARK 数量与分布跨距 | <3 个；跨距 < 板尺寸 25% |

`PL-HEIGHT-*` 需要快照里有 `height_mm`，`PL-FID-*` 需要 `fiducials` 数组，`PL-AUTO-*` 需要 `board.automotive = true`。**缺字段时输出 INFO 明说"未判定"，不是通过**——要判定就把高度从封装库/BOM 补进 snapshot。

`examples/snapshot.placement.json` 是这八类的活样例（`FAIL 0 / WARN 10`），改完摆放后跑它对照。`PL-MAINT-CONN` 只对 `J/USB/DC/RJ/CN/P` 前缀的**插拔连接器**生效，安装孔 `H/MH` 不算——安装孔不需要插拔净空，算进来全是误报。

### E. 交付

输出物：计划 JSON、检查报告、改动清单（哪些器件动了、哪些网络重布了）、未解决项。**不要**只说"已完成"。

## 画的时候脑子里要有的数字

这些是最高频的，完整表在 `references/`：

| 项 | 数值 | 出处 |
|---|---|---|
| 走线载流（1oz, ΔT=10℃） | 0.5mm→1.35A；1mm→2.3A；2mm→4.0A；铜皮作导线再降额 50% | TY-18/19/22 |
| 国内推荐最小线宽/间距 | 0.25mm / 0.3mm（4mil/4mil 是极限） | TY-43/44 |
| 上海贝尔四档走线 | 0.30 / 0.20 / 0.15 / 0.12 mm，配套焊盘与过孔表 | BELL-16/17/18 |
| 电气间距（一次侧 300V） | 空气间隙 1.7mm，爬电 3.2mm | TY-28 |
| 过孔孔径 vs 板厚 | 1.6mm 板 → ≥12mil；2.0mm → ≥16mil；孔厚比 ≤5~8 | TY-6.3.1.2.5.1 |
| 标准过孔 | 内径/外径 12/25、16/30、20/35、24/40、32/50 mil | EM-5.6.5 |
| 器件间距 | 同种贴片 ≥0.3mm；异种 ≥0.13h+0.3mm；手贴 ≥1.5mm | EM-5.4.11 |
| SMD↔SMD / SMD↔通孔 / 通孔↔通孔 | 0.5 / 1.0 / 1.5mm（推荐 0.75 / 1.5 / 2.0mm） | DFM 通用 |
| PLCC·QFP·SOP 相互 / 与 Chip·SOT | ≥2.5mm / ≥1.5mm | 通用 SMT |
| SMT 焊盘边到边 | ≥0.15mm（0201/01005 ≥0.12mm） | IPC-7351B |
| 器件本体到板边 / 到拼板导轨边 | ≥2mm（推荐 3mm） / ≥3mm（推荐 5mm） | DFM 通用 |
| 器件高度 | 贴片设备上限 25mm；高件(≥6mm)与矮件间距 ≥2mm | DFM 通用 |
| 重心偏移 | 不得超过半板尺寸的 25%（不落入板边缘 1/4 区域） | 机械装配 |
| 插拔件周围净空 | ≥3mm | EM-5.4.6 |
| 极性标识方向 | 全板 ≤2 个，且必须互相垂直 | 检验口径 |
| 基准点 | ≥3 个（2 对角 + 1 定向），非对称，焊盘 φ1.0mm | 通用 SMT |
| BGA 禁布区 | 3mm（最佳 5mm） | EM-5.4.10 / TY-6.2.10 |
| 安装孔禁布 | M3 → φ8.6mm；M2.5 → φ7.6mm；M4 → φ10.6mm | EM-表5 |
| 板边到铜 | V-CUT 边 >0.75mm；铣槽边 >0.3mm | EM-5.5.1 |
| 定位孔 | φ3.0mm，孔心距板边 5.08mm，距 SMD 本体 ≥200mil | BELL-7/14 |
| 基准点 | φ40mil±1mil，阻焊窗 φ80mil，保护环 φ110/φ90，中心距边 ≥5mm | EM-5.7.2 |
| 测试点 | 焊盘 ≥24mil，中心距 ≥60mil(ICT 规范 2.54mm) | TY-65/66、BELL-24 |
| 去耦电容 | 2 层板 ≤150mil 贴电源脚；4 层以上 ≤250mil 但过孔必须压在 pad 旁 | 见 layout-rules.md |
| 串联匹配电阻 | 距驱动端 ≤500mil | TY-10 |
| 晶振 | 体中心距 MCU ≤300mil，负载电容对称，下方铺地不穿线 | TY-7.2.2 |
| 平面分割宽度 | 电位差 >12V → 50mil；≤12V → 20~25mil | TY-56/57 |

## 与其它技能的关系

- 本 skill 负责**规范 + 计算 + 坐标 + 检查**（"画什么、画多宽、画在哪"）。
- 若环境里装了 `easyeda-agent`，它负责**落笔执行**（CLI 动作集）；两者用同一个数据契约（mil、y-up、layer id），可直接对接。
- 若装了 `easyeda-api`，可用 bridge 直接跑 `eda.pcb_*` JS —— 见 `references/eda-execution.md` 通道 B。
- 若只要"设计文档套件"（任务书 / BOM / 原理图要点）而不画图，用 `jlc-pcb-design`。

## 命令速查

```bash
# 计算（唯一允许的数值来源）
python scripts/rules.py width     --current 3 --copper 1oz [--pour] [--delta-t 10]
python scripts/rules.py clearance --voltage 300 --side primary|secondary [--input-range 150-300|300-600]
python scripts/rules.py via       --thickness 1.6 [--drill 12]
python scripts/rules.py layers    --pins 320 --area-mm2 2500 [--clock-mhz 24] [--rise-ns 2]
python scripts/rules.py spacing   --trace 0.2
python scripts/rules.py impedance --type microstrip|stripline|diff-microstrip|diff-stripline \
                                  --w 0.25 --h 0.20 [--s 0.15] [--t 0.035] [--er 4.3] [--target 90]
# 任何子命令都可加 --json

# 计划
python scripts/plan.py my_spec.json -o plan.json [--report] [--emit-steps steps.json] [--emit-sh] [--project <名>]

# 检查
python scripts/check.py snapshot.json [--min-severity FAIL|WARN|INFO] [--json] [--strict]
```

`check.py` 覆盖 20 类：出框、板边净空、courtyard 间距、BGA 禁布、安装孔禁布、去耦距离（按最近 IC）、晶振距离、朝向一致性、**有极性件朝向与正交**、**同封装 IC 的 1 脚朝向**、**器件高度与高矮混放**、**电感间距与正交（车规）**、**重心平衡**、**连接器插拔净空**、**MARK 数量与分布**、悬空线头、直角/锐角、3W 与异网短路、过孔孔厚比、图面整洁度。数据缺就降级为 `INFO`，**不会假装通过**。

## 装在哪、先自检

```bash
# Codex / 本机 skill 目录
ls ~/.codex/skills/jlc-pcb-layout          # 或 ~/.workbuddy/skills/jlc-pcb-layout
cd ~/.codex/skills/jlc-pcb-layout
python scripts/rules.py width --current 3 --copper 1oz    # 应输出 1.5mm / 3.2A
python scripts/plan.py assets/plan.example.json -o /tmp/p.json --report
python scripts/check.py examples/snapshot.demo.json --min-severity WARN
```

三条都能跑通，说明 Python 3.9+ 环境、JSON 与相对路径都正常。第一次用先跑这三条，别直接上手画板。

## 红线

- 不擅自删器件、不擅自清空布线、不擅自改板框 —— 这三类先问。
- 不在 `.epro`/工程文件层面做二进制修改，一切通过 API/CLI。
- 不把"自动布线跑通了"当成"布好了"：自动布线的结果必须逐网络看 3W、45°、回流路径。
- 不虚构 DRC 结果。没跑就是没跑。

## 文件索引

```
SKILL.md
references/
  layout-rules.md            布局规范（边距/分区/热/去耦/间距/朝向/**摆放顺序 P0→P6**/**高度**/**重心**/**可维护性**/**MARK**/**车规**/拼板）
  routing-rules.md           布线规范（载流/间距/过孔/平面/20 条走线规则）
  routing-aesthetics.md      布线美观：整洁的可量化判据 + 美观与性能的裁决顺序
  stackup-and-impedance.md   叠层设计与阻抗计算
  dfm-assembly-rules.md      工艺与组装（courtyard/V-cut/钢网/基准点/测试点）
  eda-execution.md           两条执行通道 + 落笔顺序 + 已知坑
  layout-plan.md             布局计划 JSON 规格
scripts/
  rules.py                   计算与查表（线宽/间距/过孔/阻抗/层数）
  plan.py                    布局坐标计划生成器（输出 playbook / shell）
  check.py                   规范符合性检查器（布局 16 类 + 走线 4 类 + 整洁度 4 类）
assets/
  constraints.json           全部数值约束（机器可读，唯一真源）
  plan.example.json          计划文件示例（60×45 四层板）
examples/
  walkthrough.md             一块板从需求到投板的完整走查（含真实输出）
  snapshot.demo.json         有典型毛病的板子快照（喂 check.py 看报什么）
  snapshot.demo.fixed.json   回修后的同一块板（FAIL 0，剩 5 条 WARN）
  snapshot.aesthetics.json   整洁度反面样例（对齐/间距/朝向/角度各一条，另含不该报的交错双排）
  snapshot.placement.json    元器件摆放专项反面样例（FAIL 0 / WARN 10，PL-* 八类各中一条）
```

## 命令写法约定

动作名一律用**类型化 id**（`pcb.outline.set`、`pcb.line.create`）。CLI 里对应 kebab-case 子命令（`easyeda pcb outline-set`、`easyeda pcb line-create`），个别版本用空格分词（`easyeda pcb drc check`）。**报参数前先 `easyeda actions` 或 `easyeda pcb --help` 对一遍**，以它的输出为准——本文只保证语义正确。
