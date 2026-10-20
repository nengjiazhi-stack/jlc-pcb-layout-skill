# jlc-pcb-layout

把 6 份企业 PCB 设计规范里的量化条款，变成 AI 在**嘉立创EDA专业版**里能直接执行的三个工具：**算**、**排**、**查**。

面向 Codex / WorkBuddy 等 Agent 宿主，装上之后说一句「3A 电流在 1oz 铜上要多少线宽」或「帮我把这块板摆一下」，它会去跑脚本查数值、出坐标计划、跑合规检查，而不是凭记忆编数字。

---

## 它解决什么

让模型画 PCB，最常见的三种翻车方式：

| 翻车方式 | 表现 | 这里的对策 |
|---|---|---|
| 凭记忆报数值 | 「线宽 1mm 大概够 2A 吧」 | 所有数值走 `rules.py`，查不到就说不确定并给保守值 |
| 凭感觉摆坐标 | 即兴 `x = 1000`，摆完发现去耦电容离 IC 3cm | 坐标只能来自 `plan.py` 出的计划文件，可追溯 |
| 无验证收工 | 「已完成布局」，实际 DRC 一堆错 | 必须跑 `check.py` + 原生 DRC，违规逐条回修或明确记为已接受风险 |

## 三层结构

```
规范层  references/        6 份规范逐页转写，按条款号索引（TY- / EM- / BELL- / ASUS-）
计算层  scripts/rules.py   载流线宽 / 电气间距 / 过孔 / 层数 / 线宽间距档位 / 阻抗
执行层  scripts/plan.py    布局坐标计划生成器
        scripts/check.py   33 条规则的符合性检查器
```

## 安装

### 方式一：脚本安装（推荐）

```bash
git clone https://github.com/<your-name>/jlc-pcb-layout-skill.git
cd jlc-pcb-layout-skill
bash install.sh          # macOS / Linux / Git Bash
```

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会装到 `~/.codex/skills/jlc-pcb-layout`，并同步一份到 `~/.workbuddy/skills/`。重复执行是幂等的（先删旧再拷）。

### 方式二：手动复制

```bash
cp -r jlc-pcb-layout ~/.codex/skills/jlc-pcb-layout
```

### 方式三：把 zip 丢给 Agent

把 `jlc-pcb-layout-skill.zip` 拖进 Codex 对话框，`INSTALL.md` 开头是直接写给 Agent 的安装指令，它会自己解压、安装、跑自检。

> **装完必须完全退出宿主进程再重开**（关窗口不算）。重开后 `$jlc-pcb-layout` 或直接说「帮我在嘉立创EDA里摆一下这块板」即可命中。

## 自检

```bash
cd ~/.codex/skills/jlc-pcb-layout
python scripts/rules.py width --current 3 --copper 1oz
python scripts/plan.py assets/plan.example.json -o /tmp/p.json --report
python scripts/check.py examples/snapshot.demo.json --min-severity WARN
```

期望输出：

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

第三条 **FAIL 1 是故意埋的**——`snapshot.demo.json` 是一块有典型毛病的示例板，用来演示检查器能抓到短路。修好的版本是 `examples/snapshot.demo.fixed.json`（`FAIL 0 / WARN 6`）。

## 规则覆盖

`check.py` 输出 33 个规则号，分四组：

| 组 | 规则 | 管什么 |
|---|---|---|
| 布局 `FAB-* / TY-* / EM-*` | 出框、板边净空、courtyard 间距、BGA 禁布、安装孔禁布、去耦距离、晶振距离 | 板能不能做出来 |
| 摆放 `PL-*` | 极性朝向、IC 1 脚朝向、器件高度、高矮混放、电感正交（车规）、重心偏移、插拔净空、MARK 数量与跨距 | 摆得对不对 |
| 布线 `TY-6.5.6-*` | 悬空线头、直角/锐角、3W、异网短路、线宽一致性、过孔孔厚比 | 走线合不合规 |
| 图面整洁 `AEST-*` | 共线、阵列间距、0/90/180/270、0/45/90 角度栅格 | 齐不齐 |

**数据缺就降级为 `INFO` 明说「未判定」，不会假装通过。** 这是刻意的设计——`INFO` 的意思就是「这项没判，交付时要写进未解决项」。

摆件顺序在 `SKILL.md` 里是硬约束（P0 机械锁定 → P1 功率热源 → P2 核心 IC → P3 就近器件 → P4 敏感小信号 → P5 其余阻容 → P6 测试点/MARK/丝印），前一档未锁定不得进入下一档。

## 目录结构

```
jlc-pcb-layout-skill/
├── INSTALL.md              给 Agent 看的安装指令
├── install.sh / install.ps1
├── jlc-pcb-layout/         ← 技能本体
│   ├── SKILL.md
│   ├── references/         6 份规范转写 + 整洁度判据 + 执行手册
│   ├── scripts/            rules.py / plan.py / check.py
│   ├── assets/             constraints.json（全部数值的唯一真源）
│   └── examples/           4 个快照样例 + 完整走查
└── test-cases/             4 个真实 KiCad 原理图（未启用的素材，见下）
```

阈值全部集中在 `assets/constraints.json`，觉得哪个太严太松直接改数字，不用改代码。

## test-cases

`test-cases/` 里是 4 个真实开源原理图（CH340G USB-TTL、TinyUSB-CH340X、STM32F103C8），跨 KiCad 4/6/8 三代格式，器件数 20 / 27 / 105。

**它们目前不被任何脚本读取**，是给「原理图 → 布局骨架」功能预留的素材。中间缺的是 `net2plan`：`.kicad_sch`/`.net` → 器件 + 网络表 → 按连通性聚类成 blocks → 喂给现成的 `plan.py`。

## 依赖

| 层 | 依赖 | 说明 |
|---|---|---|
| 规范层 | 无 | 纯 Markdown |
| 计算/检查层 | Python 3.8+，**纯标准库** | 不用 pip install |
| 落笔层 | `easyeda` CLI + daemon | 来自 [easyeda-agent](https://github.com/zhoushoujianwork/easyeda-agent)，本仓库不含 |

不装 CLI 也能用前两层——照样算线宽、出坐标计划、跑合规检查，只是最后一步落笔得自己在画布上点。

## 数值来源

条款来自华为 PCBLayout 设计规范、爱默生 PCB 设计规范、华硕内部 PCB 设计规范、上海贝尔 PCB 设计规范、印刷电路板通用设计规范、PCB 器件建库规范，以及 IPC-7351B 与通用 DFM 口径。出处编号写在 `constraints.json` 的 `_source` 字段里，`check.py` 输出的规则号可直接回 `references/` 查原文。

**规范冲突时取严，与厂家能力表冲突时以厂家为准。** 投板前必须确认 PCB/SMT 厂家的实际能力——各家工艺窗口不一样，本仓库给的是保守起点，不是验收依据。

## License

MIT，见 [LICENSE](LICENSE)。
