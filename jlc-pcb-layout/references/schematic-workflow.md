# 需求 → 原理图 工作流（Workflow）

这份文件回答三件事：**客户的需求怎么变成可核查的电路**、**哪一步不许跳**、**什么时候必须停下来问人**。

配套：
- 电路怎么接 → `references/circuit-blocks.md`
- 判据与数值 → `references/schematic-rules.md`
- 怎么落笔到嘉立创EDA → `references/eda-execution.md` §6
- 计算与检查 → `scripts/schem.py`

---

## 0. 核心立场

**原理图不是"画个大概"，它是整块板唯一的逻辑真源。** PCB 上的每一根网络都来自这里；原理图错了，后面布线再完美也是废板。

所以本工作流的产物不是"一张图"，而是三层可核查的数据：

```
需求文本  →  spec.json（块清单+参数+假设+待确认）  →  netlist.json（器件+网络+引脚）
                                                        ↓
                                           ERC 报告 + BOM + 落笔脚本 + 回读比对
```

模型在这里的角色是**执行这套流水线并如实报告缺口**，不是"凭印象画个 STM32 最小系统"。

---

## 1. 需求澄清：先问，再画

客户给的原话通常是「做个 4 路继电器控制板，接 485，24V 供电」。这句话缺了至少 8 个关键参数。**不要用假设硬画**——按下面清单问，问不到就写进 `open_questions` 并在交付物上标红。

### 必答（答不出就不该开始）

| # | 问题 | 为什么必须问 |
|---|---|---|
| 1 | 主控型号 / 有无指定平台？ | 引脚数、外设、电平、工具链全由它决定 |
| 2 | 输入电源的电压范围与最大电流 | 决定保护等级、线宽、buck/LDO 选型 |
| 3 | 每路输出的负载类型与电流 | 继电器 vs MOS vs 可控硅，方案完全不同 |
| 4 | 对外接口的电气标准 | RS485/RS232/4~20mA/0~10V，保护与隔离方案不同 |
| 5 | 是否需要隔离（以及隔离电压） | 隔离电源 + 数字隔离器，成本翻倍 |
| 6 | 工作温度范围 | 商业级 0~70℃ / 工业级 −40~85℃ / 车规 |
| 7 | 结构与尺寸约束 | 连接器位置、板厚、安装孔，反向约束选型 |
| 8 | 认证要求（CE/UL/3C/本安） | 直接影响安规间距与器件认证状态 |

### 可假设（但要写明假设）

| 项 | 默认假设 | 假设错了的代价 |
|---|---|---|
| 时钟 | 外部 8MHz 晶振 | 改内部 RC，去掉晶振与两颗电容 |
| 调试口 | SWD 4P | 换 JTAG 要加针 |
| 复位 | 10k + 100nF | 电源慢时要换 supervisor |
| 指示灯 | 每功能一颗，5mA | 电流大时换驱动 |
| 连接器 | 凤凰端子 / PH2.0 | 封装要改，PCB 跟着改 |
| 保护 | 保险 + TVS | 现场无浪涌要求时可省 |

**假设必须落在 `spec.json` 的 `assumptions` 里**，并在交付时明确说"这些是我替你定的，错了告诉我"。

---

## 2. 六步流水线

### A. 需求 → 规格（`schem.py spec`）

```bash
python scripts/schem.py spec --req 需求.txt -o spec.json
```

它做关键词识别 + 数量提取，输出块清单、电源树草稿、假设、待确认清单。**它一定会漏、也一定会多**，因为自然语言里"3 路继电器驱动电磁阀"既可理解为"3 路继电器"也可能被误识别出 MOSFET 块。所以这一步的产物是**草稿**，必须人工改：

- 删掉误识别的块
- 补 `rails` 的电流（自动解析不可能知道电流）
- 补块的 `params`（UART 编号、晶振频率、线圈电流…）
- 把 `open_questions` 逐条发给客户

参考 `examples/spec.example.json`（由 `req.example.txt` 演化来的成品规格）。

### B. 规格 → 网表（`schem.py netlist`）

```bash
python scripts/schem.py netlist spec.json -o netlist.json --report
```

生成 76 个器件、57 个网络的完整连接关系。这一步会做四件事：

1. **分配位号**（U/R/C/D/Q/Y/J/F/K/SW/BZ 按种类递增）
2. **解析局部网络**（块内 `#CTRL` → `RLY1_CTRL`，多实例不串）
3. **补外围**（去耦、偏置、终端、续流——块定义里已经带好，不用人记）
4. **标出待绑定器件**（`binding: required` 且无 `lcsc`）

`--report` 打出电源网络与待确认清单。**先读这个报告再往下走**：电源网络引脚数是 0 或 1，说明电源树没接对。

### C. 网表 → ERC（`schem.py erc`）

```bash
python scripts/schem.py erc netlist.json            # 默认显示 FAIL/WARN/INFO
python scripts/schem.py erc netlist.json --min-severity WARN --strict   # CI 门禁
```

23 条规则，判据见 `references/schematic-rules.md` §9。

处理原则：

| 严重度 | 怎么处理 |
|---|---|
| `FAIL` | 必须改。这些是会烧或者根本不动的问题（LED 无限流、线圈无续流、CC 缺下拉、TVS 选小） |
| `WARN` | 改，或者在交付物里写明"为什么可以接受" |
| `INFO` | 数据不足或已声明例外。**不能当通过**，要逐条确认 |

`examples/netlist.bad.json` 是故意埋了 4 处 FAIL / 15 处 WARN 的反面样例，拿来验证检查器真的会报。

### D. 落笔前审计（`schem.py audit`）

```bash
python scripts/schem.py audit netlist.json --gate     # 1 BLOCKED / 2 INCOMPLETE / 0 PASS
```

ERC 查「连了什么、电气对不对」，audit 查「**该有的东西有没有**」。10 条规则，判据见 `references/schematic-rules.md` §12。

两条纪律：

- **`UNKNOWN` 不能当通过。** 库存没查、引脚名只有脚号，这两类都属"检查根本没跑"，`--gate` 判 `INCOMPLETE`（exit 2）。
- **别为了让 audit 变绿去补假数据。** 给 `supply.lcsc_stock` 填个 999 确实能让它过，但那和编料号是同一件事——只是错得更晚，在下单那一刻暴露。

`examples/netlist.xpin.json` 是埋了 4 类缺陷的反面夹具（BOOT 无自举电容、VIN 无 100nF、EN 悬空、LED 无限流），应报 `FAIL 2 / WARN 2 / UNKNOWN 1 / INFO 2`。

### E. 选型与绑定（人的工作，不能自动化）

**这一步脚本不替你决定，是刻意的。**

1. 对每个 `binding: required` 的器件，到立创商城按参数筛选：
   - 电气参数（电压/电流/频率/精度）
   - 封装（与结构、生产工艺匹配）
   - **库存与价格**（有货比参数漂亮更重要）
   - 生命周期（NRND/EOL 的不要）
2. 把料号填进 netlist：`"lcsc": "C14663"`
3. 落笔后从 EDA 回读引脚，把功能名映射到实际引脚号：

```bash
python scripts/schem.py bind netlist.json readback.json -o netlist.bound.json
```

回读格式（由 EDA 侧脚本导出）：

```json
{"U3": {"pins": [{"n": "24", "name": "VDD"}, {"n": "23", "name": "VSS"}]}}
```

### F. 落笔（`schem.py playbook`）

```bash
python scripts/schem.py playbook netlist.json --js apply.js
```

没填 `lcsc` 时**拒绝生成**（exit 2）——这是门禁，不要用 `--allow-missing` 绕过。生成后按 `references/eda-execution.md` §6 执行：

1. EDA 里「高级 → 运行脚本」贴 `apply.js`，F9
2. 等它放完器件、拉完网络短线
3. `sch_Document.save()`
4. **等 8~10 秒**再跑 `sch_Drc.check(true, false, true)`
5. 导出网表 `getNetlistFile`，与 `netlist.json` 逐网络比对
6. 人工整理走线走向（脚本只保证逻辑连通）

---

## 3. 四道门禁（不许跳）

| 门禁 | 在哪一步 | 不过会怎样 |
|---|---|---|
| **需求缺口未列清，不许出网表** | A → B | 带着错误假设画完，返工成本 = 重画 |
| **ERC 有 FAIL，不许落笔** | C → D | 把会烧的电路画进 EDA，后面 PCB 做得再对也是废板 |
| **审计有 FAIL 或 UNKNOWN，不许落笔** | D → E | 外围漏了、库存没查，落笔只是把问题画得更清楚 |
| **关键器件没选型，不许落笔** | E → F | 编个料号 → 批量买错料 → 整批报废 |

四道门禁都是 `schem.py` 里写死的返回码（`--strict` 时 WARN 也算不过），不靠自觉。

---

## 4. 交付物

原理图阶段结束时应该交出这些东西，**缺任何一项都算没做完**：

| 交付物 | 内容 | 给谁看 |
|---|---|---|
| `spec.json` | 块清单、电源树、假设、待确认 | 客户确认需求 |
| `netlist.json` | 器件清单 + 网络连接（可 diff 的文本） | 内部评审 / 版本管理 |
| ERC 报告 | 违规清单与处理结论 | 评审记录 |
| BOM（`schem.py bom`） | 按参数归并的物料表 + 选型状态 | 采购 |
| 落笔脚本 `apply.js` | 可复现的建图过程 | 换机器重画时用 |
| 网表比对记录 | EDA 导出网表 vs 设计网表 | 证明"画的和设计的一致" |
| 未解决项 | 假设清单 + 开放问题 | 交接给 PCB / 客户 |

**不要只说"原理图画好了"。** 用 `netlist.json` + ERC 报告说话，别人能复核。

---

## 5. 常见翻车（按踩坑频率排序）

1. **拿假设当需求**。客户说"24V"，实际现场是 24V±20% 且带浪涌——TVS 选小了，第一批就烧。
2. **抄参考设计不改分压**。buck 的 FB 分压对应的是参考设计的基准电压，换芯片后输出全错。
3. **终端电阻每个节点都加**。RS485 三个节点各 120Ω → 40Ω 负载，通信距离腰斩。
4. **LED 不串电阻**。看着"能亮"，实际 IO 在超规格工作。
5. **漏掉 BOOT0 的处理**。表现为"芯片有时候能跑有时候不能"，查一周。
6. **I2C 上拉抄 4.7k**。100kHz 能用，提到 400kHz 就时好时坏。
7. **料号是编的**。这是最贵的错误：PCB 打样几百块，买错料 + 贴片返工几千块，交期多两周。
8. **ERC 过了就以为电路对**。ERC 只查结构，查不了选型、时序、环路稳定性、EMC。

---

## 6. 与 PCB 阶段的交接

原理图完成后，PCB 阶段需要的输入是：

| 输入 | 从哪来 | 用途 |
|---|---|---|
| 器件清单（含封装） | `netlist.json` + BOM | 布局分区 |
| 网络表 | EDA 导出网表 | 走线、DRC |
| 电源树与电流 | `spec.rails` | 线宽计算（`rules.py width`） |
| 电压等级 | `spec.rails[].v` | 电气间距（`rules.py clearance`） |
| 功能块划分 | `spec.blocks` | 布局分区（`plan.py` 的 `blocks`） |
| 高频/差分对 | netlist 里的网络名 | 阻抗设计、等长 |

**交接检查**：`plan.py` 的功能块应该与 `spec.blocks` 一一对应。对不上说明布局计划漏了模块，这是布局阶段最常见的问题来源。
