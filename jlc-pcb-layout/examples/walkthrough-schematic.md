# 走查：从一句需求到可投板的原理图

真实跑一遍 `examples/req.example.txt` 的流程，输出都是实际跑出来的（不是示意）。

---

## 0. 客户原话

```
现场 24V 供电，板内需要 5V 和 3.3V 两路稳压，主控用 STM32 类 MCU。
- 1 路 RS485 接上位机（Modbus RTU）
- 1 路 CAN 接车载设备
- 3 路继电器输出，驱动 24V 电磁阀
- 2 个按键做本地操作
- 4 个指示灯显示状态
- 1 个 I2C 接口接 OLED 显示屏
- USB Type-C 口用于调试供电
- 输入要防浪涌和防反接
板子要塞进 100×80mm 的塑料壳，工作温度 -20~60℃。
```

---

## 1. `spec`：机器出草稿，并主动承认它不确定

```bash
python scripts/schem.py spec --req examples/req.example.txt -o spec.json
```

识别出的块：

```json
[{"type":"usb_typec_ufp","count":1},
 {"type":"rs485","count":1},
 {"type":"can","count":1},
 {"type":"relay_drive","count":3},
 {"type":"mosfet_load","count":1},     ← 多识别的
 {"type":"key","count":2},
 {"type":"led_ind","count":4},
 {"type":"i2c_port","count":1}]
```

`review` 字段直接点出问题：

```
- 没有识别到稳压模块：若板上没有电源树，确认是否整板由外部稳压供电
- 没有识别到输入保护：工业现场建议至少加保险 + TVS（power_protect）
- 同时命中 relay_drive 与 mosfet_load：客户若要『继电器驱动电磁阀』，
  MOSFET 块是多余的，删掉 mosfet_load 再生成网表
```

`open_questions` 里第一句就是：

```
这些电源没有对应的稳压模块，电从哪来？3V3, 5V, 12V
（补一个 power_ldo / power_buck，或说明由外部直接供给）
```

**这一步的价值不在于"识别对了"，而在于它把自己不确定的地方写出来了。** 需求里说"5V 和 3.3V 两路稳压"，关键词只匹配到 `power_ldo`（因为 `3.3v`/`5v`/`稳压` 都是 LDO 的关键词），机器不知道 24V→5V 该用 buck——这个判断必须人做。

---

## 2. 人工改 spec：三件事

对照 `examples/spec.example.json`（成品），改了这些：

| 改动 | 原因 |
|---|---|
| 删掉 `mosfet_load` | "继电器驱动电磁阀"是继电器的活，不需要额外的 MOS 块 |
| 加 `power_protect`（vop=24） | 客户明确要求防浪涌防反接 |
| 加 `power_buck`（24V→5V @1A） | 24V 压差太大，5V 这级必须开关电源 |
| 改 `power_ldo` 为 5V→3.3V @0.6A | 两级降压，LDO 只承担小压差 |
| 补所有 `rails[].i_a` | 电流决定线宽与铜厚，机器猜不出来 |
| 指定 UART 编号 | RS485 用 UART1、CAN 用 CAN1，这要看主控手册 |

---

## 3. `netlist`：76 个器件、57 个网络

```bash
python scripts/schem.py netlist examples/spec.example.json -o netlist.json --report
```

```
=== 网表摘要 ===
器件 76（其中待绑定 16/16），网络 57，功能块 11

电源网络：
  3V3              3.3V  14 个引脚
  5V                 5V  11 个引脚
  GND                0V  49 个引脚
  VBUS               5V  3 个引脚
  VIN               24V  8 个引脚
  VIN_IN            24V  2 个引脚
```

读这份报告要看的是：**每路电源的引脚数是不是合理**。`VIN_IN` 只有 2 个引脚（连接器 + 保险）是对的；`12V` 如果出现且只有 1 个引脚，说明有个块没人给它供电。

---

## 4. `erc`：干净，但 INFO 不是"通过"

```bash
python scripts/schem.py erc examples/netlist.example.json
```

```
检查结果：FAIL 0 / WARN 0 / INFO 12
```

12 条 INFO 全是两类：

| 规则 | 条数 | 含义 |
|---|---|---|
| `SCH-BINDING` | 1（合并列出 16 个器件） | 关键器件还没选型 |
| `SCH-ERC-SINGLE` | 11 | `RLY1_CTRL`/`UART1_TX`/`LED3_CTRL`… 另一端在 MCU 上，但主控还没绑定型号 |

这 11 条**不是可以忽略的**——它们准确描述了当前状态：电路骨架对了，但"哪个 GPIO 接哪个"还没定。选定主控后重新生成网表，这些会自动变成 2 引脚网络。

同一条命令跑反面样例：

```bash
python scripts/schem.py erc examples/netlist.bad.json
```

```
检查结果：FAIL 4 / WARN 15 / INFO 2
[FAIL] ✗ SCH-LED-RES       D1    LED 没有串联限流电阻（只共用地网络不算，必须串在回路里）
[FAIL] ✗ SCH-RELAY-FLYBACK  K1    线圈两端没有反并联续流二极管，关断瞬间会打穿驱动管
[FAIL] ✗ SCH-TVX-VRWM      D2@VIN TVS VRWM=12.0V 低于工作电压 24.0V 的 1.1 倍
[FAIL] ✗ SCH-USB-CC        J1.CC2 CC2 没有 5.1k 下拉，source 不会供电
```

四条都是"板子做出来会坏"的级别。这就是为什么要成对跑：**只测好样例的检查器等于没测**。

---

## 5. 计算器：把"大概"换成数字

```bash
$ python scripts/schem.py calc i2c --vcc 3.3 --mode fast --cb-pf 200
I2C 上拉：Vcc=3.3V fast(400000Hz) Cb=200.0pF
结论：OK
允许区间：967Ω ~ 1771Ω
推荐：1000Ω（候选 [1000, 1500]）
说明：VOL=0.4V / IOL=3mA 决定下限；tr≤300ns 决定上限；
     候选区为空说明总线电容太大或速率太高：降速、缩短总线、或换主动上拉
```

注意这个结果和"抄 4.7k"的差距：400kHz、200pF 时 4.7k 已经超出了上升时间允许的上限（1771Ω）。这就是 I2C 提到 400kHz 就时好时坏的根本原因。

```bash
$ python scripts/schem.py calc crystal --cl 12 --cstray 3
C1=C2=18.0pF

$ python scripts/schem.py calc ldo --vin 24 --vout 3.3 --iout 0.3
结论：TOO_HOT
功耗：6.21W；估算结温 661.0℃（上限 125℃）
效率：13.7%
说明：结温 661℃ 已远超上限，线性模型外推失真——这个组合物理上就不成立，直接改 buck
```

最后这条是 24V 系统最常见的翻车点：**用 LDO 从 24V 直接降到 3.3V**，6.2W 全变成热，任何封装都救不回来。

---

## 6. `bom`：采购能直接用的表

```bash
python scripts/schem.py bom examples/netlist.example.json
```

```
种类    参数              数量   位号                      LCSC      状态
C     100nF           6    C7 C8 C11 C13 C14 C15             通用件
C     10uF            3    C1 C2 C5                          通用件
C     18pF            2    C9 C10                            通用件
D     1N4148W         3    D5 D6 D7                          通用件
IC    BUCK-5V         1    U1                                待绑定
IC    CAN-5V          1    U5                                待绑定
J     DC-005 5.5x2.1  1    J1                                通用件
```

`待绑定` 就是必须人来选型的（`binding: required`）。同型号自动归并、数量自动统计。

---

## 7. 落笔：脚本拒绝执行没选型的网表

```bash
python scripts/schem.py playbook examples/netlist.example.json --js apply.js
```

```
拒绝生成落笔脚本：以下器件没有 lcsc 料号 ——
  J1, F1, D1, C1, U1, L1, C2, C3, R1, R2, ...
落笔前必须选定型号。编料号会直接造成批量买错料。
```

这是门禁，不是 bug。选型之后填进 netlist：

```json
{"ref": "R1", "kind": "R", "value": "1k", "lcsc": "C21190", "pins": {"1": "3V3", "2": "MID"}}
```

再生成就能得到可运行的 JS（`node --check` 验证过语法），执行路径见 `references/eda-execution.md` §6：

```
打开图页 → create(第8参数 addIntoPcb=true) → modify 位号 → 读回引脚坐标
→ 每脚拉 20 单位短线（带 net 名）→ 电源放 netflag → save → 等 9s → sch_Drc
```

**落笔不等于验证完**。必须再导出 EDA 网表与 `netlist.json` 逐网络比对——这是唯一能证明"画出来的就是设计的"的手段。

---

## 8. 交接给 PCB

原理图定稿后，PCB 阶段直接吃这几样：

| PCB 需要什么 | 从哪来 |
|---|---|
| 器件清单与分区 | `netlist.json` 的 `block` 字段 + BOM |
| 电源电流（算线宽） | `spec.rails[].i_a` |
| 电压等级（算间距） | `spec.rails[].v` |
| 功能块划分（布局分区） | `spec.blocks` → 转成 `plan.py` 的 `blocks` |

最后一条最容易漏：`plan.py` 的计划文件里 `blocks` 要和 `spec.blocks` 一一对应，否则布局阶段会"少摆一个模块"，而这个问题要到装配时才发现。

---

## 9. 这条链上什么是机器做的、什么是人做的

| 环节 | 机器 | 人 |
|---|---|---|
| 需求识别 | 关键词 + 数量提取 | 判断哪些是误识别 |
| 电源树 | 按块的默认值拼 | **定 buck/LDO 分工与电流** |
| 网表 | 分配位号、连网络、补外围 | 决定 GPIO 分配、UART 编号 |
| ERC | 23 条结构规则 | 判断 WARN/INFO 是否可接受 |
| 选型 | 列出待绑定清单 | **查手册、查库存、填料号** |
| 落笔 | 放器件、连线、存盘 | 整理走线、核对网络 |
| 验证 | ERC / DRC / 网表比对 | **判断电路是否真的对** |

机器负责"不漏、不算错、可复现"，人负责"选什么、为什么、能不能接受"。这条界线画清楚，AI 才有用；画不清，就是"看起来很专业地画错"。
