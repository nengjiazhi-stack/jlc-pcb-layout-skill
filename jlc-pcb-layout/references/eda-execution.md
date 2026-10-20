# 在嘉立创EDA里真正"落笔"（Execution）

布局计划算完了，坐标表有了，接下来是把它变成画布上的图元。这一节只讲**怎么落**、**顺序**和**会踩的坑**。

---

## 0. 数据模型（三个必须先记住的事实）

1. **PCB 数据单位 = 1 mil**（原理图是 10mil / 0.01inch）。y 轴**向上**，+y 在屏幕上是上方。
   （官方 `SYS_Unit` 类备注原文："PCB 数据单位跨度等效为 mil"）
2. **无左右镜像，只有翻层**。器件要么在 TOP，要么在 BOTTOM，翻面改 layer，不存在 mirror。
3. **层是一等维度**，每条走线、每个焊盘、每个覆铜都绑定层 id。常用层 id：

| id | 层 | id | 层 |
|---|---|---|---|
| 1 | TOP（顶层铜） | 2 | BOTTOM（底层铜） |
| 3 | TOP_SILKSCREEN | 4 | BOTTOM_SILKSCREEN |
| 5 | TOP_SOLDER_MASK | 6 | BOTTOM_SOLDER_MASK |
| 7 | TOP_PASTE_MASK | 8 | BOTTOM_PASTE_MASK |
| 9 | TOP_ASSEMBLY | 10 | BOTTOM_ASSEMBLY |
| **11** | **BOARD_OUTLINE（板框）** | 12 | MULTI |
| 13 | DOCUMENT | 14 | MECHANICAL |
| 15…44 | INNER_1 … INNER_30（内层铜） | 47 | HOLE（孔） |
| 48 | COMPONENT_SHAPE（器件外形/courtyard） | 49 | COMPONENT_MARKING |
| 56 | DRILL_DRAWING | 57 | RATLINE |

> **坑**：内层铜从 **15** 开始，不是 3。id=3 是顶层丝印。写内层走线前必须 `pcb layers list` 读实际启用层，猜 id 会画到丝印层上。

---

## 1. 两条通道，优先用 A

### 通道 A：`easyeda` CLI（推荐）

已经封装成类型化动作，带校验、回读和审计。先 `easyeda health` 确认窗口连着，再 `easyeda actions` 拿权威参数表（**报参数以它的输出为准**，本文只给动作名和语义）。

| 阶段 | 动作 | 说明 |
|---|---|---|
| 建板框 | `pcb.outline.set` / `pcb.outline.get` | 传闭合多边形 `points`（`[[x,y],…]`，mil，y-up）；曲线用折线逼近（48–120 段） |
| 从原理图导入 | `pcb.import_changes` | 器件第一次出现在板上的唯一正路；浮空 PCB 会返回 `imported:false` |
| 读现状 | `pcb.components.list --include-bbox --include-pads` / `pcb.line.list` / `pcb.via.list` | 拿 bbox、每 pad 的 net、已布走线与过孔，是后续一切检查的原料 |
| 读层/网 | `pcb.layers.list` / `pcb.nets.list` | 拿 `copperLayerCount` 决定去耦阈值；拿真实层 id |
| 摆件 | `pcb.component.modify` | 改 x/y、旋转、翻层、锁定、位号 |
| 组移动 | `pcb.components.move` | 相对平移一组 |
| 对齐/分布/吸栅 | `pcb.align` / `pcb.distribute` / `pcb.grid_snap` | 收尾美化用，不得覆盖功能位置 |
| 粗排种子 | `pcb.components.arrange mode=cluster` | 按共享网络聚簇后网格装箱，只是种子 |
| 走线 | `pcb.line.create` | 一段一条：`({net, layer, startX, startY, endX, endY, lineWidth})` |
| 过孔 | `pcb.via.create` | `{net, x, y, holeDiameter, diameter}` |
| 拆线 | `pcb.route.rip_up --net <name>` | 可靠删除走线+过孔，只作用于铜层，不碰板框与丝印 |
| 铺铜 | `pcb.pour.create` / `pcb.pour.rebuild` | 传 `points` + `net`（**net 不能空**）；改完布局必须重铺 |
| 整板铺铜 | `pcb pour-fit --net GND --layer <id> --inset 20` | 按板框自动内缩铺矩形铜 |
| 缝合孔 | `pcb via-stitch --rect "x0,y0,x1,y1" --pitch 100 --net GND` | 地缝合/散热过孔阵列 |
| 禁布区 | `pcb.region.create --rule no-components` | 天线净空、机械避让 |
| DRC | `pcb.drc.check` / `pcb.drc.rules` | 跑检查 / 只读当前规则值 |
| 布线质量 | `pcb.report` | 每网络长度、差分 P/N 长度与 skew、等长组 spread |
| 批量 | `easyeda apply steps.json` | 多步操作用 playbook，别写 shell 胶水；先 `--dry-run` |

**playbook 最小形态**（`plan.py --emit-steps` 就是这个格式）：

```json
{
  "meta": {"project": "my-board"},
  "steps": [
    {"action": "pcb.outline.set", "payload": {"points": [[0,0],[2362,0],[2362,1772],[0,1772]]}},
    {"action": "pcb.component.modify", "payload": {"designator": "U1", "x": 1300, "y": 1000, "rotation": 0}},
    {"action": "pcb.line.create", "payload": {"net": "VDD", "layer": 1, "startX": 1200, "startY": 1000, "endX": 1100, "endY": 1000, "lineWidth": 20}},
    {"action": "pcb.drc.check"}
  ]
}
```

### 通道 B：`eda.pcb_*` JS（bridge / 扩展内运行）

装了 `easyeda-api` 的 bridge，或自建扩展时，可直接调底层 API。已核实的签名：

```js
// 走线：net, layer, x1,y1,x2,y2, lineWidth?, lock?
await eda.pcb_PrimitiveLine.create('VDD', 1, 1200, 1000, 1100, 1000, 20, false);

// 折线（板框必须用这个，Line 不支持 BOARD_OUTLINE）
const poly = eda.pcb_MathPolygon.createPolygon([[0,0],[2362,0],[2362,1772],[0,1772],[0,0]]);
await eda.pcb_PrimitivePolyline.create('', 11, poly, 10, false);

// 矩形多边形快捷写法：['R', x, y, w, h, rot, 0]
const r = eda.pcb_MathPolygon.createPolygon(['R', 100, 100, 500, 300, 0, 0]);

// 过孔：net, x, y, holeDiameter, diameter, viaType?, blindName?, mask?, lock?
await eda.pcb_PrimitiveVia.create('GND', 800, 900, 12, 25);

// 覆铜：net 不能是空串；fill: 'solid' | 'grid' | 'grid45'
await eda.pcb_PrimitivePour.create('GND', 1, r, 'solid', false, 'GND_TOP', 5, 10, false);

// 放器件：先按立创编号查库，再补 libraryUuid
const devs = await eda.lib_Device.searchByProperties({ supplierId: 'C1523' }, undefined, undefined, undefined, 5, 1);
const sysLib = await eda.lib_LibrariesList.getSystemLibraryUuid();
const comp = await eda.pcb_PrimitiveComponent.create({ libraryUuid: sysLib, uuid: devs[0].uuid }, 1, 12000, 8000, 0, false);

// 读焊盘（走线端点要用的坐标）
const pads = eda.pcb_PrimitiveComponent.getAllPinsByPrimitiveId(comp.getState_PrimitiveId());
pads.forEach(p => console.log(p.getState_PadNumber(), p.getState_Net(), p.getState_X(), p.getState_Y()));

// 网表 / 网络长度 / DRC / 规则
const nets = eda.pcb_Net.getAllNetsName();
const rules = eda.pcb_Drc.getNetRules();
await eda.pcb_Drc.check(false, false, true);
const z = await eda.pcb_Drc.createNetClass('PWR', ['VDD', '3V3', 'VBUS'], '#ff0000');

// 单位换算（写代码时别手算）
eda.sys_Unit.mmToMil(0.2, 1);   // → 7.9
```

**关于自动布线**：`eda.pcb_Document.autoRouting(props)` 在 EDA v3.2.150 / v4.1.66 之后才有，且内部有 900 秒硬上限；老版本该函数为 `undefined`。**先探测** `typeof eda.pcb_Document?.autoRouting === 'function'`，没有就走逐段布线或文件交换式自动布线。自动布线的结果**必须逐网络复核**（3W、45°、回流路径），不能直接交付。

---

## 2. 落笔顺序（不要乱）

```
0. easyeda health                    // 确认窗口连着（system.health）
1. pcb.layers.list                   // 拿真实层 id + copperLayerCount
2. pcb.outline.set                   // 板框（Board outline 是一切相对定位的基准）
3. pcb.import_changes                // 器件上板（从原理图导入）
4. 咬住锚点：连接器/安装孔/定位孔 → lock
5. 按计划摆块：power → mcu → io → ...（每块摆完 components.list 回读）
6. 关键信号优先布线：时钟 → 差分 → 高速 → 模拟小信号
7. 电源与地：粗线 + 平面 + 过孔缝合
8. 其余信号线
9. pcb.pour.create / pour-fit + pour.rebuild
10. 丝印整理（不压焊盘、极性可辨）
11. pcb.drc.check + check.py + pcb.report
12. save（显式保存，别只靠自动保存）
```

**为什么要这个顺序**：板框决定边距，导入决定器件存在，锚点决定剩多少空间，关键信号决定层分配。任何一步提前都会导致返工——而 EDA 没有 undo。

---

## 3. 已知的坑（都是真机踩出来的）

| 坑 | 现象 | 对策 |
|---|---|---|
| `create()` 太宽容 | 参数错/层错时**不报错、返回 undefined** | 每次创建后检查返回值非空，并回读一次 |
| 走线自动分段 | 线与已有图元相交处被原生切割（调 200 次可能得到 202 段） | 校验几何时别只比段数；用网络长度或连通的 primitiveId 集合判断 |
| 板框不能用 `Line` | 板框层（11）必须用 Polyline/多边形 | 用 `pcb_PrimitivePolyline` 或 `pcb.outline.set` |
| 覆铜空网 | `net` 传空串会报"参数不正确"，铺出来是死铜 | 必须传已有网络名（GND / VDD） |
| 内层 id 猜错 | 以为 3 是内层，结果画到丝印层 | 一律从 `pcb.layers.list` 读 |
| 停用层被静默忽略 | 自动布线 `props.layers` 传了没启用的层 | 传 `layerStatus !== NOT_USED` 的层 |
| 无 undo | 删除/移动不可撤销 | 每阶段显式 `save`，破坏性操作前先问用户 |
| 内电层顺序 | 先翻内电层类型再铺铜会掉到 L1 且 netless | 先在信号层铺铜 → 再翻 PLANE 类型 → 再 `pour rebuild` |
| 截图滞后 | 后台窗口截图可能是旧的或全白 | 以数据校验为准（`components.list` / `drc.check`），不要把截图当验证 |
| 位号重名 | 同一器件在计划里出现两次 | `plan.py` 已去重，手工改计划时注意 |

---

## 4. 验证三件套

1. **几何**：`pcb.components.list --include-bbox` 回读坐标，与计划文件逐条 diff（允许 ±1mil 网格误差）。
2. **规则**：`python check.py <snapshot>` 跑规范库；`pcb.drc.check` 跑 EDA 原生 DRC。两者都要过——规范库管"规范级"问题（去耦距离、边距带、朝向、3W、直角），DRC 管"电气级"问题（间距、短路、未布通）。
3. **布线质量**：`pcb.report` 看每网络长度、差分 skew、等长组 spread。

```bash
easyeda pcb components-list --include-bbox --json > comps.json     # 参数名以 easyeda actions 为准
python - <<'PY'   # 转成 check.py 要的 snapshot
import json
comps = json.load(open('comps.json'))
snap = {"board": {"width_mil": 2362, "height_mil": 1772, "copper_layers": 4,
                  "thickness_mm": 1.6, "wave_solder": False},
        "components": comps["components"],
        "traces": comps.get("traces", []),
        "vias": comps.get("vias", [])}
json.dump(snap, open('snapshot.json','w'), ensure_ascii=False, indent=2)
PY
python check.py snapshot.json --min-severity WARN
```

> 字段名以 `easyeda actions` / 实际输出为准。如果 CLI 的输出结构不一致，**不要硬凑**——直接把 `components` 数组里每项的 `designator/layer/x/y/rotation/w/h/pads` 映射过来即可，`check.py` 只看这几个键。

---

## 5. 什么时候该停手问人

- 需要删器件、清空布线、改板框；
- 计划里有块"放不下"（`overflow_blocks` 非空）；
- 涉及安规间距（>60V）而你推不出电位；
- 自动布线的结果与关键信号规则冲突（要人决定牺牲哪个）；
- 用户给的坐标与结构图矛盾。

这五种情况**停下来问**，比"先画了再说"便宜得多。
