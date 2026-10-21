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

### 禁用项：自动布线 / 自动布局（不是第三条通道）

专业版确实提供 `eda.pcb_Document.autoRouting(props)`（v3.2.150 / v4.1.66 之后才有，老版本是 `undefined`），**但本 skill 禁止调用**——不探测、不试探、也不找"文件交换式自动布线"之类的替代路径。走线一律 `pcb_PrimitiveLine.create` / `pcb.line.create` 逐段来。

四条理由，都是现场能踩到的：

1. **没有 undo。** 一次调用改动整块板，走错了撤不回来，只能靠事先 `save` 的版本回退。
2. **无法逐段归因。** `check.py` 报 `TY-6.5.6-*` 的 3W 不足或 `AEST-ANGLE` 偏离 45° 时，你对着几千段线指不出是哪一步布错的，也就没法只重做那一段。
3. **900 秒硬上限。** 超过就截断返回，留下一块布了一半的板子——比没布更难判断。
4. **它不认你的判据。** 差分对、等长组、回流路径、去耦优先、功率环路面积，这些 `check.py` 要查的东西自动布线不保证，冲突时它也不会停下来问你。

**唯一例外**：用户明确说"就用自动布线，别逐段画"。此时先说明上面四条，要一句明确确认，跑完把结果记进交付物的「未按规范验证项」，并照常跑 `check.py`——布通了不等于合规。

---

## 2. 落笔顺序（不要乱）

```
0. easyeda health                    // 确认窗口连着（system.health）
1. pcb.layers.list                   // 拿真实层 id + copperLayerCount
2. pcb.outline.set                   // 板框（Board outline 是一切相对定位的基准）
3. pcb.import_changes                // 器件上板（从原理图导入）
4. 咬住锚点：连接器/安装孔/定位孔 → lock
5. 按计划摆块：power → mcu → io → ...（每块摆完 components.list 回读）
6. 记布线基线：line-list 导出 → journal.py baseline（per-net primitiveId + 几何）
7. 关键信号优先布线：时钟 → 差分 → 高速 → 模拟小信号（每次 create/delete 后 record 一笔）
8. 电源与地：粗线 + 平面 + 过孔缝合
9. 其余信号线
10. pcb.pour.create / pour-fit + pour.rebuild
11. 丝印整理（不压焊盘、极性可辨）
12. journal.py verify + pcb.drc.check + check.py + pcb.report
13. save（显式保存，别只靠自动保存）
```

**为什么要这个顺序**：板框决定边距，导入决定器件存在，锚点决定剩多少空间，关键信号决定层分配。任何一步提前都会导致返工——而 EDA 没有 undo。

**第 6 步不能省。** 布完再补基线已经晚了：那时分不清哪段铜是本流程画的、哪段是上一轮留下的。基线和账本是唯一能证明"我只动了这些"的东西，也是 `RT-JRN-05/06` 的输入。详见 `routing-rules.md` §9.5。

---

## 3. 已知的坑（都是真机踩出来的）

| 坑 | 现象 | 对策 |
|---|---|---|
| `create()` 太宽容 | 参数错/层错时**不报错、返回 undefined** | 每次创建后检查返回值非空，并回读一次 |
| 走线自动分段 | 线与已有图元相交处被原生切割（调 200 次可能得到 202 段） | 校验几何时别只比段数；`journal.py verify` 按 primitiveId 集合比，切段会被自动豁免 |
| 改线叠画 | 想改走向时直接画了新线、忘了删旧段；两段错开一个线宽时几何判据一句都不报 | **先 `pcb.line.delete` 再画**（不许 `line.modify` 换 net）；每步 `journal.py record` 记账，收尾 `RT-JRN-05` 会指名哪几个 id 没删 |
| 板框不能用 `Line` | 板框层（11）必须用 Polyline/多边形 | 用 `pcb_PrimitivePolyline` 或 `pcb.outline.set` |
| 覆铜空网 | `net` 传空串会报"参数不正确"，铺出来是死铜 | 必须传已有网络名（GND / VDD） |
| 内层 id 猜错 | 以为 3 是内层，结果画到丝印层 | 一律从 `pcb.layers.list` 读 |
| 停用层被静默忽略 | 往未启用的层 `create` 图元，不报错但看不见 | 先 `pcb.layers.list` 看 `layerStatus`，只往 `!== NOT_USED` 的层写 |
| 无 undo | 删除/移动不可撤销 | 每阶段显式 `save`，破坏性操作前先问用户 |
| 内电层顺序 | 先翻内电层类型再铺铜会掉到 L1 且 netless | 先在信号层铺铜 → 再翻 PLANE 类型 → 再 `pour rebuild` |
| 截图滞后 | 后台窗口截图可能是旧的或全白 | 以数据校验为准（`components.list` / `drc.check`），不要把截图当验证 |
| 位号重名 | 同一器件在计划里出现两次 | `plan.py` 已去重，手工改计划时注意 |

---

## 4. 验证四件套

1. **几何**：`pcb.components.list --include-bbox` 回读坐标，与计划文件逐条 diff（允许 ±1mil 网格误差）。
2. **规则**：`python check.py <snapshot>` 跑规范库；`pcb.drc.check` 跑 EDA 原生 DRC。两者都要过——规范库管"规范级"问题（去耦距离、边距带、朝向、3W、直角），DRC 管"电气级"问题（间距、短路、未布通）。
3. **布线质量**：`pcb.report` 看每网络长度、差分 skew、等长组 spread。
4. **画布归属与叠画**：布线前 `line-list` 存基线 → `journal.py baseline`；每步 `record`；布完 `journal.py verify`。按 primitiveId 比，不按段数（EDA 会在交点自动切段）。`RT-JRN-05` FAIL = 声明删的旧段还在，`RT-JRN-06` WARN = 有增删没记账。对不上且解释不通就是有非本流程的走线，先停下问，别在别人的结果上继续改。

```bash
easyeda pcb components-list --include-bbox --include-pads --json > comps.json   # 参数名以 easyeda actions 为准
easyeda pcb line-list --json > lines.json
easyeda pcb via-list  --json > vias.json
easyeda pcb outline-get --json > outline.json
python scripts/snap.py --components comps.json --lines lines.json \
    --vias vias.json --outline outline.json -o snapshot.json
python scripts/journal.py verify --journal journal.json --final lines.json
python check.py snapshot.json --min-severity WARN --journal journal.json
```

> **三份必须分开导出。** `components-list` 的输出里**没有走线**，`line-list` 里没有器件。早期这一段的写法是
> `"traces": comps.get("traces", [])` —— 从 components-list 的响应里取走线，永远拿到空列表，
> `check_traces()` 整段被跳过，3W/直角/悬空/线宽四项从来没跑过，输出里只剩一条 `FAB-DATA` UNKNOWN。
> 现在由 `snap.py` 合并；缺 `--lines` 时它**退出码 2 并且不生成快照**，不再产出一个"看起来正常"的空文件。
>
> 字段名以 `easyeda actions` / 实际输出为准。`snap.py` 只做常见别名归一化（`startX/startY`、`x1/y1`、`from:{x,y}`；
> `lineWidth/width`；`width/height` 或 `bbox`），对不上会指名报错，不猜。

---

## 5. 什么时候该停手问人

- 需要删器件、清空布线、改板框；
- 计划里有块"放不下"（`overflow_blocks` 非空）；
- 涉及安规间距（>60V）而你推不出电位；
- 画布上出现不是本流程创建的走线，或 `journal.py verify` 报出解释不通的增删、段数暴涨成片连通——疑似被自动布线/自动布局动过——留着还是清掉，要人决定；
- 用户给的坐标与结构图矛盾。

这五种情况**停下来问**，比"先画了再说"便宜得多。

---

## 6. 原理图落笔（`sch_*` API）

前面五节讲的是 PCB。原理图是另一套 API、另一套坐标系、另一批坑。**不要拿 PCB 的经验套**。

### 6.1 数据模型

| 项 | PCB | 原理图 |
|---|---|---|
| 坐标单位 | 1 mil | **10 mil**（0.01 inch） |
| 坐标系 | 原点左下，y 向上 | 原点左下，y 向上 |
| A4 图幅 | — | 1170 × 825（10mil 单位） |
| 建图元 | `pcb_Primitive*` | `sch_Primitive*` |

按 10mil 算：A4 图纸可用区域约 110×78 个栅格，一张 A4 放 40~50 个器件就该分页了。

### 6.2 用到的模块

| 模块 | 用途 |
|---|---|
| `eda.lib_LibrariesList.getSystemLibraryUuid()` | 取系统库 UUID（放器件前必须） |
| `eda.lib_Device.getByLcscIds([...])` / `.search(kw)` | 按料号或关键字搜器件 |
| `eda.sch_PrimitiveComponent.create()` | 放器件 |
| `eda.sch_PrimitiveComponent.get()` / `.modify()` | 读/写位号与属性 |
| `eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId()` | **读回引脚真实坐标** |
| `eda.sch_PrimitiveComponent.createNetFlag()` | 放电源/地符号 |
| `eda.sch_PrimitiveWire.create()` | 连线（可带网络名） |
| `eda.sch_Document.save()` | 保存 |
| `eda.sch_Drc.check()` | 原理图 DRC |
| `eda.sch_ManufactureData.getNetlistFile()` | 导出网表（用于比对） |
| `eda.sch_ManufactureData.getBomFile()` | 导出 BOM |
| `eda.dmt_Project.getCurrentProjectInfo()` | 取 page UUID |
| `eda.dmt_EditorControl.openDocument(page)` | 打开图页（操作前必须） |

### 6.3 已验证的签名

```js
// 放器件：8 个参数，顺序不能错
await eda.sch_PrimitiveComponent.create(
  lib,          // { libraryType, libraryUuid, uuid } 或搜索结果项
  x, y,         // 10mil 单位
  undefined,    // subPartName：多子部件器件才填
  0,            // rotation（逆时针为正）
  false,        // mirror
  true,         // addIntoBom
  true          // addIntoPcb —— 漏传或传 false，同步 PCB 时是 0 个器件
);

// 位号：create → 读快照 → 一次写回（不要分两次 modify）
const snap = comp.getState_OtherProperty();
await eda.sch_PrimitiveComponent.modify(id, { designator: 'U1', otherProperty: snap });

// 引脚坐标：只能读回，不能推算
const pins = await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(id);
// 每项：pinNumber / x / y（可能要以 getState_*() 取，见 6.5）

// 电源符号
await eda.sch_PrimitiveComponent.createNetFlag('Power', '3V3', x, y, 0, false);
// 类型：'Power' | 'Ground' | 'AnalogGround' | 'ProtectGround'

// 连线：坐标组 + 网络名
await eda.sch_PrimitiveWire.create(
  [[x, y, x + 20, y]],   // 每段 4 个数：x1,y1,x2,y2
  '3V3',                 // 网络名（不传则按落点推断）
  null,                  // 颜色
  2,                     // 线宽 1~10
  null                   // 线型
);

// DRC：save 之后等 8~10 秒
await eda.sch_Document.save();
await new Promise(r => setTimeout(r, 9000));
const violations = await eda.sch_Drc.check(true, false, true);  // 明细（分组树）
const passed     = await eda.sch_Drc.check(true, false, false); // 布尔

// 网表导出（比对用）
// 'Protel2' 是立创EDA 内置的网表导出格式名，不是外部工具；无需装任何第三方软件
const f = await eda.sch_ManufactureData.getNetlistFile('BOARD_NET', 'Protel2');
const txt = await f.text();   // 必须 await
```

### 6.4 落笔顺序

```
0. getCurrentProjectInfo()          // 拿 page UUID
1. openDocument(page) + 等 2500ms   // 不打开图页，create 会静默失败
2. 逐器件：搜索 → create → modify(位号)
3. getAllPinsByPrimitiveId() 读回坐标
4. 逐引脚拉带 net 的短线（同名即连通）
5. 电源网络放 createNetFlag
6. sch_Document.save()
7. 等 8~10s → sch_Drc.check()
8. getNetlistFile() 导出 → 与设计网表比对
```

**为什么第 2 步要一个一个来**：`create()` 参数错时不报错、返回 `undefined`。必须每放一个就检查返回值非空。批量 `invoke` 时每批 20~25 个，超时余量留足（默认 60s，大工程要调 `--timeout`）。

### 6.5 版本差异（这一条能省你一晚上）

| 项 | v3.2.x | v3.3+ |
|---|---|---|
| 库模块名 | `eda.lib_Device` | `eda.LIB_Device` |
| `create()` | **同步**，不能 await | 异步，返回 Promise |

**在 v3.2.x 里给 `create()` 加 `await` 会静默失败**——不报错、不放器件、不进 catch。名字大小写不对也是 `undefined`。

生成的落笔脚本 (`schem.py playbook`) 用这个兼容写法绕过去：

```js
const LD = eda.lib_Device || eda.LIB_Device;
function callSyncOrAsync(fn, args) {
  const r = fn.apply(null, args);                    // 先同步调用
  return (r && typeof r.then === 'function') ? r : Promise.resolve(r);  // 是 Promise 才 await
}
```

属性读取同理，用 getter 和裸字段都试：

```js
function pick(o, k) {
  const g = o['getState_' + k];
  return (typeof g === 'function') ? g.call(o) : o[k];
}
```

**动手前先探测**：在「运行脚本」里跑一段 `Object.keys(eda).filter(k => k.startsWith('sch_'))` 和 `typeof eda.sch_PrimitiveComponent.create(...)`，比照文档猜快得多。

### 6.6 连线为什么可以"每个引脚拉一段短线"

`sch_PrimitiveWire.create(line, net)` 的第二个参数是**网络名**。原理图的连通性判定基于网络名，不是几何接触：

- 同一网络名 = 同一网络
- 相邻段会自动合并成同一个 WIRE 图元组（返回的 id 重复是正常的，不是 bug）
- 段与段必须首尾相接，每段必须**水平或垂直**（不能斜线）

所以「每个引脚往外拉 20 单位的短线 + 同名 net」就能建立逻辑连通，而不用先算好整条路径。代价是图上看起来像一堆触须，**走向要人工整理**。这是刻意的取舍：逻辑连通可验证，走向好看不可验证。

### 6.7 坑表（原理图专属）

| 坑 | 现象 | 对策 |
|---|---|---|
| `addIntoPcb` 没传 `true` | 原理图有器件，同步 PCB 后 0 个器件 | create 第 8 个参数固定 `true` |
| 旧版加 `await` | 不报错、不放器件 | 用 6.5 的 `callSyncOrAsync` |
| 模块名大小写 | `LD` 为 `undefined`，每次都 MISS | `eda.lib_Device \|\| eda.LIB_Device` |
| 自己推算引脚坐标 | 线画到别处，DRC 报"未连接" | 一律 `getAllPinsByPrimitiveId` 读回 |
| 没 `openDocument` | create 静默失败 | 先打开图页并等 2500ms |
| DRC 立刻跑 | 报一堆假错或 0 违规 | `save()` 后等 8~10 秒 |
| 位号分两次 modify | 第二次覆盖掉第一次 | 一次 modify 写 `designator` + `otherProperty` |
| 网络名拼错 | 两边都"连着"但网络不同 | 网络名从 netlist 取，不要手敲 |
| 斜线 | create 失败或图形异常 | 正交折线，每段水平/垂直 |
| 中文位号/网络名 | 部分版本写入异常 | 位号与网络名用 ASCII |

### 6.8 CLI 调用方式

```bash
lceda-pro open                       # 开 session，返回 sessionId
lceda-pro invoke --session <id> --ext-uuid eda --code "$(cat apply.js)"
```

- `--ext-uuid eda` 必须带，否则是"独立脚本权限"，拿不到完整 API。
- 传入的代码视为**异步函数体**：用 `return` 返回结果，可以直接 `await`。
- 大工程分批 invoke，`--timeout` 可调大到 1800000ms。

### 6.9 验证（原理图三件套）

1. **结构**：`schem.py erc` 过 FAIL=0（这是设计层）
2. **EDA 原生**：`sch_Drc.check(true, false, true)` 返回空数组（这是画图层）
3. **一致性**：`getNetlistFile` 导出的网表 vs `netlist.json` —— **逐网络比对**。这一步是唯一能证明"画出来的就是设计的"的手段，跳过它就等于假设落笔没出错。
