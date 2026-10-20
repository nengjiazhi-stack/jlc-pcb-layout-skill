# jlc-pcb-layout 安装包

> **给 AI Agent 的指令**：这是一个 Codex / WorkBuddy 技能包。用户把它丢给你，就是要你把它装进本机。
> 请严格按下面「二、Agent 自动安装」执行，装完跑「三、自检」，把结果报给用户。
> 不要修改技能内的任何文件内容。

---

## 一、这个包是什么

`jlc-pcb-layout` —— 在嘉立创EDA专业版（EasyEDA Pro）里按企业规范画 PCB Layout 的技能。

把 6 份企业 PCB 规范（华为、爱默生、华硕、上海贝尔、通用、器件建库）拆成了三层可执行资产：

| 层 | 位置 | 作用 | 外部依赖 |
|---|---|---|---|
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
python scripts/rules.py width --current 3 --copper 1oz
python scripts/plan.py assets/plan.example.json -o /tmp/p.json --report
python scripts/check.py examples/snapshot.demo.json --min-severity WARN
python scripts/check.py examples/snapshot.aesthetics.json --min-severity WARN
```

期望输出（用这四条判断装没装好）：

```
需求：3.0A / 1oz / ΔT=10.0℃
最小线宽：1.5mm (59.1mil)
该档载流：3.2A
```

```
26 颗器件，占板 18.3%，无溢出块
```

```
检查结果：FAIL 1 / WARN 14 / INFO 3
[FAIL] ✗ FAB-SHORT      VDD × GND
```

```
检查结果：FAIL 0 / WARN 7 / INFO 5
[WARN] ! AEST-ALIGN     C 组(0402) 第行
        5 颗本应共线，C5 偏离基准线 22mil（基准 500mil，容差 12mil）
```

第三条 **FAIL 1 是正常的**——`snapshot.demo.json` 是故意埋了缺陷的示例板，用来演示检查器能抓到短路。`examples/snapshot.demo.fixed.json` 是修好的版本，跑它是 `FAIL 0 / WARN 6`。

第四条里的 `AEST-*` 也是**故意做的反面样例**（对齐、间距、朝向、角度各埋一条），同样不是装坏了。

Python 找不到时用 `python3`，或告知用户需要 Python 3.8+。

---

## 四、可选依赖：落笔层

`easyeda` CLI 不在本包内。没有它，规范层/计算层/计划层/检查层全部照常工作，只是最后"写进画布"这一步要人工在 EDA 里做。

需要自动化时，装社区项目：

https://github.com/zhoushoujianwork/easyeda-agent

装完用 `easyeda health` 确认 EasyEDA 窗口已连接。

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
  INSTALL.md               同上说明（随技能一起装）
  agents/openai.yaml       Codex 界面显示名与默认提示词
  assets/
    constraints.json       全部数值约束，唯一真源，每节带条款出处
    plan.example.json      计划文件示例
  references/
    layout-rules.md        布局规范
    routing-rules.md       布线规范（含走线二十条）
    stackup-and-impedance.md  叠层与阻抗
    dfm-assembly-rules.md  工艺与组装
    routing-aesthetics.md  图面整洁度：判据、证据强度分级、与性能的裁决顺序
    eda-execution.md       两条执行通道 + 落笔顺序 + 已知坑
    layout-plan.md         计划文件 JSON 规格
  scripts/
    rules.py               六个计算子命令
    plan.py                布局坐标计划生成器
    check.py               规范符合性检查器
  examples/
    walkthrough.md         60×45 四层板完整走查（含真实输出）
    snapshot.demo.json     故意埋缺陷的快照（FAIL 1 / WARN 14）
    snapshot.demo.fixed.json  修好后的快照（FAIL 0 / WARN 6）
    snapshot.aesthetics.json  整洁度反面样例（AEST-ALIGN/PITCH/ROT/ANGLE 各一条）
    snapshot.placement.json   元器件摆放反面样例（PL-ORI/HEIGHT/AUTO-IND/MECH-CG/MAINT-CONN/FID）
```
