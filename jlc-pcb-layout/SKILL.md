---
name: jlc-pcb-layout
description: 在嘉立创EDA专业版里按客户需求做原理图、再把 PCB Layout 真正画出来的规范驱动执行技能。当用户要求做原理图 / 画电路 / 按需求设计板子 / 选型 / 出网表 / 出 BOM / 查 ERC，或要求画 PCB / 做 layout / 摆件 / 走线 / 铺铜 / 拼板 / 按规范检查板子 / 评估某块板是否合规时使用。原理图侧内置 15 个功能块参考电路（电源/主控/RS485/CAN/USB-C/继电器/按键/LED/I2C/ADC/蜂鸣器）与 23 条 ERC 规则，可从一句需求生成网表并查错；PCB 侧内置华为、艾默生、华硕、上海贝尔、车规与通用 DFM 六套规范的量化条款（摆放顺序与间距、朝向与极性、高度与重心、热与可维护性、载流量、电气间距、过孔、叠层），提供线宽/间距/过孔/阻抗计算器、布局坐标计划生成器、合规检查器，通过 easyeda CLI 或 eda.sch_* / eda.pcb_* API 落笔到画布。触发词：原理图、画电路、电路设计、选型、网表、BOM、ERC、MCU 最小系统、485、CAN、Type-C、继电器驱动、画PCB、PCB layout、布局布线、摆件、走线、铺铜、DRC、设计规范、载流、线宽、爬电距离、拼板、嘉立创EDA、立创EDA、easyeda。
---

# 嘉立创EDA 原理图 + PCB Layout 规范驱动设计

## 这个 skill 解决什么

普通做法是"给模型一堆规范 PDF，让它写一堆建议"。本 skill 反过来：**需求先变成可核查的网表，网表先变成坐标计划，计划再变成 EDA 里真实的图元**。你（Codex）的角色是执行这条流水线，而不是复述规范。

两块能力，共用一套数据契约：

```
客户需求 ──schem.py──> 网表(器件+网络) ──ERC──> 落笔原理图 ──> 导入PCB ──plan.py──> 布局计划 ──> 走线 ──check.py──> 投板
          (原理图)                      ↑ 不合规停在这                 (PCB Layout)
```

| 层 | 内容 | 在哪 |
|---|---|---|
| 需求层 | 需求澄清清单、工作流门禁、选型纪律 | `references/schematic-workflow.md` |
| 电路层 | 15 个功能块参考电路（含必接外围与常见错误） | `assets/circuit-blocks.json` + `references/circuit-blocks.md` |
| 芯片实例层 | 5 颗具体型号：真实脚号 / 料号 / 块级布局约束 / 出处 | `assets/chip-variants.json` + `references/chip-variants.md` |
| 原理图规则层 | 23 条 ERC 判据 + 数值表 | `references/schematic-rules.md` |
| 规范层 | 六套企业规范的量化条款（带出处编号） | `references/layout-rules.md` 等 |
| 计算层 | 载流/间距/过孔/阻抗 + 上拉/限流/负载电容/偏置/电感 | `scripts/rules.py`、`scripts/schem.py calc`、`assets/constraints.json` |
| 执行层 | 网表生成、ERC、落笔脚本、布局计划、合规检查 | `scripts/schem.py`、`scripts/plan.py`、`scripts/check.py`、`references/eda-execution.md` |

## 九条硬规矩

1. **不许凭记忆报工艺数值**。任何"线要多宽 / 间距多少 / 过孔多大 / 爬电多少 / 器件间距多少"的问题，先跑 `rules.py`，或查 `assets/constraints.json`。查不到就说不确定，并给出保守取值 + 标注假设。
2. **不许凭记忆报电路数值**。上拉、限流电阻、晶振负载电容、RS485 偏置、buck 电感、TVS 档位，先跑 `schem.py calc`，或查 `constraints.json → schematic`。
3. **不许编料号**。LCSC 编号只能来自立创商城实际检索。脚本没有 `lcsc` 就拒绝生成落笔代码（exit 2），`--allow-missing` 只用于先看布局，不是绕过门禁的手段。**编料号会造成批量买错料，是这类工具最贵的错误。**
4. **不许凭感觉摆坐标**。所有 XY 必须来自 `plan.py` 生成的计划文件（或用户/结构图给定的锚点），并在计划里落盘后可追溯。禁止即兴 `x = 1000`。
5. **不许乱序摆件**。摆器件必须按 `P0 → P6` 的档位顺序（见 `references/layout-rules.md` §11 与 `constraints.json → placement.placement_order`）：机械锁定件 → 功率热源 → 核心 IC → 就近器件（去耦/串阻/晶振）→ 敏感小信号 → 其余阻容 → 测试点/MARK/丝印。**前一档未锁定不得进入下一档。** 尤其是 P3 的就近器件，禁止为了"阵列好看"把它们从 IC 电源脚旁挪走——裁决顺序永远是**电气 > 结构 > 制造 > 整齐**。
6. **不许把「没查」说成「通过」**。`check.py` 的 `UNKNOWN` 表示这条检查没跑成（缺 traces、缺高度、缺丝印、块级约束无几何判据）。有 UNKNOWN 时判定是 `INCOMPLETE`，**不是通过**。交付物里必须列出哪些项没查，不许只报 `FAIL 0`。
7. **不许无验证收工**。原理图阶段必须 ERC 过 FAIL=0 且 EDA 原生 `sch_Drc` 通过；PCB 阶段必须跑 `check.py` + `drc-check`。违规逐条回修或明确记录为"已接受的风险"。
8. **不许用自动布线 / 自动布局**。走线只能 `pcb.line.create` 逐段写；`eda.pcb_Document.autoRouting()`、EDA 菜单里的自动布线、任何 auto-route 类动作、以及"导出 DSN/SES 交给外部路由器"这类替代路线，一个都不许碰。一次调用改动整块板、EDA 没有 undo、单次最长 900 秒、报错时无法逐段归因——这四条决定了它一旦跑起来就不可控。用户明确要求时，先讲清后果、拿到确认，再记进交付物的「未按规范验证项」。
9. **改线先删后画，动一次记一笔。** 改某网络的走向、或给它续一段之前，先把要替换的旧段 `pcb.line.delete` 掉，再画新的；**不许在旧线上叠着画**（EDA 不会自动合并同网重叠铜，也没有 undo）。每次 create/delete 之后立刻把那一段的 primitiveId 记进 `journal.py` 的账本，收尾 `journal.py verify` 对账。`RT-JRN-05` FAIL = 账上声明删了、画布上还在；`RT-JRN-06` WARN = 有增删没记账。**段数对不上不算出错，id 对不上才算** —— EDA 会在交点自动切段。

## 先判断任务模式

| 用户说 | 模式 | 走哪条路 |
|---|---|---|
| "客户要个 XX 板 / 帮我做原理图" | **原理图新建** | 第一部分 A→E |
| "这个电路对不对 / 帮我看看原理图" | **原理图审查** | 只读：`schem.py erc` + `references/schematic-rules.md`，不改图 |
| "LED 串多大电阻 / I2C 上拉多少" 类单点问题 | **速查** | `schem.py calc` 一条命令出答案 |
| "帮我画 / 布一块 XX 板" | **PCB 新建** | 第二部分 A→E |
| "这块板帮我看下 / 这样布对不对" | **PCB 审查** | 只读：`check.py` + `references/*` 对照，不改图 |
| "3A 电流用多宽的线" 类单点问题 | **速查** | `rules.py` 一条命令出答案 |
| "已经画了一部分，继续 / 改这里" | **改造** | 读现状 → 局部重排 → 局部重布 → 验证 |

信息不全时按"最保守可制造"假设并在结果里**显式列出假设**，不要卡在提问上。但涉及安规、隔离电压、负载类型这三类，必须问（见 `schematic-workflow.md` §11）。

---

# 第一部分：原理图（需求 → 网表）

## A. 需求澄清

先看 `references/schematic-workflow.md` §1 的必答清单（主控、电压电流、负载类型、接口标准、是否隔离、温度、结构、认证）。问不到的写进 `open_questions` 并在交付时标红。

## B. 需求 → 设计规格

```bash
python scripts/schem.py spec --req 需求.txt -o spec.json
```

输出块清单、电源树草稿、假设、待确认。**这一步的产物是草稿，必须人工改三件事**：

1. 删掉误识别的块（"继电器驱动电磁阀"会同时命中 `relay_drive` 和 `mosfet_load`，看 `review` 字段的提示）
2. 补 `rails[].i_a`（电流自动解析不可能知道）
3. 补块的 `params`（UART 编号、晶振频率、线圈电流、I2C 速率）

参考成品：`examples/spec.example.json`。

## C. 规格 → 网表

```bash
python scripts/schem.py netlist spec.json -o netlist.json --report
```

自动做四件事：分配位号、解析块内局部网络（多实例不串）、补外围（去耦/偏置/终端/续流）、标出待绑定器件。`--report` 打出电源网络引脚数与待确认清单——**引脚数为 0 或 1 说明电源树没接对**。

块库 15 个：`power_protect` / `power_ldo` / `power_buck` / `mcu_min` / `usb_typec_ufp` / `rs485` / `can` / `relay_drive` / `mosfet_load` / `key` / `led_ind` / `i2c_port` / `uart_port` / `adc_divider` / `buzzer`。每块的必接外围与常见错误见 `references/circuit-blocks.md`。

## D. ERC

```bash
python scripts/schem.py erc netlist.json [--strict] [--json]
```

23 条规则（判据表在 `references/schematic-rules.md` §9）：

| 严重度 | 典型规则 | 处理 |
|---|---|---|
| `FAIL` | `SCH-USB-CC`（CC 缺 5.1k）、`SCH-LED-RES`（LED 无限流）、`SCH-RELAY-FLYBACK`（线圈无续流）、`SCH-TVX-VRWM`（TVS 选小） | 必须改，这些会烧或根本不动 |
| `WARN` | `SCH-PWR-DECOUPLE`、`SCH-I2C-PULLUP`、`SCH-MCU-RESET`、`SCH-RS485-TERM` | 改，或书面说明为什么可接受 |
| `INFO` | `SCH-BINDING`（未选型）、`SCH-ERC-SINGLE`（MCU 未绑定时的控制网）、`SCH-DATA`（缺电压） | 逐条确认，**不能当通过** |

`examples/netlist.bad.json` 是埋了 4 处 FAIL 的反面样例，用来验证检查器真的会报。

## E. 选型 → 落笔

选型是**人的工作**：`binding: required` 的器件必须查立创商城的参数、封装、库存、生命周期，把料号填进 netlist 的 `lcsc`。

```bash
python scripts/schem.py bind netlist.json readback.json -o netlist.bound.json   # 功能名 → 实际引脚号
python scripts/schem.py playbook netlist.json --js apply.js                     # 未选型则拒绝
python scripts/schem.py bom netlist.json -o bom.csv
```

落笔按 `references/eda-execution.md` §6 执行：打开图页 → 逐器件 create（第 8 个参数 `addIntoPcb` 必须 true）→ 读回引脚坐标 → 每脚拉带 net 的短线 → `save` → 等 8~10 秒 → `sch_Drc`。脚本只保证**逻辑连通**，走线走向要人工整理。

**落笔后必须做一致性比对**：`getNetlistFile` 导出的网表 vs `netlist.json`，逐网络比。这是唯一能证明"画出来的就是设计的"的手段。

---

# 第二部分：PCB Layout（网表 → 投板）

## A. 建板与叠层

先定 4 件事，缺一不可：板框尺寸、层数、铜厚、表面处理。

- 板框：`pcb.outline.set`（CLI `easyeda pcb outline-set`），传闭合多边形 `[[x,y],…]`，单位 mil、y 向上。圆角用 `pcb outline-round --radius` 或折线逼近。
- 层数选题：跑 `rules.py layers --pins <总管脚数> --area-mm2 <板面积>` 得到 Pin 密度建议层数（来源 TY-11~TY-17）。时钟 ≥5MHz 或上升沿 <5ns 直接上多层（TY-63）。
- 叠层：查 `references/stackup-and-impedance.md`，**必须对称**，信号层夹在平面层之间，相邻信号层走线正交。
- 铜厚：默认 1oz(35µm)。有 >2A 电流或散热需求时上 2oz。

## B. 出布局计划（不要跳过）

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

## C. 落笔

按 `references/eda-execution.md` 里的顺序和命令表执行：板框 → 从原理图导入 → 摆件（先锁锚点）→ 关键信号 → 电源 → 其余 → 过孔缝合 → 铺铜。

关键纪律：

- 每摆完一个功能块就 `pcb.components.list --include-bbox` 回读一次，确认位置真的落上去了。
- 每次只做一类操作，做完立刻验证。EDA 没有 undo。
- 走线用 `pcb.line.create`，一段一段来，`net` 必须写对网络名（从 `pcb.nets.list` 取，别拼）。**不要用自动布线**，理由见 `references/eda-execution.md` §1 的「禁用项」。
- **布线前先记基线，布完对账（按 id 不按段数）。** 动手前 `easyeda pcb line-list --json > lines.pre.json`（子命令以 `easyeda actions` 为准），跑 `snap.py`/`journal.py baseline` 把每个网络的 primitiveId 与几何存下来；之后每次 `line.create`/`line.delete` 都用 `journal.py record` 记一笔；布完 `journal.py verify --final lines.post.json` 对账。EDA 会在交点处把线切开（调 200 次可能得到 202 段），所以只能比 id 集合，比段数必误报。对不上且解释不通 = 有别的进程或上一轮动过画布，按红线停下来问。
- 铺铜 `net` 不能为空（空网 = 死铜）。
- 多步批量别写 shell 胶水，写成 playbook 先 `easyeda apply steps.json --dry-run`。

**走线阶段必读** `references/routing-aesthetics.md`。用户说"布得整齐点""要好看"，或你要判断某处"不整齐"该不该改时，去那里查判据——它把"美观"拆成了角度、对齐、间距一致性、线宽连续性、图元从属关系 5 个可测量，还给了一个明确的裁决顺序（电气 > 结构 > 制造 > 整齐）。**别凭感觉说"这样不好看"，要么给出判据编号，要么承认是个人偏好。** 其中对齐、阵列间距、正交朝向、45° 走线这 4 项已由 `check.py` 的 `AEST-*` 规则自动判（见 D 节）。

## D. 验证

```bash
# 0) 先拼快照：components-list 不返回走线，三份分开导出再合并
easyeda pcb components-list --include-bbox --include-pads --json > comps.json
easyeda pcb line-list --json > lines.json
easyeda pcb via-list  --json > vias.json
easyeda pcb outline-get --json > outline.json
python scripts/snap.py --components comps.json --lines lines.json \
    --vias vias.json --outline outline.json -o my_board.json

python scripts/check.py my_board.json      # 规范符合性（本 skill 的规则库）
easyeda pcb drc-check                      # EDA 原生 DRC：间距/短路/未布通
easyeda pcb check                          # DFM 级：悬空端/酸角/丝印翻转/孤铜
easyeda pcb report                         # 网络长度/差分偏斜

# 过程对账（布线阶段每步记账，收尾核 —— 专抓"改线时没删旧段"）
python scripts/journal.py baseline --lines lines.pre.json -o journal.json
python scripts/journal.py record --journal journal.json --net VDD --delete 101 --add 301,302
python scripts/journal.py verify --journal journal.json --final lines.json
python scripts/check.py my_board.json --journal journal.json   # 并入 RT-JRN-05/06
```

**没有 `traces` 的快照不能用来判走线。** `components-list` 的输出里没有走线，早期写法从它里面取 `traces` 拿到的一直是空列表，于是 3W/直角/悬空/线宽四项全部静默跳过。`snap.py` 把这件事变成硬错误：缺 `--lines` 直接 exit 2，不生成"看起来正常"的空快照。确实还没布线才加 `--allow-empty`，此时 `check.py` 记 `FAB-DATA` UNKNOWN，`--gate` 判 `INCOMPLETE`。

**冗余走线**（`RT-*`）。这一类 EDA 原生 `drc-check` / `check` 一条都查不出来：重复段不短路、残段不开路、自环不违反间距，电气上全是"合法"的，但都是画布上的多余铜：

| 规则号 | 查什么 | 阈值（`assets/constraints.json` → `trace_redundancy`） | 默认 |
|---|---|---|---|
| `RT-DUP-01` | 同层同网两段共线且投影重叠 | 重叠 ≥ 较短段的 80%，两段都 ≥20mil | FAIL |
| `RT-STUB-02` | 残段：从自由端走到第一个岔路口/焊盘的累计长度 | ≥ max(200mil, 10×线宽) | FAIL |
| `RT-LOOP-03` | 同网络闭合铜环（TY-6.5.6-8） | 周长 ≥50mil | WARN |
| `RT-DETOUR-04` | 生成树铜长 / 引脚曼哈顿最小生成树 | >1.8（高速网络 >1.3） | WARN |
| `RT-JRN-05` | 账本声明删掉的 primitiveId 最终还在（旧段没删就叠画）／声明新增的 id 找不到 | 无阈值，按 id 集合比 | FAIL |
| `RT-JRN-06` | 某网络的 id 集合变了，但没有对应的记账（增删未登记） | 切段按 `journal_cover_ratio`（0.9）豁免 | WARN |

判据只认几何、不认意图。**故意留着的必须登记**，否则照报：测试点引线、天线焊盘、蛇形等长、差分补偿写进 `trace_redundancy.allow`，形如 `{"net":"TP_*","rules":["RT-STUB-02"],"max_len_mil":400,"reason":"测试点引线按设计保留"}`。登记生效后会以 `RT-ALLOW` INFO 列出来——豁免不是静默，"登记一次永久静音"也不成立（超过 `max_len_mil` 照样报）。

数据不够时不编结论：整份快照没有焊盘坐标 → 残段判据不参与（缺数据由 `FAB-DATA` 报）；某个网络自己一个焊盘/过孔坐标都没有 → 该网络报 `RT-STUB-02` UNKNOWN。`examples/snapshot.redundancy.json` 是四条判据的活样例，`snapshot.redundancy.clean.json` 是同一块板清干净后（RT-* 全 0）的样子。

**过程对账**（`RT-JRN-*`）。上面四条只看铜，抓不到"旧段没删、新线只盖住一小截"——两段错开一个线宽就不重叠，铜却实实在在多出来了。这一类只能靠 id 账本：布线前记基线、每步记增删、收尾比集合。

```bash
python scripts/journal.py baseline --lines lines.pre.json -o journal.json
python scripts/journal.py record --journal journal.json --net VDD --delete 101 --add 301,302
python scripts/journal.py verify --journal journal.json --final lines.post.json
```

- `RT-JRN-05` FAIL：账上声明删的 id 还在画布上 → 叠画源头，先 `pcb.line.delete` 再重画。
- `RT-JRN-06` WARN：某网络的 id 变了但没记账。**切段自动豁免**：消失的 id 若仍被最终铜覆盖 ≥90%、或新增的 id 落在基线铜范围内，判原生切段只记 INFO。所以别拿段数去比，那条路必然误报。
- 只记基线、没有 `rounds` 就不给对账（exit 2），`check.py --journal` 会记 `RT-JRN-05` UNKNOWN。**没记账不等于没问题。**
- 活样例：`journal.rounds.json` + `eda-export.lines.after.json`（账平）；`eda-export.lines.overlap.json`（叠画 → FAIL）；`journal.rounds.forgot.json` + `after`（漏记账 → WARN）；同账本 + `eda-export.lines.split.json`（原生切段 → 只记 INFO 豁免）。

`check.py` 输出的每条违规都带**规则号**（如 `TY-6.5.6-12`、`AEST-ALIGN`、`EM-5.4.11`、`FAB-SHORT`、`RT-DUP-01`），可回 `references/` 定位原文。严重度分四档：

- `FAIL` — 违反硬约束（叠板、短路风险、工艺做不出来）→ 必须改；
- `WARN` — 违反推荐值（间距偏小、去耦偏远）→ 改，或说明为什么能接受；
- `UNKNOWN` — 这项**没查成**（缺 traces、缺高度、缺焊盘）→ 补数据重跑，不能算通过；
- `INFO` — 已知事实或已声明例外（待选型、GPIO 未分配、allow 登记）→ 记录，不算违规。

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

## E. 交付

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

## 做原理图时脑子里要有的数字

能算的一律跑 `schem.py calc`，这里只放最高频的锚点，完整表在 `references/schematic-rules.md` §10：

| 项 | 数值 | 依据 |
|---|---|---|
| 去耦电容 | 每个电源脚 100nF X7R，电压额定 ≥2× 工作电压 | 通用实践 |
| bulk 电容 | 每路电源 ≥10µF | 通用实践 |
| 复位 RC | 10k + 100nF（τ=1ms） | 通用实践 |
| 晶振负载电容 | C=2×(CL−Cstray)，Cstray 2层 3pF / 多层 5pF | 晶振手册 |
| I2C 上拉区间 | 3.3V：Rmin=967Ω；Rmax 100kHz→5.9k / 400kHz→1.8k（Cb=200pF） | UM10204 |
| USB-C Rd | CC1/CC2 各 5.1kΩ±5% 到 GND | USB Type-C R2.0 |
| RS485 终端 | 120Ω，仅总线两端 | TIA-485-A |
| RS485 偏置 | A 上拉 / B 下拉，Rb ≤ Rt×(Vcc/0.2−1)/2（5V→≤1.44k） | TIA-485-A |
| CAN 终端 | 120Ω 或 2×60Ω + 4.7nF 分裂终端 | ISO 11898-2 |
| LED 限流 | R=(Vcc−Vf)/I，Vf 红 1.9 / 绿黄 2.1 / 蓝白 3.0 V | 器件手册 |
| ADC 源阻抗 | ≤10kΩ（12 位 SAR），分压点并 100nF | 应用笔记 |
| TVS VRWM | ≥1.1×工作电压，按标准档位取下一个 | 通用实践 |
| 继电器驱动 | Ic ≥3×线圈电流，β 用最小值，线圈必并续流二极管 | 通用实践 |

## 与其它技能的关系

- 本 skill 负责**规范 + 计算 + 坐标 + 检查**（"画什么、画多宽、画在哪"）。
- **[easyeda-agent-open](https://github.com/yitengchun833-gif/easyeda-agent-open) / easyeda-agent 负责落笔执行**（Go daemon + `.eext` 扩展 + MCP + CLI）。本 skill 的 `eda-execution.md` 描述的就是这套 CLI，其中 7 个动作 id 已在其源码中逐个核实存在，**可直接对接，不用改代码**。装法、对接点、什么不该学它，见 `references/integration.md`。
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

# 快照（EDA 回读三源合并 → check.py 的输入）
python scripts/snap.py --components comps.json --lines lines.json --vias vias.json \
    --outline outline.json -o snapshot.json
python scripts/snap.py --components comps.json --width 2362 --height 1772   # 没有 outline 时给板框尺寸
python scripts/snap.py --components comps.json --allow-empty                # 确实未布线才加，会判 INCOMPLETE

# 检查
python scripts/check.py snapshot.json [--min-severity FAIL|WARN|UNKNOWN|INFO] [--json] [--strict]
python scripts/check.py snapshot.json --netlist nl.json              # 并入 ERC + 块级布局约束
python scripts/check.py snapshot.json --netlist nl.json --gate        # 单一判定：BLOCKED/INCOMPLETE/REVIEW/PASS
python scripts/check.py snapshot.json --journal journal.json          # 并入过程对账 RT-JRN-05/06
python scripts/check.py snapshot.json --require-known                 # 有 UNKNOWN 就返回 2

# 布线账本（过程对账，防叠画）
python scripts/journal.py baseline --lines lines.pre.json -o journal.json
python scripts/journal.py record --journal journal.json --net VDD --delete 101 --add 301,302
python scripts/journal.py verify --journal journal.json --final lines.post.json [--json]
python scripts/journal.py ids --lines lines.post.json --net VDD       # 查某网络现有哪些 id

# ---- 原理图（schem.py）----
python scripts/schem.py blocks                              # 15 个功能块清单
python scripts/schem.py chips [--for-block rs485]           # 5 颗芯片实例（型号/料号/引脚/约束）
python scripts/schem.py netlist spec.json --chip rs485=sp3485 -o nl.json   # 套用芯片实例
python scripts/schem.py spec --req 需求.txt -o spec.json    # 需求 → 设计规格（草稿，需人改）
python scripts/schem.py netlist spec.json -o netlist.json --report
python scripts/schem.py erc netlist.json [--strict] [--json] [--min-severity WARN]
python scripts/schem.py bom netlist.json -o bom.csv
python scripts/schem.py bind netlist.json readback.json -o netlist.bound.json
python scripts/schem.py playbook netlist.json --js apply.js [--allow-missing]

# 原理图计算
python scripts/schem.py calc led      --vcc 3.3 --vf 1.9 --i-ma 5
python scripts/schem.py calc crystal  --cl 12 --cstray 3
python scripts/schem.py calc i2c      --vcc 3.3 --mode fast --cb-pf 200
python scripts/schem.py calc divider  --vin 24 --vout 4.5 --r-bottom 10k
python scripts/schem.py calc ldo      --vin 24 --vout 3.3 --iout 0.3
python scripts/schem.py calc rs485-bias --vcc 5 --rt 120
python scripts/schem.py calc relay    --vdrive 3.3 --coil-ma 70 --beta 100
python scripts/schem.py calc buck     --vin 24 --vout 5 --iout 1 --fsw-khz 500
python scripts/schem.py calc tvs      --vop 24
python scripts/schem.py calc usb-cc
```

`check.py` 覆盖 21 类：出框、板边净空、courtyard 间距、BGA 禁布、安装孔禁布、去耦距离（按最近 IC）、晶振距离、朝向一致性、**有极性件朝向与正交**、**同封装 IC 的 1 脚朝向**、**器件高度与高矮混放**、**电感间距与正交（车规）**、**重心平衡**、**连接器插拔净空**、**MARK 数量与分布**、悬空线头、直角/锐角、3W 与异网短路、过孔孔厚比、**冗余走线（RT-\*）**、**布线过程对账（RT-JRN-\*，需 --journal）**。数据缺就降级为 `UNKNOWN`（不是 INFO），`--gate` 判 `INCOMPLETE`，**不会假装通过**。

带 `--netlist` 时另跑块级约束：`BLK-DECAP`（去耦贴 IC ≤2mm）、`BLK-ESD`（保护器件贴连接器 ≤5mm）能几何硬算；其余 `BLK-MANUAL` 汇总报 UNKNOWN —— **程序没报错不代表这些约束满足了**。

## 装在哪、先自检

```bash
# Codex / 本机 skill 目录
ls ~/.codex/skills/jlc-pcb-layout          # 或 ~/.workbuddy/skills/jlc-pcb-layout
cd ~/.codex/skills/jlc-pcb-layout
python scripts/rules.py width --current 3 --copper 1oz    # 应输出 1.5mm / 3.2A
python scripts/plan.py assets/plan.example.json -o p.json --report
python scripts/snap.py --components examples/eda-export.components.json --lines examples/eda-export.lines.json --vias examples/eda-export.vias.json --width 1000 --height 800 -o s.json   # 应报 器件 2 / 走线 3 / 过孔 1；去掉 --lines 应退出 2
python scripts/check.py examples/snapshot.demo.json --min-severity WARN
python scripts/check.py examples/snapshot.decap.ok.json  --netlist examples/netlist.decap.json   # BLK-DECAP 应通过
python scripts/check.py examples/snapshot.decap.bad.json --netlist examples/netlist.decap.json   # 应报 BLK-DECAP FAIL
python scripts/check.py examples/snapshot.redundancy.json        # 应报 RT-DUP-01 / RT-STUB-02 FAIL，RT-LOOP-03 / RT-DETOUR-04 WARN
python scripts/check.py examples/snapshot.redundancy.clean.json  # 同一块板清掉多余铜后，RT-* 应 0 条
python scripts/journal.py verify --journal examples/journal.rounds.forgot.json --final examples/eda-export.lines.after.json    # 应报 3 条 RT-JRN-06 WARN
python scripts/journal.py verify --journal examples/journal.rounds.json --final examples/eda-export.lines.overlap.json         # 应报 RT-JRN-05 FAIL（叠画）
python scripts/journal.py verify --journal examples/journal.rounds.forgot.json --final examples/eda-export.lines.split.json    # 切段应被豁免：0 FAIL 0 WARN，只有 INFO
python scripts/schem.py netlist examples/spec.example.json -o n.json --report   # 应报 76 器件 / 57 网络
python scripts/schem.py netlist examples/spec.example.json --chip rs485=sp3485 --chip power_ldo=ams1117_33 -o n2.json
python scripts/schem.py erc examples/netlist.example.json                            # 应报 FAIL 0 / WARN 0
python scripts/schem.py erc examples/netlist.bad.json                                # 反面样例，应报 FAIL 4
python scripts/schem.py chips                                                        # 应见 5 颗，其中 1 颗不可套用
```

十三条都要跑通。检查类的都是**成对自检**：正例必须干净、反例必须报错——只测前者等于没测。


## 红线

- 不擅自删器件、不擅自清空布线、不擅自改板框 —— 这三类先问。
- 不在 `.epro`/工程文件层面做二进制修改，一切通过 API/CLI。
- **不调用任何自动布线 / 自动布局功能。** 包括 `eda.pcb_Document.autoRouting()`、EDA 菜单里的"自动布线"、CLI 里任何 auto-route 类动作，也不要去找"文件交换式自动布线"之类的替代路线。走线只能 `pcb.line.create` 逐段写。理由：一次调用动整块板而 EDA 没有 undo；单次最长跑到 900 秒，超时留下一半布通的板；出问题无法逐段归因，`check.py` 报 3W/45° 时你指不出是哪一步错。用户明确要求"就用自动布线"时，先说明上述后果并要一句确认，再写进交付物的「未按规范验证项」。
- **画布上出现不是本流程创建的走线就停下。** 判定特征：段数远超计划、成片网络一次性连上、走线绕过你摆的坐标。这说明有人（或上一轮的你）跑过自动布线或自动布局，**不要在污染的画布上继续改**——先问用户是留还是清。
- **不在旧线上叠着画。** 改某个网络的走向或续一段之前，先 `pcb.line.delete` 掉被替换的旧段再画新的，并把那一段的 id 记进 `journal.py` 账本。EDA 不会自动合并同网络的重叠铜，也没有 undo 能退回。收尾 `RT-JRN-05` FAIL 就是"旧段还在"——那不是多一根线，是同一段铜画了两遍：蚀刻留残渣、线宽虚胖导致阻抗突变，高速线上直接算错。**判据抓不全（错开一个线宽就不重叠），账本抓得全。**
- 不虚构 DRC 结果。没跑就是没跑。
- **不编 LCSC 料号**。没有料号就停在选型，`playbook` 会拒绝生成脚本——不要把 `--allow-missing` 当绕过手段。
- 不绕过 ERC 的 FAIL 直接落笔。真要先看效果，就把 FAIL 逐条写进交付物并标注"未修复"。
- 不擅自改客户的网表连接（删网络、并地、改上拉）。改之前说清改什么、为什么。
- 不把"脚本跑完了"当"电路对了"。ERC 查不出选型、时序、环路稳定性、EMC。
- **不把 UNKNOWN 报成 PASS**。缺数据就是没查，明确写进交付物；`FAIL 0` 单独出现不是合格证明。
- **不猜引脚号**。芯片实例里没有的脚就是没有，走 `bind` 从 EDA 回读，或去对 datasheet，不许按顺序推。

## 文件索引

```
SKILL.md
references/
  schematic-workflow.md      需求 → 原理图 工作流（澄清清单/五步/三道门禁/交付物/常见翻车）
  circuit-blocks.md          15 个功能块参考电路（组成/参数怎么算/常见错误/对应规则号）
  chip-variants.md           芯片实例层：收录标准、5 颗的真实状态、怎么加新芯片、7 种布局约束的可检查性
  integration.md             与执行层（easyeda-agent-open）对接：已验证事实、三个对接点、不吸收的四项
  schematic-rules.md         原理图规范 + 23 条 ERC 判据表 + 数值表
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
  snap.py                    EDA 回读三源合并成 check.py 的快照（缺走线数据就拒绝生成）
  journal.py                 布线账本：按 primitiveId 记增删，收尾对账（抓叠画/未记账）
  check.py                   规范符合性检查器（布局 16 类 + 走线 8 类含 4 条 RT-* + 过程对账 2 类 + 整洁度 4 类 + 块级 2 类，四态含 UNKNOWN）
  schem.py                   原理图生成与校验（spec/netlist/erc/bom/bind/playbook + 11 个计算器）
assets/
  constraints.json           全部数值约束（机器可读，唯一真源）
  circuit-blocks.json        15 个功能块的可执行定义（网表生成器读这个）
  chip-variants.json         5 颗芯片实例：真实脚号/料号/块级布局约束/出处
  plan.example.json          计划文件示例（60×45 四层板）
examples/
  walkthrough.md             一块板从需求到投板的完整走查（含真实输出）
  req.example.txt            客户需求原文样例（工业采集板）
  spec.example.json          成品设计规格（人改过的版本，对照 spec 草稿看差在哪）
  netlist.example.json       76 器件 / 57 网络的成品网表（ERC: FAIL 0 / WARN 0）
  netlist.bad.json           反面网表（4 处 FAIL / 15 处 WARN，验证 ERC 真的会报）
  netlist.decap.json         块级约束夹具（配合下面两个快照验证 BLK-DECAP）
  snapshot.decap.ok.json     正例：去耦 1.8mm，应通过
  snapshot.decap.bad.json    反例：去耦 8.1mm，必须报 FAIL
  snapshot.demo.json         有典型毛病的板子快照（喂 check.py 看报什么）
  snapshot.demo.fixed.json   回修后的同一块板（FAIL 0 / WARN 6 / UNKNOWN 2，UNKNOWN 明确表示"没查"）
  snapshot.aesthetics.json   整洁度反面样例（对齐/间距/朝向/角度各一条，另含不该报的交错双排）
  snapshot.placement.json    元器件摆放专项反面样例（FAIL 0 / WARN 10，PL-* 八类各中一条）
  snapshot.redundancy.json   冗余走线反面样例（重复段/残段/自环/绕行各一条，四个 RT-* 全命中）
  snapshot.redundancy.clean.json  同一块板清掉多余铜后（RT-* 全 0，验证不会滥报）
  eda-export.components.json 假的 components-list 输出（验证 snap.py 的器件/焊盘字段映射）
  eda-export.lines.json      假的 line-list 输出（3 段走线 / 2 个网络，带 primitiveId）
  eda-export.lines.after.json     改线后的正确结果（101 已删、301→303 绕行；账本对上）
  eda-export.lines.overlap.json   反面：声明删了 101 但它还在 —— 叠画（RT-JRN-05 FAIL）
  eda-export.lines.split.json     同一根线在交点被原生切成 401/402（验证切段被豁免、不按段数比）
  eda-export.vias.json       假的 via-list 输出（1 个过孔）
  journal.baseline.json      布线前基线（per-net primitiveId + 几何）
  journal.rounds.json        记账正确的账本（删 101 / 加 301-303）
  journal.rounds.forgot.json 反面：只记了删、忘了记加（RT-JRN-06 WARN）
```

## 命令写法约定

动作名一律用**类型化 id**（`pcb.outline.set`、`pcb.line.create`）。CLI 里对应 kebab-case 子命令（`easyeda pcb outline-set`、`easyeda pcb line-create`），个别版本用空格分词（`easyeda pcb drc check`）。**报参数前先 `easyeda actions` 或 `easyeda pcb --help` 对一遍**，以它的输出为准——本文只保证语义正确。
