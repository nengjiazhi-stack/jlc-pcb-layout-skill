# jlc-pcb-layout 安装包

> **给 AI Agent 的指令**：这是一个 Codex / WorkBuddy 技能包。用户把它丢给你，就是要你把它装进本机。
> 请严格按下面「二、Agent 自动安装」执行，装完跑「三、自检」，把结果报给用户。
> 不要修改技能内的任何文件内容。

---

## 一、这个包是什么

`jlc-pcb-layout` —— 在嘉立创EDA专业版（EasyEDA Pro）里**按客户需求做原理图、再按企业规范画 PCB Layout** 的技能。

原理图侧把 15 个常用功能块（电源/主控/RS485/CAN/USB-C/继电器/按键/LED/I2C/ADC/蜂鸣器）变成可执行网表 + 23 条 ERC 规则；PCB 侧把 6 份企业规范（华为、爱默生、华硕、上海贝尔、通用、器件建库）拆成量化条款。共六层可执行资产：

| 层 | 位置 | 作用 | 外部依赖 |
|---|---|---|---|
| 需求层 | `jlc-pcb-layout/references/schematic-workflow.md` | 需求澄清清单 / 五步流水线 / 三道门禁 | 无 |
| 电路层 | `assets/circuit-blocks.json` + `references/circuit-blocks.md` | 15 个功能块的参考电路与必接外围 | 无 |
| 原理图校验 | `scripts/schem.py` | 需求→规格→网表→ERC→BOM→落笔脚本 | 仅 Python 3.8+，纯标准库 |
| 规范层 | `jlc-pcb-layout/references/` | 条款化规范，带原始编号可回查 | 无 |
| 计算层 | `jlc-pcb-layout/scripts/rules.py` | 载流线宽 / 电气间距 / 过孔 / 层数 / 阻抗 | 仅 Python 3.8+，纯标准库 |
| 计划层 | `jlc-pcb-layout/scripts/plan.py` | 出摆件坐标计划 | 同上 |
| 检查层 | `jlc-pcb-layout/scripts/check.py` | 16 类规范符合性检查（含图面整洁度 `AEST-*`） | 同上 |
| 落笔层 | 需另装 `easyeda` CLI | 把计划写进 EDA 画布 | 见「四、可选依赖」 |

---

## 二、Agent 自动安装

**判断操作系统，选一条执行。**

### Windows（PowerShell）

```powershell
cd <解压后的目录>
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### macOS / Linux（bash）

```bash
cd <解压后的目录>
bash ./install.sh
```

脚本会做四件事：清掉旧版本 → 复制到 `~/.codex/skills/jlc-pcb-layout` → 同时复制一份到 `~/.workbuddy/skills/jlc-pcb-layout` → 校验 `SKILL.md` 是否存在且 frontmatter 合法。

如果你想手工装（不跑脚本），等价于：

```bash
mkdir -p ~/.codex/skills
rm -rf ~/.codex/skills/jlc-pcb-layout
cp -r jlc-pcb-layout ~/.codex/skills/jlc-pcb-layout
```

**装完必须告诉用户：Codex 要完全退出进程再重开才会加载技能**（关窗口不算）。

---

## 三、自检（必跑）

```bash
cd ~/.codex/skills/jlc-pcb-layout
python scripts/schem.py netlist examples/spec.example.json -o n.json --report
python scripts/schem.py erc examples/netlist.example.json
python scripts/schem.py erc examples/netlist.bad.json
python scripts/schem.py chips
python scripts/rules.py width --current 3 --copper 1oz
python scripts/plan.py assets/plan.example.json -o p.json --report
python scripts/check.py examples/snapshot.demo.json --min-severity WARN
python scripts/check.py examples/snapshot.aesthetics.json --min-severity WARN
python scripts/check.py examples/snapshot.decap.bad.json --netlist examples/netlist.decap.json
```

期望输出（九条，顺序与上面一一对应）：

```
器件 76（其中待绑定 16/16），网络 57，功能块 11
```

```
检查结果：FAIL 0 / WARN 0 / INFO 12
```

```
检查结果：FAIL 4 / WARN 15 / INFO 2
[FAIL] ✗ SCH-LED-RES       D1
[FAIL] ✗ SCH-RELAY-FLYBACK  K1
```

```
5 个芯片实例

ams1117_33         → power_ldo        AMS1117-3.3
   SOT-223            LCSC C6186          3 脚 引脚完整 · 2 条布局约束
esp32s3_wroom1     → mcu_min          ESP32-S3-WROOM-1
   Module-SMD         LCSC —（需选型）         4 脚 ✗ 不可套用 · 3 条布局约束
```

```
需求：3.0A / 1oz / ΔT=10.0℃
最小线宽：1.5mm (59.1mil)
该档载流：3.2A
```

```
26 颗器件，占板 18.3%，无溢出块
```

```
检查结果：FAIL 1 / WARN 14 / UNKNOWN 2 / INFO 1
[FAIL   ] ✗ FAB-SHORT      VDD × GND
```

```
检查结果：FAIL 0 / WARN 7 / UNKNOWN 4 / INFO 1
[WARN   ] ! AEST-ALIGN     C 组(0402) 第行
        5 颗本应共线，C5 偏离基准线 22mil（基准 500mil，容差 12mil）
```

```
检查结果：FAIL 1 / WARN 0 / UNKNOWN 6 / INFO 1
[FAIL   ] ✗ BLK-DECAP      U4/decap-adjacency
        C9 到 U4 间距 8.13mm > 2.0mm（紧贴 VCC 脚，供电环路短）
```

> **ERC 两条是成对设计**：成品网表必须干净，反面样例必须报 4 个 FAIL。只跑前者等于没测。
> **BLK 那条是反例**：`snapshot.decap.bad.json` 故意把去耦放到 8mm 外，必须报 FAIL。配套的 `.ok.json` 是 1.8mm，应通过。

`snapshot.demo.json` 的 **FAIL 1 是正常的**——它是故意埋了缺陷的示例板（VDD×GND 短路），用来演示检查器能抓到。修好的版本 `snapshot.demo.fixed.json` 跑出来是 `FAIL 0 / WARN 6 / UNKNOWN 2`。`netlist.bad.json` 的 4 个 FAIL 同理，是反面样例（LED 无限流、线圈无续流、TVS 选小、CC 缺下拉）。

`UNKNOWN` 不是错误、也不是通过：它表示**这项检查没跑**（缺 traces 数据、缺器件高度等）。修好的板子出现 `UNKNOWN 2` 是正常的，`--gate` 会判 `INCOMPLETE`。要消除它，就把对应的数据补进快照。

**这些 FAIL 都不是装坏了**，而是"检查器真的会报错"的证据。如果反面样例跑出来 0 违规，说明装坏了。

Python 找不到时用 `python3`，或告知用户需要 Python 3.8+。

---

## 四、可选依赖：落笔层

`easyeda` CLI 不在本包内。没有它，规范层/计算层/计划层/检查层全部照常工作，只是最后"写进画布"这一步要人工在 EDA 里做。

需要自动化时，装社区项目：

https://github.com/zhoushoujianwork/easyeda-agent
（维护分支，功能更全：https://github.com/yitengchun833-gif/easyeda-agent-open）

装完用 `easyeda health` 确认 EasyEDA 窗口已连接。对接细节与边界见 `references/integration.md` —— 它管"画得对不对"，本包管"设计对不对"，两者不重叠。

---

## 五、卸载

删目录即可：

```bash
rm -rf ~/.codex/skills/jlc-pcb-layout
rm -rf ~/.workbuddy/skills/jlc-pcb-layout
```

---

## 六、包内清单

```
INSTALL.md                 本文件
install.ps1                Windows 安装脚本
install.sh                 macOS/Linux 安装脚本
jlc-pcb-layout/
  SKILL.md                 技能主入口（frontmatter + 执行流水线）
  agents/openai.yaml       Codex 界面显示名与默认提示词
  assets/
    constraints.json       全部数值约束，唯一真源，含 schematic 节
    chip-variants.json     5 颗芯片实例：真实脚号/料号/块级布局约束/出处
    circuit-blocks.json    15 个功能块的可执行定义
    plan.example.json      计划文件示例
  references/
    schematic-workflow.md  需求 → 原理图 工作流（澄清/五步/门禁/交付物）
    circuit-blocks.md      15 个功能块参考电路（组成/参数/常见错误）
    schematic-rules.md     原理图规范 + 23 条 ERC 判据 + 数值表
    chip-variants.md       芯片实例层：收录标准、5 颗的真实状态、布局约束可检查性
    integration.md         与执行层（easyeda-agent-open）对接：已验证事实、三个对接点
    layout-rules.md        布局规范
    routing-rules.md       布线规范（含走线二十条）
    stackup-and-impedance.md  叠层与阻抗
    dfm-assembly-rules.md  工艺与组装
    routing-aesthetics.md  图面整洁度：判据、证据强度分级、与性能的裁决顺序
    eda-execution.md       两条执行通道 + 落笔顺序 + 已知坑
    layout-plan.md         计划文件 JSON 规格
  scripts/
    schem.py               原理图生成与校验（spec/netlist/erc/bom/bind/playbook + 11 个计算器）
    rules.py               六个计算子命令
    plan.py                布局坐标计划生成器
    check.py               规范符合性检查器（四态含 UNKNOWN；--gate 单一判定；--netlist 并入 ERC 与块级约束）
  examples/
    walkthrough.md         60×45 四层板完整走查（含真实输出）
    walkthrough-schematic.md  从客户一句话到可投板原理图的走查（含真实输出）
    req.example.txt        客户需求原文样例
    spec.example.json      成品设计规格
    netlist.example.json   成品网表（76 器件 / 57 网络，ERC FAIL 0）
    netlist.bad.json       反面网表（4 FAIL / 15 WARN）
    netlist.decap.json     块级约束夹具（验证 BLK-DECAP）
    snapshot.decap.ok.json    正例：去耦 1.8mm 应通过
    snapshot.decap.bad.json   反例：去耦 8.1mm 必须 FAIL
    snapshot.demo.json     故意埋缺陷的快照（FAIL 1 / WARN 14）
    snapshot.demo.fixed.json  修好后的快照（FAIL 0 / WARN 6）
    snapshot.aesthetics.json  整洁度反面样例（AEST-ALIGN/PITCH/ROT/ANGLE 各一条）
    snapshot.placement.json   元器件摆放反面样例（PL-ORI/HEIGHT/AUTO-IND/MECH-CG/MAINT-CONN/FID）
```
