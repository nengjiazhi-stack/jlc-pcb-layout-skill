# 走查：60 × 45 mm 四层控制板，从需求到可投产

这份走查用仓库自带的示例文件，把 skill 的每一步都跑一遍，**所有输出都是真跑出来的**，可以照着复现。

```bash
cd <skill 目录>
PY=python3        # Windows: C:\Users\<你>\.workbuddy\binaries\python\envs\default\Scripts\python.exe
```

---

## 0. 需求与假设

用户原话："帮我画一块 60×45 的四层控制板，USB 供电，STM32 + 一个 LDO + 排针 IO。"

信息不全，按最保守可制造假设，并**在交付物里显式列出**：

| 项 | 取值 | 依据 |
|---|---|---|
| 板框 | 60 × 45 mm，R5 圆角 | 用户给定 |
| 层数 | 4 层 | 见 §1 计算 |
| 铜厚 | 1 oz (35 µm) | 电流 <2A，无散热需求 |
| 表面处理 | 沉金 ENIG | 细间距 QFN + 免清洗 |
| 板厚 | 1.6 mm | 常规 |
| 输入 | USB-C 5V / 1A | 假设 |
| 安装孔 | 4 × M3，孔心距板边 6 mm | 假设，待结构确认 |

> 这 3 条"假设"必须在交付时说出口。skill 的规矩是：**不卡在提问上，但也不许把假设当事实交付。**

---

## 1. 定层数与叠层

```bash
$PY scripts/rules.py layers --pins 96 --area-mm2 2700 --clock-mhz 24
```

```
pin_density_in2_per_pin: 0.61
table_row: {'density_min': 0.6, 'signal_layers': 2, 'total_layers': 4}
recommended_total_layers: 4
recommended_signal_layers: 2
reasons: ['Pin 密度 = 4.19 in² / (96/14) = 0.61', '时钟 24.0MHz ≥ 5MHz → 至少 4 层（TY-63）']
出处：TY-11~TY-17 / TY-63
```

两条独立理由都指向 4 层：Pin 密度 0.61 落在 0.6–1.0 档，且 24 MHz 时钟触发"五—五规则"。

叠层（`references/stackup-and-impedance.md` §4 惯用 4 层，**对称**）：

| 层 | id | 用途 |
|---|---|---|
| L1 | 1 | 信号 + GND 铺铜（器件面） |
| L2 | 15 | **完整地平面**，不走线 |
| L3 | 16 | 电源平面（VDD 3V3），分割宽度按电位差取 |
| L4 | 2 | 信号 + GND 铺铜 |

---

## 2. 定约束（全部来自计算，不靠记忆）

```bash
$PY scripts/rules.py width --current 1 --copper 1oz
$PY scripts/rules.py clearance --voltage 5 --side secondary
$PY scripts/rules.py via --thickness 1.6
$PY scripts/rules.py spacing --trace 0.25
$PY scripts/rules.py impedance --type diff-microstrip --w 0.25 --s 0.15 --h 0.20 --target 90
```

| 约束 | 结论 | 出处 |
|---|---|---|
| VDD/VIN 主干（1 A） | **0.4 mm**（该档载流 1.1 A；IPC 算 0.30 mm，取大） | TY-18 |
| USB 差分 | **0.25 mm 线宽 / 0.15 mm 间距 / h=0.20 mm → 90.8 Ω**（偏差 0.9%） | TY-6.3.1.1 |
| 普通信号 | 0.20 mm | TY-43 |
| 最小线宽/间距 | 0.15 / 0.20 mm（加工地板，0.12 mm 非必要不用） | BELL-17 |
| 过孔 | **12 / 25 mil**（板厚 1.6 mm → 孔厚比 5.25，合规） | TY-6.3.1.2.5.1 / EM-5.6.5 |
| 电气间距（5 V 二次侧） | 空气 0.7 mm，爬电 1.2 mm（表里最低档，偏保守） | TY-28 |
| 线-线/线-焊盘间距（0.25 mm 档） | 0.30 mm | BELL-16 |

阻抗这一步的注意点：`w=0.15` 算出来 114 Ω，差 26%；**先把 h 定死再扫 w/s**，才是正确顺序（§6.3 敏感度排序）。最终 0.25/0.15 落在 90.8 Ω。

---

## 3. 出布局计划

把上面这些数字写成规格文件（示例已给：`assets/plan.example.json`），然后：

```bash
$PY scripts/plan.py assets/plan.example.json -o plan.json --report
```

```
power        edge=left   parts=5   rect=[480.0, 580.0, 820.0, 925.0]
mcu          edge=center parts=12  rect=[1180.0, 880.0, 1670.0, 1390.0]
io           edge=right  parts=4   rect=[1880.0, 580.0, 2270.0, 1085.0]

器件数 26，占板面积 18.3%，溢出块 无
```

计划器做的三件事（`references/layout-plan.md` 有 JSON 全字段）：

1. **锚点锁死**：J1（USB-C）、H1–H4（M3 安装孔）坐标由结构给定，直接落位并当障碍物；
2. **块级分区**：power 贴左（进线端）、mcu 居中、io 贴右（排针边），块间留 60 mil 走廊；
3. **块内货架排布**：按"高→低、大→小"装箱，25 mil 栅格对齐，坐标单位 mil、y 向上。

摆完先读一遍，三个具体判断：

- J1 在左、LDO（U2）也在左 → 输入回路短，✓
- Y1（晶振）距 U1 体中心 200 mil ≤ 300 mil，✓
- C1/C2/C3 在 LDO 一侧，是**输入/输出电容**而不是 MCU 去耦 —— MCU 的去耦要等 §5 检查里再定位（这正是 `check.py` 会抓的东西）。

不合格就改 `blocks[].edge` / `priority` 重跑，**不要在画布上手工纠偏**——手工改完计划文件就和板子脱钩了。

---

## 4. 落笔（两条通道，先试 A）

### 通道 A：`easyeda` CLI

```bash
easyeda health                                   # 确认 EDA 窗口连着
easyeda actions | head -50                       # 拿权威动作表与参数名
easyeda pcb layers-list                          # 真实层 id + copperLayerCount
```

板框（对角两点画矩形，y 向上，单位 mil）：

```bash
easyeda pcb outline-set --points '[[0,0],[2362,0],[2362,1772],[0,1772]]'
```

从原理图导入器件（**器件上板的唯一正路**）：

```bash
easyeda pcb import-changes
```

摆件：计划已导出成 playbook，先空跑再执行：

```bash
$PY scripts/plan.py assets/plan.example.json -o plan.json --emit-steps steps.json --project my-board
easyeda apply steps.json --dry-run
easyeda apply steps.json
```

`steps.json` 里每步长这样（共 21 步，锚点不重复移动）：

```json
{"action": "pcb.component.modify",
 "payload": {"designator": "J2", "x": 1950.0, "y": 800.0, "rotation": 90, "layer": 1}}
```

> ⚠️ `pcb.component.modify` 的键名以 `easyeda actions` 输出为准。若这一版要求 `primitiveId` 而不是 `designator`，在 playbook 前面插一步
> `{"action": "pcb.components.list", "capture": {"U1": "$.components[?(@.designator=='U1')].primitiveId"}}`，后面的步骤用 `${U1}` 引用。
> **id 跨会话会变**，所以永远 capture，不要抄死。

### 通道 B：`eda.pcb_*` JS（自建扩展 / bridge）

```js
// 板框必须用 Polyline，Line 不支持 BOARD_OUTLINE
const poly = eda.pcb_MathPolygon.createPolygon([[0,0],[2362,0],[2362,1772],[0,1772],[0,0]]);
await eda.pcb_PrimitivePolyline.create('', 11, poly, 10, false);

// 走线：net, layer, x1,y1,x2,y2, lineWidth
await eda.pcb_PrimitiveLine.create('VDD', 1, 700, 810, 700, 775, 20, false);

// 过孔：net, x, y, holeDiameter, diameter
await eda.pcb_PrimitiveVia.create('GND', 605, 875, 12, 25);
```

落笔顺序（**顺序错了必返工**）：板框 → 导入 → 锁锚点 → 摆块（每块回读）→ 关键信号 → 电源/地 → 其余信号 → 铺铜 → 丝印 → 验证 → 保存。

---

## 5. 验证：先看检查器抓到了什么

板子布到一半（示例文件 `examples/snapshot.demo.json`，故意留了典型毛病）：

```bash
$PY scripts/check.py examples/snapshot.demo.json --min-severity WARN
```

```
检查结果：FAIL 1 / WARN 14 / INFO 3

[FAIL] ✗ FAB-SHORT      VDD × GND
        同层（layer 1）走线相交 → 短路
        → 改层或重新布线；交叉必须换层并打过孔
[WARN] ! AEST-ANGLE     net VDD
        1 段走线角度偏离 45° 栅格（最大偏差 18°，位于 660,825）
[WARN] ! PL-MAINT-CONN  J1 周边
        U2 距连接器仅 88mil < 118mil 操作空间（插拔净空 ≥3mm）
[WARN] ! TY-6.2.11      C8 → U1（最近）
        去耦电容距最近电源脚 407mil > 250mil（4 层以上阈值）
[WARN] ! TY-6.3.1.2.5.1 via@575,875
        孔径 10mil < 1.6mm 板厚要求的最小 12mil
[WARN] ! TY-6.5.6-12    net VDD @575,875
        转折角 90°（≤90° 直角/锐角，酸角陷阱）
[WARN] ! TY-6.5.6-12    net VDD @1230,1300
        转折角 90°（≤90° 直角/锐角，酸角陷阱）
[WARN] ! TY-6.5.6-18    VDD ∥ XTAL1
        平行段中心距 15mil < 3W（36mil，重叠长度 115mil）
[WARN] ! TY-6.5.6-18    USB_DP ∥ USB_DM
        平行段中心距 25mil < 3W（30mil，重叠长度 185mil）
[WARN] ! TY-6.5.6-5     net VDD      2 个线头悬空（首端 900,700）
[WARN] ! TY-6.5.6-5     net GND      2 个线头悬空（首端 850,750）
[WARN] ! TY-6.5.6-5     net USB_DP   1 个线头悬空（首端 1290,900）
...
```

每条都带**规则号**，可以回 `references/routing-rules.md` / `layout-rules.md` 找到原文和"为什么"。重点不是数字，而是它抓到了三类人眼容易漏的：

- **VDD 与 GND 同层相交**——布线时视觉上只是"擦过去"，实际是短路；
- **C8 距 U1 电源脚 407 mil**——300 mil 以外去耦基本失效，而布局图上它看起来"就在旁边"；
- **VDD ∥ XTAL1 只隔 15 mil**——电源噪声直接耦合进晶振，W 数对的表（3×12=36 mil）。

> 注意 `USB_DP ∥ USB_DM 25 mil < 30 mil` 这条是**有意不修**的：差分对要的是**等间距**，3W 规则针对的是不同网络。这种"规则冲突"必须由人裁决，检查器只负责把矛盾摆出来。

---

## 6. 回修（改完必须复跑）

| 问题 | 动作 | 结果 |
|---|---|---|
| VDD × GND 短路 | 让 VDD 在 VDD 自己的层（L1→L3）；删掉测试用的悬空铜 | FAIL → 0 |
| 两处 90° 直角 | 插入 45° 斜段（`(605,875)`、`(1260,1300)` 各加一段） | 直角 WARN → 0 |
| 过孔 10 mil | 改 12/25（板厚 1.6 mm 的下限） | TY-6.3.1.2.5.1 → 0 |
| C8 距 U1 407 mil | C8 从 `(1575,1300)` 移到 `(1345,1300)` → 237 mil | TY-6.2.11 → 0 |
| USB 差分间距 25 mil | DM 从 x=1315 挪到 1325（间距 35 mil） | 3W WARN 消（等间距仍保持） |
| VDD ∥ XTAL1 15 mil | **没修**——XTAL1 要短且要包地，得重排晶振区 | 保留 WARN，交人决定 |

复跑：

```bash
$PY scripts/check.py examples/snapshot.demo.fixed.json --min-severity WARN
```

```
检查结果：FAIL 0 / WARN 6 / INFO 3

[WARN] ! PL-MAINT-CONN  J1 周边       U2 距连接器仅 88mil < 118mil 操作空间
[WARN] ! TY-6.5.6-18    VDD ∥ XTAL1   平行段中心距 15mil < 3W（36mil，重叠长度 115mil）
[WARN] ! TY-6.5.6-5     net USB_DP    1 个线头悬空（首端 1290,900）
[WARN] ! TY-6.5.6-5     net USB_DM    1 个线头悬空（首端 1325,900）
[WARN] ! TY-6.5.6-5     net XTAL2     1 个线头悬空（首端 1290,1120）
[WARN] ! TY-6.5.6-5     net RST       1 个线头悬空（首端 1100,1000）
```

**FAIL 归零不等于"通过"**：4 条悬空线头是还没布完（继续布），1 条是等人的决策，还有 1 条 `PL-MAINT-CONN` 是**摆放类**问题——U2 离 USB-C 插座只有 88mil，插拔时插头会撞到它，这条要挪器件才能消，走线修不掉。这三类必须在交付里分开写，不能糊成"已检查通过"。

注意回修后 `AEST-ANGLE` 那条自己消失了——被改掉的 90° 直角换成了 45° 斜段，正好落在栅格上。这也是"电气修好了，图面自然就齐了"的常见情形，不必当成两件事分别处理。

---

## 5.1 图面整洁度：用户说"不够整齐"时怎么答

"整齐"不能凭感觉说。`examples/snapshot.aesthetics.json` 是专门做的一版反面样例，4 类毛病各埋一条，同时**故意放了一条不该报的**：

```bash
$PY scripts/check.py examples/snapshot.aesthetics.json --min-severity WARN
```

```
检查结果：FAIL 0 / WARN 7 / INFO 5

[WARN] ! AEST-ALIGN     C 组(0402) 第行
        5 颗本应共线，C5 偏离基准线 22mil（基准 500mil，容差 12mil）
        → 把中心对齐到 500mil；基准取组内众数，别逐颗手调
[WARN] ! AEST-ANGLE     net SIG1
        1 段走线角度偏离 45° 栅格（最大偏差 6°，位于 250,320）
[WARN] ! AEST-PITCH     C 组(0402) 第行
        相邻中心距离散：80~200mil，均值 140mil，变异系数 43% > 25%
[WARN] ! AEST-ROT       R1
        旋转 37° 不是 90° 整数倍
```

三条判定口径值得记住，否则会把检查器当噪音：

1. **对齐**只在"多数已经齐了、少数掉队"时才报（组内 ≥60% 在 ±12mil 内）。C6~C8 与 C9~C11 是 y=900/960 的**交错双排**，有意为之，检查器对它保持沉默——这是设计上的验证点，不是漏报。
2. **间距**用变异系数，不用绝对差：同为 20mil 偏差，在 100mil 间距组里是 20%，在 400mil 组里只有 5%，后者更整齐。
3. **角度**容差 4°：EDA 导出坐标常因焊盘中心取整出现亚度偏差（如 110mil 长走线偏 5mil ≈ 2.6°），那不算"任意角度走线"。真正要抓的是 30°、60° 这种没落栅格的段。

丝印字高、丝印压焊盘、死铜不判定——快照里没有丝印图元与铺铜对象，所以输出 `AEST-DATA` 的 INFO 明说"未判定"。**把它当"通过"会漏掉一块板的观感问题**，交付时要写进未解决项。

最后跑 EDA 原生检查（管电气级：间距、未布通）：

```bash
easyeda pcb drc-check        # 规则间距/短路/断线
easyeda pcb check            # DFM 级：悬空端、酸角、非 45°、丝印翻转、孤铜、死铜
easyeda pcb layout-lint      # 摆放质量打分 + 可布通性预测（ratsnest 交叉数）
easyeda pcb report           # 每网络长度、差分 P/N skew、等长组 spread
```

---

## 7. 交付清单

不要把"画完了"当交付。四件东西：

1. **计划文件** `plan.json` —— 每颗器件的 x/y/rot/layer，可追溯；
2. **检查报告** —— `check.py` 输出（含未解决项）+ `pcb drc` 结果；
3. **改动清单** —— 哪些器件动了、哪些网络重布了、哪些规则有意违反及理由；
4. **假设与待确认** —— 板材、阻抗控制层、安装孔坐标、3W 与差分等距的取舍。

---

## 8. 这次走查里真正的三个坑

1. **先算 h 再扫 w**。阻抗线宽不能凭"0.2 mm 差不多"，同样是 0.2 mm，h=0.2 → 114 Ω，h=0.18 → 90 Ω。叠层一改全部重算。
2. **去耦要看"最近的那颗 IC"**。C8 在 U1 与 U2 之间时，如果拿它和 U2 比距离会得到 1136 mil 的假警报；`check.py` 已按"最近 IC"判定，手算时也要这么算。
3. **空跑 playbook 再执行**。21 步一次打下去，错一步就得整板重排——EDA 没有 undo，`--dry-run` 是唯一免费的保险。
