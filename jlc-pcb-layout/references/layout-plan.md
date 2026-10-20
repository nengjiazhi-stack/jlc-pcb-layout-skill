# 布局计划规格（Layout Plan Spec）

计划文件是**坐标的唯一真源**。画布上的位置必须能从它推出来；手工微调后要回写，否则下次重排会把改动冲掉。

由 `scripts/plan.py` 生成和消费，`scripts/check.py` 可直接读它做合规检查（缺 pads 的检查项会降级为 INFO）。

---

## 1. 输入格式

```json
{
  "board": {"width_mm": 60, "height_mm": 45, "corner_radius_mm": 5},
  "options": {
    "grid_mil": 25,
    "edge_band_mil": 80,
    "gutter_mil": 60,
    "part_gap_mil": 40,
    "area_fill_factor": 1.8,
    "scan_step_mil": 100
  },
  "anchors": [
    {"designator": "J1", "x_mm": 6, "y_mm": 22.5, "w_mm": 9.0, "h_mm": 7.4,
     "rotation": 90, "layer": 1, "block": "anchor"}
  ],
  "blocks": [
    {
      "name": "mcu",
      "edge": "center",
      "priority": 2,
      "gutter_mil": 60,
      "parts": [
        {"designator": "U1", "w_mm": 5.0, "h_mm": 5.0, "rotation": 0, "layer": 1},
        {"designator": "Y1", "w_mm": 3.2, "h_mm": 2.5, "rotation": 0, "layer": 1}
      ]
    }
  ]
}
```

### 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `board.width_mm / height_mm` | ✓ | 板框尺寸。**用 mm 写**，脚本内部换算成 mil |
| `board.corner_radius_mm` | | 仅记录，不参与计算（圆角由板框点集决定） |
| `options.grid_mil` | | 网格，默认 25（SMD）/ 50（纯通孔） |
| `options.edge_band_mil` | | 板边净空带，默认 80（≈2mm）。要 5mm 轨道边就设 197 |
| `options.gutter_mil` | | 块与块之间/块与锚点之间的通道，默认 60 |
| `options.part_gap_mil` | | 块内器件间距，默认 40（≈1mm） |
| `options.area_fill_factor` | | 块面积估算系数，默认 1.8。器件密集就调小（1.3），要留布线通道就调大（2.5） |
| `options.scan_step_mil` | | 块位置的搜索步长，默认 100。块多而小板可调小到 50 |
| `anchors[].x_mm / y_mm` | ✓ | **器件中心**坐标，来自结构图 |
| `anchors[].w_mm / h_mm` | ✓ | **含禁布区的封装外框**（不是本体） |
| `anchors[].rotation` | | 0/90/180/270；90 会交换 w/h |
| `anchors[].layer` | | 1=TOP，2=BOTTOM |
| `blocks[].edge` | | `left/right/top/bottom/center`：只影响搜索顺序（靠哪条边先找位置），**不是硬约束** |
| `blocks[].priority` | | 数字小 = 先落位 = 优先占地。按 P0→P7 优先级给：电源块 1、主控 2、接口 3、其它 5 |
| `blocks[].parts[].designator` | ✓ | 必须与原理图位号一致 |
| `blocks[].parts[].w_mm / h_mm` | ✓ | **courtyard 尺寸**，不是本体尺寸 |

---

## 2. 输出格式

```json
{
  "meta": {
    "unit": "mil", "y_axis": "up", "origin": "板框左下角",
    "board_mil": [2362, 1772], "usable_mil": [80, 80, 2282, 1692],
    "grid_mil": 25, "edge_band_mil": 80, "gutter_mil": 60
  },
  "blocks": [
    {"block": "power", "priority": 1, "edge": "left",
     "rect_mil": [480, 80, 820, 425], "parts": 5, "used_h_mil": 305, "overflow": false}
  ],
  "components": [
    {"designator": "U1", "block": "mcu", "x": 1300, "y": 1000,
     "w": 200, "h": 200, "rotation": 0, "layer": 1, "fixed": false}
  ],
  "summary": {
    "count": 26, "footprint_area_mil2": 765625, "board_area_mil2": 4185008,
    "area_utilization_pct": 18.3,
    "overflow_blocks": [], "out_of_board": [], "anchors_clamped": []
  }
}
```

- `x / y` 是**器件中心**，单位 mil，原点在板框左下角，y 向上；
- `fixed: true` 表示这是锚点，落笔时**不要移动**，只做 `lock`；
- `overflow: true` 表示该块放不下，已被合并到全板区域——**必须人工裁决**，不能直接落笔；
- `out_of_board` 非空说明有器件超出板框，先修坐标。

---

## 3. 算法（知道它在干什么，才知道怎么调）

```
1. 板框 → 可用区 usable = 板框内缩 edge_band
2. 锚点先落位（夹回板框内），每颗按 gutter 膨胀成障碍矩形
3. 每个块估算尺寸：
      块面积 = Σ(器件外框面积) × area_fill_factor
      块宽   = sqrt(块面积 × 板宽高比)，且 ≥ 块内最宽器件
      块高   = 用同一套货架算法在"块宽"下试排得出
4. 块排序：priority 升序 → 面积降序
5. 逐块找位置：按 edge 偏好生成候选点（scan_step 步长），
      第一个"在可用区内 + 与所有障碍/已落块不撞（含 gutter）"的位置即采用
6. 块内排布：器件按"高→低、大→小"排序，货架式逐行排，行满换行
7. 全部吸附网格，去重，统计占用率
```

这是**确定性装箱**，不是最优化求解。它保证：不重叠、不越界、可复现。它不保证：信号路径最短、扇出最容易。后者靠"块顺序 + edge 偏好 + 关键器件手动指定锚点"来引导。

---

## 4. 调参手册

| 症状 | 调什么 |
|---|---|
| 块放不下 / `overflow` | 调小 `area_fill_factor`；或加板面积；或把块拆成两个 |
| 块挤在一起，没地方走线 | 调大 `gutter_mil`（80→120）、`part_gap_mil`（40→60） |
| 电源块没贴到输入连接器 | 给电源块 `edge: "left"` + `priority: 1`，或把连接器坐标做成锚点 |
| 主控不在板中心 | `edge: "center"`；仍不理想就直接把主控写进 `anchors` |
| 器件被推得太靠边 | 调大 `edge_band_mil` |
| 排得松，浪费面积 | 调小 `area_fill_factor` 到 1.3–1.5 |
| 想让某颗器件绝对固定 | 写进 `anchors`（它就不会被移动） |

---

## 5. 从计划到画布

```bash
# 1) 只看块占用，不写文件
python scripts/plan.py my_spec.json --report

# 2) 生成计划 + playbook
python scripts/plan.py my_spec.json -o my_plan.json --emit-steps my_steps.json --project my-board

# 3) 预检 playbook
easyeda apply my_steps.json --dry-run

# 4) 执行
easyeda apply my_steps.json

# 5) 回读 + 规范检查
easyeda pcb components-list --include-bbox --json > comps.json
python scripts/check.py my_plan.json
```

playbook 里 `pcb.component.modify` 的 payload 键名以 `easyeda actions` 的输出为准。如果 connector 要求 `primitiveId` 而不是 `designator`，先在 playbook 开头插一步 `pcb.components.list` 并 `capture` 位号到 id 的映射，后续引用 `${...}`。
