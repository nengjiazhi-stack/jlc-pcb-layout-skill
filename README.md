# jlc-pcb-layout

给 AI 用的**嘉立创EDA 专业版**硬件设计技能：从一句客户需求出发做原理图，再从网表一路做到可投板的 PCB Layout。

> **作用域只有立创EDA 专业版。** 不含 KiCad / Altium / PADS 等任何其它 EDA 的适配、转换或示例；执行层的每一行代码都只对嘉立创EDA 的 API 与 CLI 成立。

面向 Codex / WorkBuddy 等 Agent 宿主。装上之后说「客户要个 4 路继电器控制板，24V 供电，带 485」或者「3A 电流在 1oz 铜上要多少线宽」，它会去跑脚本出网表、查规范数值、生成坐标计划、跑检查器，而不是凭记忆编数字和料号。

---

## 两块能力

```
客户需求 ──schem.py──► 网表 ──ERC+audit──► 落笔原理图 ──► 导入PCB ──plan.py──► 布局计划 ──► 走线 ──check.py──► 投板
           原理图                     ↑ 不合规停在这                            PCB Layout
```

| | 原理图 | PCB Layout |
|---|---|---|
| 输入 | 客户功能描述 | 网表 + 板框约束 |
| 核心工具 | `schem.py`（spec/netlist/erc/audit/bom/bind/playbook） | `plan.py` + `check.py` |
| 规则库 | 15 个功能块 + 5 颗芯片实例 + 23 条 ERC + 10 条落笔前审计 | 6 套企业规范 + 38 条 DRC/DFM/块级规则 |
| 数值 | 上拉/限流/负载电容/偏置/电感/TVS 档位 | 载流线宽/电气间距/过孔/叠层/阻抗 |
| 落笔 | `eda.sch_*` API | `easyeda` CLI + `eda.pcb_*` API |

## 它解决什么

让模型做硬件设计，六种典型翻车：

| 翻车 | 表现 | 这里的对策 |
|---|---|---|
| 凭记忆报工艺数值 | 「线宽 1mm 大概够 2A 吧」 | 所有数值走 `rules.py`，查不到就说不确定并给保守值 |
| 凭记忆报电路数值 | 「I2C 上拉 4.7k 是标准值」 | 走 `schem.py calc`：400kHz/200pF 时上限只有 1.8k，4.7k 超了 |
| 编料号 | 「用 STM32F103C8T6，立创编号 C8734」——编号是猜的 | **脚本强制门禁**：没填 `lcsc` 就拒绝生成落笔代码 |
| 漏必接外围 | 继电器不并续流二极管、Type-C 只接一颗 CC 电阻 | 功能块自带外围，ERC 专查这四类（FAIL 级） |
| 凭感觉摆坐标 | 即兴 `x = 1000`，摆完发现去耦电容离 IC 3cm | 坐标只能来自 `plan.py` 的计划文件 |
| 无验证收工 | 「已完成」，实际 DRC 一堆错 | 必须跑 `erc`/`check.py` + 原生 DRC + 网表比对 |

## 结构

```
需求层  references/schematic-workflow.md   澄清清单 / 六步流水线 / 四道门禁
电路层  assets/circuit-blocks.json         15 个功能块的可执行定义
        references/circuit-blocks.md       每块"为什么这么接 + 常见错误"
芯片层  assets/chip-variants.json           5 颗具体型号：真实脚号/料号/块级布局约束/出处
        references/chip-variants.md        收录标准 + 7 种布局约束的可检查性
规则层  references/schematic-rules.md      23 条 ERC + 10 条落笔前审计 判据 + 数值表
        references/layout-rules.md 等      6 份企业规范转写（TY- / EM- / BELL- / ASUS-）
计算层  scripts/schem.py                   11 个电路计算器
        scripts/rules.py                   载流/间距/过孔/层数/阻抗
执行层  scripts/plan.py                    布局坐标计划生成器
        scripts/check.py                   PCB 合规检查器（38 个规则号，四态含 UNKNOWN）
        references/eda-execution.md        两套 API 的落笔顺序与坑
```

## 安装

### 方式一：脚本安装（推荐）

```bash
git clone https://github.com/nengjiazhi-stack/jlc-pcb-layout-skill.git
cd jlc-pcb-layout-skill
bash install.sh          # macOS / Linux / Git Bash
```

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本装到 `~/.codex/skills/jlc-pcb-layout`，并同步一份到 `~/.workbuddy/skills/`。重复执行是幂等的（先删旧再拷）。

### 方式二：手动复制

```bash
cp -r jlc-pcb-layout ~/.codex/skills/jlc-pcb-layout
```

### 方式三：把 zip 丢给 Agent

下载 Releases 里的 `jlc-pcb-layout-skill.zip` 拖进 Codex 对话框，`INSTALL.md` 开头是写给 Agent 的安装指令，它会自己解压、安装、跑自检。

> **装完必须完全退出宿主进程再重开**（关窗口不算）。重开后 `$jlc-pcb-layout` 或直接说「帮我按需求做块板」即可命中。

## 自检

```bash
cd ~/.codex/skills/jlc-pcb-layout
python scripts/schem.py netlist examples/spec.example.json -o n.json --report
python scripts/schem.py erc examples/netlist.example.json
python scripts/schem.py erc examples/netlist.bad.json
python scripts/schem.py audit examples/netlist.example.json
python scripts/schem.py audit examples/netlist.xpin.json
python scripts/rules.py width --current 3 --copper 1oz
python scripts/check.py examples/snapshot.demo.json --min-severity WARN
```

期望输出：

```
器件 76（其中待绑定 16/16），网络 57，功能块 11

检查结果：FAIL 0 / WARN 0 / INFO 12                    ← 成品网表：结构干净，INFO 全是"待选型/GPIO 未分配"
检查结果：FAIL 4 / WARN 15 / INFO 2                    ← 反面样例：LED 无限流、线圈无续流、TVS 选小、CC 缺下拉

审计结果：FAIL 0 / WARN 0 / UNKNOWN 1 / INFO 1        ← 成品网表的"该有的有没有"：唯一缺口是 U2 引脚名只有脚号
审计结果：FAIL 2 / WARN 2 / UNKNOWN 1 / INFO 2        ← 反面夹具：BOOT 无自举电容、VIN 无 100nF、EN 悬空、LED 无限流

最小线宽：1.5mm (59.1mil)   该档载流：3.2A
检查结果：FAIL 1 / WARN 14 / UNKNOWN 2 / INFO 1        ← demo 板：故意埋的 VDD×GND 短路
```

**后两条 ERC 是成对的自检设计**：好的必须干净、坏的必须报错。只测前者等于没测——检查器永远返回 0 违规也能"通过"。

`snapshot.demo.json` 的 FAIL 1 同样是故意埋的短路，用来演示 `check.py` 能抓到；修好的版本是 `snapshot.demo.fixed.json`：`FAIL 0 / WARN 6 / UNKNOWN 2`。

注意那个 **UNKNOWN 2**——修好之后反而不是"通过"。这两条是"缺 traces 数据"和"缺器件高度"，说明这两类检查**根本没跑**。以前它们混在 INFO 里，`FAIL 0` 看起来像合格；现在独立成 UNKNOWN，`--gate` 判 `INCOMPLETE`。**测不到不等于通过，这是从执行层项目学来的最重要一条。**

## 原理图部分

### 15 个功能块

`power_protect`（保险+TVS）、`power_ldo`、`power_buck`、`mcu_min`（复位/时钟/去耦/下载口四件套）、`usb_typec_ufp`、`rs485`、`can`、`relay_drive`、`mosfet_load`、`key`、`led_ind`、`i2c_port`、`uart_port`、`adc_divider`、`buzzer`。

每个块定义的不只是"有哪些器件"，还有：必接外围、参数怎么算、常见错误、对应的 ERC 规则号。例如 `rs485` 自带失效保护偏置与可选 120Ω 终端——中间节点把 `term` 设 `false`，模块会跳过终端电阻。

### 23 条 ERC

| 组 | 规则 | 管什么 |
|---|---|---|
| 结构性 `SCH-ERC-*` | 位号重复、引脚冲突、单点悬空 | 网表本身是否自洽 |
| 电源 `SCH-PWR-*` | 每脚去耦、每路 bulk、输入保护 | 上电能不能稳 |
| 主控 `SCH-MCU-*` / `SCH-XTAL-CL` | 复位 RC、时钟、负载电容对称 | 能不能启动 |
| 接口 `SCH-USB-*` / `SCH-RS485-*` / `SCH-CAN-TERM` / `SCH-I2C-PULLUP` | CC 下拉、ESD、终端、偏置、上拉 | 接口能不能通 |
| 驱动 `SCH-LED-RES` / `SCH-RELAY-FLYBACK` | 限流、续流 | 会不会烧 |
| 保护/采集 `SCH-TVX-VRWM` / `SCH-ADC-IMP` | TVS 档位、ADC 源阻抗 | 现场会不会死 |
| 数据缺口 `SCH-BINDING` / `SCH-DATA` | 未选型、缺电压 | **不能当通过** |

`FAIL` 是会烧或根本不动；`WARN` 是大概率出问题；`INFO` 是数据不足或已声明例外——**INFO 不等于通过**，交付时要逐条确认。

### 10 条落笔前审计

ERC 查「连了什么、电气对不对」，`schem.py audit` 查「**该有的东西有没有**」。两条都过才许落笔。

| 规则 | 查什么 | 为什么单独一条 |
|---|---|---|
| `SCH-BOM-XPIN` FAIL | **跨引脚电容**漏了 | `SETC`/`VREG`/`CP`/`BOOT`/`SS`/`COMP` 这些脚不在 VCC 上，以芯片为中心设计时最易漏，漏了芯片不工作 |
| `SCH-BOM-DECAP` FAIL | 电源脚没有 10nF~1µF 且接地的电容 | 必须真的接地，跨在两路电源之间的不算 |
| `SCH-BOM-PULL` WARN | 控制脚悬空 | 必须真的被电阻拉到电源/地，串在信号路上的电阻不算 |
| `SCH-BOM-LED` WARN | LED 没串限流 | 必须真的串联，共用电源轨上的分压电阻不算 |
| `SCH-BOM-CONN` INFO | 外露接口没 ESD | USB/485/CAN/RJ45 都算 |
| `SCH-BOM-TP` INFO | 全板没测试点 | 量产返修靠它 |
| `SCH-NC-UNCONN` INFO | 引脚没接网络 | 含"指向不存在的网络"这种悬空引用 |
| `SCH-SUPPLY-STOCK` 动态 | 库存 0→FAIL，<50→WARN，**未核验→UNKNOWN** | 料号真实但库存 0，一样下不了单 |
| `SCH-SUPPLY-LIB` INFO | 非基础库/优选库 | 长期供货与价格都不占优 |
| `SCH-AUDIT-BLIND` UNKNOWN | 引脚名只有脚号 | 去耦和专用脚**根本没查**，不能算过 |

三条判定纪律都是为了堵「看起来查了、其实没查」：**「网上有电容」不算去耦、「网上有电阻」不算上拉、「引脚名是数字」不算通过。**

`--gate` 的判定：`1` BLOCKED（有 FAIL）、`2` INCOMPLETE（有 UNKNOWN）、`0` PASS。库存没查、引脚名不可识别这两类会让检查**根本没跑**，`FAIL 0` 看着像合格，`INCOMPLETE` 就是不让它蒙过去。

### 四道门禁

| 门禁 | 不过会怎样 |
|---|---|
| 需求缺口未列清，不许出网表 | 带着错误假设画完，返工 = 重画 |
| ERC 有 FAIL，不许落笔 | 把会烧的电路画进 EDA |
| 审计有 FAIL 或 UNKNOWN，不许落笔 | 外围漏了/库存没查，落笔只是把问题画得更清楚 |
| 关键器件没选型，不许落笔 | 编个料号 → 批量买错料 → 整批报废 |

都是 `schem.py` 里写死的返回码（`--strict` 时 WARN 也算不过），不靠自觉。

## 芯片实例层

拓扑块知道「要串限流电阻」，但不知道「SP3485 的 DI 是第 4 脚」。`assets/chip-variants.json` 补的是后一半：**5 颗具体型号的真实引脚表 + 料号 + 块级布局约束 + 出处**。

```bash
python scripts/schem.py chips                    # 看有哪几颗、各自什么状态
python scripts/schem.py netlist spec.json --chip rs485=sp3485 --chip power_ldo=ams1117_33 -o nl.json
```

| id | 块 | 型号 | 料号 | 引脚 | 货源 |
|---|---|---|---|---|---|
| `ams1117_33` | power_ldo | AMS1117-3.3 | C6186 | 3/3 完整 | 库存未核验 |
| `sp3485` | rs485 | SP3485 | 需选型 | 8/8 完整 | 无料号 |
| `sy8089` | power_buck | SY8089A | 需选型 | 5/5 完整 | 无料号 |
| `usb_c_16p` | usb_typec_ufp | USB-C 16P | 需选型 | 部分（DP/DN 双焊盘） | 无料号 |
| `esp32s3_wroom1` | mcu_min | ESP32-S3-WROOM-1 | 需选型 | **不可套用** | 无料号 |

**只有一颗是「引脚完整 + 有料号」的可用组合**，而且它的库存还没查——所以 `audit` 会把它报成 `UNKNOWN`，不是 `PASS`。其余料号空着是故意的：宁可让落笔脚本拒绝生成，也不让你拿着编出来的 C 号去下单。

货源分 P0-P3 四档（基础库+库存≥500 / 优选库+≥100 / ≥50 / <50），库存 0 直接 FAIL。**`null` 一律 UNKNOWN，不参与分档**——给个 999 看着方便，但那是编的数据，和编料号一样危险，只是错误暴露在下单那一刻。详见 `references/chip-variants.md`。

最后那颗 `esp32s3_wroom1` 是个刻意留的反面教材：它和 `mcu_min` 块的引脚名根本对不上（ESP32-S3 内建晶振、调试是 JTAG、复位脚叫 EN），硬套会生成一张看起来正常、实际全错的网表。所以它带 `blocked_by`，套用时直接报错并告诉你该新建哪种块。**能明确说「不行」的知识，比含糊能用的值钱。**

## PCB 部分

### 38 个规则号，五组

| 组 | 规则 | 管什么 |
|---|---|---|
| 布局 `FAB-* / TY-* / EM-*` | 出框、板边净空、courtyard 间距、BGA 禁布、安装孔禁布、去耦距离、晶振距离 | 板能不能做出来 |
| 摆放 `PL-*` | 极性朝向、IC 1 脚朝向、器件高度、高矮混放、电感正交（车规）、重心偏移、插拔净空、MARK 数量与跨距 | 摆得对不对 |
| 布线 `TY-6.5.6-*` | 悬空线头、直角/锐角、3W、异网短路、线宽一致性、过孔孔厚比 | 走线合不合规 |
| 图面整洁 `AEST-*` | 共线、阵列间距、0/90/180/270、0/45/90 角度栅格 | 齐不齐 |
| 块级约束 `BLK-*` | 去耦贴 IC ≤2mm、保护器件贴连接器 ≤5mm（几何硬算）；其余约束汇总报 UNKNOWN | 芯片级要求落实了没 |

摆件顺序是硬约束（P0 机械锁定 → P1 功率热源 → P2 核心 IC → P3 就近器件 → P4 敏感小信号 → P5 其余阻容 → P6 测试点/MARK/丝印），前一档未锁定不得进入下一档。

### 两趟高度：估算排一遍，回读真实高度再排一遍

`plan.py` 排坐标靠 `w_mm`/`h_mm`，填的通常是封装名义尺寸。但 EDA 符号的实际高度经常不一样——带屏蔽罩的模块、带散热片的 LDO、带排针的插座都高一截。只排一趟，叠件要等 `check.py` 事后才报。

```bash
python scripts/plan.py spec.json -o pass1.json --emit-heights heights.todo.json   # 第一趟 + 列回读清单
python scripts/plan.py spec.json --heights heights.json -o plan.json --report      # 第二趟用实测重排
```

报告末行的 `高度：实测 N / 估算 M` 要读到**估算 0** 才算收工。仍标 `estimated` 的器件，叠件风险**没有排除**——那不是"大概没事"，是"没查"。计划里每颗器件带 `height_source`（`measured`/`estimated`/`given`），可逐条核对。

## 目录结构

```
jlc-pcb-layout-skill/
├── INSTALL.md              给 Agent 看的安装指令
├── install.sh / install.ps1
└── jlc-pcb-layout/         ← 技能本体
    ├── SKILL.md
    ├── agents/openai.yaml  Codex 入口：显示名与默认提示词
    ├── references/         原理图工作流/功能块/ERC 判据 + 6 份规范转写 + 执行手册
    ├── scripts/            schem.py / rules.py / plan.py / check.py
    ├── assets/             constraints.json（数值唯一真源）+ circuit-blocks.json
    └── examples/           需求样例、成品 spec/netlist、反面 netlist（ERC/审计）、6 个 PCB 快照、实测高度样例、两份走查
```

阈值全部集中在 `assets/constraints.json`（原理图部分在 `schematic` 节），觉得哪个太严太松直接改数字，不用改代码。功能块在 `assets/circuit-blocks.json`，加块的方法见 `references/circuit-blocks.md` 末节。

## 边界（先说清楚）

- **不编料号。** 拓扑块只给"必接什么、参数范围"；芯片实例只收核对过 datasheet 的脚号，料号没有就留空。这是刻意的——料号编错会造成批量买错料，比画错图贵得多。
- **`UNKNOWN` 不等于通过。** 缺 traces、缺高度、缺丝印、块级约束无几何判据，都会报 UNKNOWN。`--gate` 判 `INCOMPLETE`，退出码 2。把它当 `FAIL 0` 读，就等于把"没查"当成"没问题"。
- **ERC 查不出全部。** 它只查结构性错误（悬空、缺失、极性、数值越界）。查不出：选型是否合适、时序是否满足、环路是否稳定、EMC 是否过关。这些必须人看。
- **审计只查「有没有」，不查「合不合适」。** `audit` 能发现 BOOT 脚没有自举电容，但发现不了这颗电容选了 1µF 而手册要求 100nF；能发现库存未核验，但不会替你判断这颗料值不值得用。它堵的是"整类漏掉"，不是"选错值"。
- **库存必须你自己查。** 本仓库不联网、不调 MCP、不缓存任何商城数据。`supply.lcsc_stock` 恒为 `null` 时 `audit` 恒报 UNKNOWN——这是设计如此，不是 bug。
- **落笔只保证逻辑连通。** `playbook` 生成的脚本用"每个引脚拉一段带网络名的短线"建立连接，同名即连通。走线走向要人工整理——逻辑连通可验证，好看不可验证。
- **不装 CLI 也能用前两层。** 只能算数值、出网表、跑 ERC、出 BOM，最后落笔得自己在画布上点。
- **只做立创EDA 专业版，不做通用 EDA。** 落笔用的 `sch_*` / `pcb_*` API、10mil 原理图栅格、25mil PCB 栅格、A4 图幅 1170×825、`lceda-pro` / `easyeda` CLI 全部只对嘉立创EDA专业版成立。换到 KiCad、Altium、PADS 等工具，判据层（数值计算、ERC、网表）仍可读，但执行层一行都用不了——本仓库不提供、也不打算提供其它 EDA 的适配。

## 依赖

| 层 | 依赖 | 说明 |
|---|---|---|
| 规范/需求层 | 无 | 纯 Markdown |
| 计算/检查层 | Python 3.8+，**纯标准库** | 不用 pip install |
| 落笔层 | 嘉立创EDA专业版 + `easyeda` CLI / `lceda-pro` CLI | 可选，见 `references/eda-execution.md` |

## 数值来源

PCB 侧条款来自华为 PCBLayout 设计规范、爱默生 PCB 设计规范、华硕内部 PCB 设计规范、上海贝尔 PCB 设计规范、印刷电路板通用设计规范、PCB 器件建库规范，以及 IPC-7351B 与通用 DFM 口径。

原理图侧数值来自 I2C-bus specification UM10204、USB Type-C Cable and Connector Spec R2.0、TIA-485-A、ISO 11898-2、常见器件手册与厂商参考设计（TI/ADI/ST）。

出处写在 `constraints.json` 的 `_source` 字段里，`check.py` / `schem.py erc` 输出的规则号可直接回 `references/` 查原文。

**规范冲突时取严，与厂家能力表冲突时以厂家为准。** 投板前必须确认 PCB/SMT 厂家的实际能力——本仓库给的是保守起点，不是验收依据。原理图的安规间距（>60V、市电、医疗）本仓库不覆盖，必须由持证工程师审。

## License

MIT，见 [LICENSE](LICENSE)。
