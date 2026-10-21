# 芯片实例层

拓扑块回答「这个功能要接什么」，芯片实例回答「这颗芯片的第几脚」。

两层的分工必须分清，否则会犯两类错：

| 只有拓扑块 | 只有芯片实例 |
|---|---|
| 知道要串限流电阻、要并续流二极管，但不知道 SP3485 的 DI 是第 4 脚 | 知道 SP3485 怎么接，但客户说「做个 4 路继电器板」时无从下手 |

`assets/circuit-blocks.json` 是拓扑层（15 块），`assets/chip-variants.json` 是实例层（5 颗）。用 `--chip` 把实例套进拓扑块。

## 用法

```bash
# 看有哪几颗
python scripts/schem.py chips
python scripts/schem.py chips --for-block rs485

# 套进网表
python scripts/schem.py netlist spec.json --chip rs485=sp3485 --chip power_ldo=ams1117_33 -o nl.json
```

套用后网表里会多出四样东西：

| 字段 | 含义 |
|---|---|
| `parts[].lcsc` | 真实料号（只有核对过的才有） |
| `parts[].pin_no` | 功能名 → 真实脚号 |
| `parts[].provenance` | 出处：`origin` 回查原始块、`source` 指向 datasheet 章节、`verified` 说明核到什么程度 |
| `layout_constraints[]` | 块级布局约束，供 `check.py` 消费 |

`pin_no` 填上了，`bind` 那一步就不用等 EDA 回读——**前提是这颗芯片的引脚表真的核对过**。没核对的会进 `pin_no_partial`，由引擎报 UNKNOWN。

## 收录标准

四条，缺一条就不收：

1. **引脚号能从 datasheet 核对到。** 核不到的写 `lcsc: null` + `lcsc_status: need_select`，绝不填猜的料号。
2. **只写确定的脚。** 对不上的留空进 `pins_partial`，让下游知道「这里不知道」。
3. **与块的引脚名对不上就不许套。** 见 `blocked_by`。
4. **必须留出处。** `origin` 指向原始块，`source` 指向 datasheet 章节。没有出处的知识不能进这个文件。

## 现有的 5 颗，和它们的真实状态

| id | 块 | 型号 | 料号 | 引脚 | 布局约束 |
|---|---|---|---|---|---|
| `ams1117_33` | power_ldo | AMS1117-3.3 | C6186 | 3/3 完整 | 2 条 |
| `sp3485` | rs485 | SP3485 | 需选型 | 8/8 完整 | 5 条 |
| `sy8089` | power_buck | SY8089A | 需选型 | 5/5 完整 | 3 条 |
| `usb_c_16p` | usb_typec_ufp | USB-C 16P | 需选型 | **部分**（DP/DN 是双焊盘） | 3 条 |
| `esp32s3_wroom1` | mcu_min | ESP32-S3-WROOM-1 | 需选型 | 部分，且 **`blocked_by`** | 3 条 |

**只有 `ams1117_33` 是「引脚完整 + 有料号」的可用组合。** 其余四颗料号空着，这是故意的：宁可让落笔脚本拒绝生成，也不让你拿着一个编出来的 C 号去下单。

`esp32s3_wroom1` 是个反面教材，值得单独说：`mcu_min` 块按「外部晶振 + SWD + NRST/BOOT0」建模，而 ESP32-S3 模组内建晶振、调试是 JTAG、复位脚叫 EN、启动脚叫 IO0 且是 strapping。**引脚名根本对不上，硬套只会生成一张看起来正常、实际全错的网表。** 所以它带 `blocked_by`，套用时直接报错并告诉你该新建 `esp32_minsys` 块。

这条比多收几颗芯片重要：**能明确说「不行」的知识，比含糊能用的知识值钱。**

## 加一颗新芯片

在 `assets/chip-variants.json` 的 `variants` 里加一项：

```json
"sp3485": {
  "for_block": "rs485",
  "fills_kind": "IC",
  "name": "SP3485", "mpn": "SP3485EN", "package": "SOIC-8",
  "lcsc": null, "lcsc_status": "need_select",
  "pins": {"RO": "1", "RE": "2", "DE": "3", "DI": "4",
           "GND": "5", "A": "6", "B": "7", "VCC": "8"},
  "pins_partial": false,
  "caveats": ["..."],
  "pcb_layout": [{"rule": "decap-adjacency", "target": "VCC 去耦",
                  "constraint": "紧贴 VCC 脚", "value": "<=2mm", "severity": "must"}],
  "source": "datasheet 第几页看到什么",
  "origin": "从哪抄的",
  "verified": "核到什么程度，没核什么"
}
```

三个字段容易写错：

- **`pins` 的键必须与块的 `parts[].pins` 键一致**，否则一个都套不上（引擎会静默只填能对的，剩下的进 `pin_no_partial`）。
- **`fills_kind` 要写对的 `kind`**：`IC` / `MCU` / `J` / `Y`，与块的 `parts[].kind` 对齐。
- **`value` 里的距离要写成 `<=2mm` 或 `<=150mil`**，`check.py` 靠这个正则取值；写成「约 2mm」就取不到，会降级成 UNKNOWN。

## `pcb_layout` 的 7 种规则名与它们的可检查性

| rule | 能自动判吗 | 判据 |
|---|---|---|
| `decap-adjacency` | ✅ 能 | 同实例内最近电容到主器件的边距 ≤ 阈值 |
| `esd-adjacency` / `tvs-at-connector` | ✅ 能 | 同实例内保护器件到连接器的边距 ≤ 阈值 |
| `bias-pair` / `term-endpoint` | ❌ 要人看 | 是否成对装配、是否总线上只有一处 |
| `thermal-copper` | ❌ 要人看 | 散热铜面积与是否误连到别网 |
| `hot-loop` / `sw-node-small` | ❌ 要人看 | 回路面积、铜面大小 |
| `antenna-keepout` | ❌ 要人看 | 全层禁铜 |
| `polarity-silk` / `edge-placement` | ❌ 要人看 | 丝印、朝向 |

**能自动判的两类用几何硬算；判不了的一律进 `BLK-MANUAL` 报 UNKNOWN，不静默当满足。** 这条不能妥协——`check.py` 报 0 违规但 12 条约束里 10 条没查，比明说「10 条没查」危险得多。

## 供货维度（`supply`）：引脚对不代表买得到

每颗实例都带 `supply` 字段：

| 字段 | 含义 | 没查过时 |
|---|---|---|
| `lcsc_stock` | 立创商城实时库存（颗） | `null` |
| `library` | `basic` 基础库 / `preferred` 优选库 / `extended` 扩展库 | `unknown` |
| `stock_checked` | 查库存的日期 | `null` |
| `price_cny` | 单价（元） | `null` |

`schem.py audit` 按这张表分级，**`null` 一律判 UNKNOWN，不参与 P0-P3 排序**：

| 档 | 条件 | 处理 |
|---|---|---|
| P0 首选 | 基础库 + 库存 ≥500 | 可直接进 BOM |
| P1 可用 | 优选库 + 库存 ≥100 | 可下，注意备货 |
| P2 备选 | 库存 ≥50（非基础/优选） | 备注供货风险 |
| P3 慎用 | 库存 <50 | 必须写替代型号 |
| — | 库存 0 | **FAIL**：这个料下不了单 |
| — | 未核验（`null`） | **UNKNOWN**：不是通过 |

**为什么不留默认值**：给 `lcsc_stock` 填个 999 看着方便，但那是编的数据——和编料号一样危险，只是错误暴露得更晚（下单那一刻）。本仓库宁可每次都提醒「没查」，也不替你填一个好看的数字。

填法（查完立创商城再写）：

```json
"supply": {
  "lcsc_stock": 12800,
  "library": "basic",
  "stock_checked": "2026-10-21",
  "price_cny": 0.62
}
```

分层不是学术洁癖：P3 的料在小批量时还能买到，量一上来就断供；等投产才发现，换料意味着重画封装、重跑 DRC。**选型阶段多查一次库存，比投产阶段换一次料便宜一个数量级。**
