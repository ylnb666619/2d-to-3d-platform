# 2D转3D建模平台

把建筑平面图（**DXF** 或 **PDF**）读成房间、门窗、柱网和楼层，装配成三维模型，
在浏览器里直接看，需要的话再交给 CAD 内核输出 **STEP / IFC**。

面向的是「翻模」这件事：先确认识别对不对，再谈构件级重建。
所有识别结果都可以在页面上和原图逐层叠着看，错了能指到哪条线。

```
DXF ──┐
      ├─► 识图管线 ─► 场景 JSON ─► three.js（浏览器里看、点选、剖切）
PDF ──┘        │
               └─► CAD 内核（build123d / ifcopenshell）─► STEP / IFC ─► Revit
```

## 能做什么

| 能力 | 说明 |
|---|---|
| **DXF 识图** | 中望建筑的 `SWR_WALL / SWR_COLUMN / SWR_OPENING / SWR_RECTSTAIR` 智能对象、图层、块、文字、标注；墙厚、房间、轴网、楼梯井、标高 |
| **门窗** | 编号即尺寸（`C1516` = 1500×1600）；无编号的洞口按门窗表宽度反推；门窗表与图面对账 |
| **整栋装配** | 按图名「二层平面图」+ 标高链把各层归位（轴号 → 墙体重合 → 楼梯 → 包围盒，四种基准按墙体重合度打分），再拉伸成楼板、外墙、柱、楼梯、女儿墙 |
| **三维查看** | 自带 three.js 查看器：楼层筛选、构件开关、爆炸、剖切、点选命名，不依赖任何外部查看器 |
| **内核导出** | build123d 出 STEP、ifcopenshell 出 IFC4 / IFC2X3（可进 Revit），单位换算按 IFC 的 SI 规范处理 |
| **位图 PDF 识图** | 没有矢量层、没有文字的扫描件/出图件：房间 = 墨线围出的空洞，长线叠加核对，比例可调 |
| **一句话建模** | 「3 层，层高 3.6，40×18，柱距 7.2」→ 楼板 + 外墙 + 柱网，约 10 秒出 STEP |

## 快速开始

需要 Python 3.11+。

```powershell
git clone <this repo> && cd <repo>

python -m venv .venv
.venv\Scripts\python -m pip install -e .

# 起前端（首页）
.venv\Scripts\python -m massing serve --port 3246 --root tmp
```

浏览器打开 <http://127.0.0.1:3246/>，在「识图」里选一个 DXF 或 PDF。

> 国内网络装依赖建议先设 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`
> 或 `PIP_INDEX_URL`，直连 PyPI 会很慢。

## 页面

| 地址 | 内容 |
|---|---|
| `/` | 首页：最近打开的图纸、生成过的模型、识图 / 一句话建模 / 文件预览 |
| `/reading.html` | 项目页：**识图**（原图线稿 + 墙体/房间/门窗/楼梯/轴网分层）和**模型**（三维） |
| `/view?m=<step>` | 模型查看：自己的 three.js 查看器，右上角可另开 CAD 查看器看 STEP |
| `/pdf.html` | 位图 PDF 识图结果：逐页叠加图 + 房间表 + 可调比例 |

## 命令行

```powershell
python -m massing inspect   <图.dxf>                  # 单位、图层、块、文字、图幅
python -m massing walls     <图.dxf> --out walls.json # 墙体候选 + 诊断
python -m massing schedule  <图.dxf>                  # 门窗表解析与对账
python -m massing recognize <图.dxf> --ground-dxf 一层块.dxf --html reading.html
python -m massing massing   <图.dxf> --building --ground-dxf 一层块.dxf `
       --ifc building.ifc --ifc-schema both --out building.step
python -m massing overlay   <图.dxf> --out overlay.png
python -m massing preset    "3 层，层高 3.6，40×18，柱距 7.2" --step out.step
python -m massing serve     --port 3246 --root tmp
```

常用参数：`--json` 输出机器可读结果，`--region <序号|bbox:x0,y0,x1,y1>` 只处理某个图幅。

## 实测（一张真实建施图）

| 项 | 数 |
|---|---|
| 楼层 | 4 层 + 屋顶，层高 4.2 / 3.6 / 3.6 / 3.6 m |
| 墙 | 538 面 |
| 柱 | 285 根（四层 64 / 55 / 55 / 55） |
| 门窗 | 278 个（有编号 258，按门窗表补全 20） |
| 楼板 | 5 块，8 处楼梯洞口 |
| 房间 | 77 个 IfcSpace，50 个带名称 |
| 三维场景 | 880 个构件、2124 m³ |
| 一次识图 | 13–27 秒（含装配与三维场景） |
| 内核导出 | 约 35 秒（STEP 27 MB + IFC 1 MB） |

## 代码结构

```
src/massing/
  document.py    DXF 读取、单位、图层、块/文字/标注、图幅聚类
  layers.py      图层角色分类
  walls.py       墙体与柱提取（智能对象 / 闭合轮廓 / 平行线配对）
  floors.py      楼层平面聚类
  building.py    整栋装配：楼层归位、拉伸、楼板、楼梯、女儿墙、门窗扇/框
  openings.py    门窗解析、无编号洞口反推、与墙匹配
  schedule.py    门窗表解析、与图面对账
  stairs.py      楼梯井、踏步、双跑梯段
  axes.py        轴号 → 轴网与对齐基准
  align.py       墙体重合度对齐（FFT 栅格互相关）
  recognize.py   识图：图名/比例、轴网、房间、墙体分类、门窗统计、尺寸分档
  scene.py       构件 → 棱柱（给浏览器）
  pipeline.py    一次读取产出 2D + 3D
  webui.py       项目页（SVG + three.js）
  model_view.js  三维视图：点选、楼层、爆炸、剖切
  server.py      页面服务 + /api + 作业队列
  pdfread.py     位图 PDF 识图
  preset.py      一句话 → 体块
  ifc_export.py  IFC4 / IFC2X3 导出
  massing.py     拉伸与 STEP 导出、层高读取
```

## 已知限制

- **只吃 DXF / 位图 PDF**，不吃 DWG（需要先在 CAD 里另存为 DXF）。
- 首层楼板会比上部大一圈（台阶、坡道被闭运算并进来）。
- 门窗的窗台高度用常规值（窗 900、高窗 2400），不是图上的。
- 房间轮廓是墙网闭运算推出来的，面积略偏大。
- 位图 PDF 的房间会误报（尺寸线框、家具缝）；墙面没有中心线和厚度，还不能直接出三维。
- 一句话建模只认矩形体块和数字，「L 形」「中庭」认不了。
- 楼梯在 STEP 里是真实梯段，在 IFC 里目前只是楼板洞口（还不是 `IfcStair`）。

## 许可

MIT。three.js 以 MIT 分发，见 `vendor/three.module.js`。
