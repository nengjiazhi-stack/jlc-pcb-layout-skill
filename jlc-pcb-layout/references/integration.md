# 与执行层对接（easyeda-agent-open）

本 skill 是**判据层**：算什么、摆在哪、为什么。它不含执行引擎，不会自己动手画画布。

真正往嘉立创EDA 里写图元的是另一套东西——社区项目 [easyeda-agent](https://github.com/zhoushoujianwork/easyeda-agent) 及其维护分支 [easyeda-agent-open](https://github.com/yitengchun833-gif/easyeda-agent-open)。本 skill 的 `references/eda-execution.md` 描述的就是这套 CLI 的用法。

## 一条已经验证过的事实

`eda-execution.md` 引用的 7 个动作 id，在 easyeda-agent-open 的 Go 源码里逐个查过，全部存在：

```
pcb.outline.set  pcb.components.list  pcb.line.create  pcb.via.create
pcb.pour.create  pcb.import_changes  pcb.component.modify
```

所以两份文档写的是同一套接口，**不需要改任何代码就能对接**。这是判断「能不能合」的硬依据，不是推测。

## 分工

| | 本 skill（判据层） | easyeda-agent-open（执行层） |
|---|---|---|
| 回答 | 要接什么、多宽、多远、摆哪、为什么 | 写进去了吗、写对了没、失败在哪一步 |
| 形态 | Python 标准库脚本 + 条款文档 | Go daemon + `.eext` 扩展 + MCP + CLI |
| 体量 | 约 40 文件 / 1MB，零运行时依赖 | 约 1900 文件 / 30MB，要装扩展和 Node |
| 输入 | 客户一句话需求 | 必须已经确定的设计 |
| 输出 | 网表、ERC、坐标计划、检查报告 | 画布上的真实图元 + 执行回执 |

**关键差别**：它的输入必须**已经是确定的设计**。它不会从「做个 4 路继电器板」推导出网表，`semanticTarget` 要调用方提供。这一步正好是本 skill 的 `schem.py spec` / `netlist` 干的活。

## 三个对接点

### 1. 落笔

`scripts/schem.py playbook` 生成的 JS 走两条路都能用：

```bash
# 路线 A：直接粘进 EDA 的「高级 → 运行脚本」
# 路线 B：走 CLI
lceda-pro open
lceda-pro invoke --session <id> --ext-uuid eda --code "$(cat apply.js)"
```

`--ext-uuid eda` 必须带，否则是独立脚本权限，拿不到完整 API。

也可以改用它的 `sch place` / `sch wire` / `sch block-apply` 逐条做——它有回执和续接，比一把梭的脚本更抗失败。取舍很清楚：**脚本快但失败要重来，CLI 慢但每步可核。**

### 2. 验证

本 skill 的 `check.py` 管**设计对不对**，它的 `sch gate` / `pcb layout-lint` 管**画得对不对**。两者不重叠：

```bash
# 设计层（本 skill）
python scripts/schem.py erc nl.json                      # 23 条电气规则
python scripts/check.py snap.json --netlist nl.json --gate   # 板级 + 块级 + ERC 汇总

# 画图层（执行层，需先装它）
easyeda sch gate --project <名> --doc <页>     # layout-lint → check → bridge-check → drc
easyeda pcb layout-lint --project <名> --doc <页>
```

`check.py --gate` 给的是 `BLOCKED / INCOMPLETE / REVIEW / PASS` 四态，跟它的 `verdict=blocked` 可以对上：

- `BLOCKED` ≈ 它的 `blocked`：有硬错误，别往下走
- `INCOMPLETE`：**它有、本 skill 原来没有的状态** —— 没有 FAIL，但有检查没跑成

### 3. 回执

落笔之后，把 EDA 的真实结果贴回交付物，别只报「脚本跑完了」：

| 证据 | 从哪拿 | 证明什么 |
|---|---|---|
| `sch_Drc.check(true,false,true)` 明细 | 本 skill 的 playbook 会自动调 | 平台认为有违规 |
| `sch netlist` 导出 | 执行层 CLI | 画出来的网表 vs 设计网表，逐网络比对 |
| `layout-lint` 的 ERROR | 执行层 CLI | 真实渲染 bbox 重叠/越界 |
| 画布截图 | `sch export-image` | 视觉上是否可读 |

**网表逐网络比对是唯一能证明「画出来的就是设计的」的手段。** 跳过它就等于假设落笔没出错。

## 不要做什么

- **不要把它的 30MB 塞进本 skill。** 本 skill 的价值是「零依赖、条款可回查」，混进执行引擎后就变成一个又大又难审计的东西。
- **不要用它的设计数值当判据。** 它的设计规则手册明确写着「线宽、间距、过孔来自当前工程和现行制造能力，不把旧范例数值当永久默认」——这是它对的地方，但方向与本 skill 相反。本 skill 的数值唯一真源是 `assets/constraints.json`，要改就改那里。
- **不要在没装 CLI 时假装落笔了。** 只出计划和检查报告，明确说「未落笔」。虚构 DRC 结果比不画危险得多。
- **不要把它的执行回执当成设计正确。** 它自己写得很清楚：`saved:true` 只证明保存动作返回了，不证明电路对。

## 本 skill 从它这里吸收了三点

不是照抄功能，是吸收判断方式：

1. **`UNKNOWN` 三态。** 原 `check.py` 只有 FAIL/WARN/INFO，缺数据时打一条 INFO「检查跳过」，结果 `FAIL 0` 看起来像通过。现在独立出 UNKNOWN，`--gate` 判定 `INCOMPLETE`，退出码 2。缺 traces、缺高度、缺丝印，都会明确告诉你**这几项没查**。
2. **块级布局约束。** 它的每个块带 `pcb_layout: [{rule, target, constraint, value, severity}]`，带 `must/should` 和数值。这套格式被吸收进 `assets/chip-variants.json`，由 `check.py` 的 `BLK-*` 规则消费：能算的算（`BLK-DECAP`、`BLK-ESD`），算不了的汇总报 `BLK-MANUAL`。
3. **「明确说不行」的结构化表达。** 它的 `blocked_by` 思路——宁可拒绝也不生成错的东西——被用在芯片实例上：ESP32-S3 模组与 `mcu_min` 块不兼容，就直接拒绝并说明原因，而不是勉强对上几个脚。

## 已知没有吸收的

- **布局求解器**（它的 `compose` / `apply` / `zone` / 刚体平移）：需要执行引擎，本 skill 的 `plan.py` 是瓦片 + 货架排布，做不到它那种「整组刚体移动 + 自检回滚」。
- **执行回执与续接**（哈希绑定、`unknown` 态、批次暂停恢复）：daemon 的活，脚本层做不了。
- **视觉审阅**：需要截图能力。
- **芯片级块库的规模**：它有 37 个块，本 skill 只收了 5 颗芯片，且只有 1 颗「引脚完整 + 有料号」。这是刻意的——收录标准见 `chip-variants.md`，没核对过的宁可空着。

想补齐上面这些，正路是**装上执行层**，而不是把它的代码抄进来。
