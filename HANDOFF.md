# 交接文档

更新于 2026-10-27。上下文压缩后从这里恢复。

## 这次做了什么（2026-10-27）

**识图 → 建模进了前端，用 three.js 画。** 前端不再是「2D 识图 + iframe 看一眼 STEP」，
而是一个应用：把 DXF 拖进页面（或填路径），本机 Python 识图、装配、生成场景，
three.js 在页面里把整栋楼画出来，每个构件都能点、能命名。

| 项 | 数 |
|---|---|
| 场景构件 | 880（墙 395 / 柱 64 / 楼板 4 / 楼梯 190 / 门 26 / 窗 199 / 屋面板 1 / 女儿墙 1） |
| 体积 | 2124 m³ |
| 一次完整识读 | 27 秒（其中打开图纸 11 秒） |
| 测试 | 58 个，全过 |

新增模块：`scene.py`（构件 → 棱柱 JSON）、`pipeline.py`（一次读取产出 2D + 3D）、
`server.py`（页面 + /api + 作业队列）、`model_view.js`（three.js 视图）、`shell_view.js`（打开图纸）。
`webui.py` 重写，`cli.py` 的 `recognize` 改走 pipeline，新增 `serve`。

**修了一个我自己引入的 bug**：楼层偏移先 `translate` 一次、又在 collector 里加了一次，
四层楼散成相距 100 m 的四块。现在偏移只在 `_place()` 里应用一次。

**开场改成先添图纸**：新开窗口会先弹出「添加图纸」，上次读的图已经填好路径，
点一下「识图并建模」就开始；没添加之前不再直接摆一个模型出来——没有三维场景时
「模型」页签整个隐藏。右键对话框里的路径可以换成任何 DXF。

**注意**：改完 `webui.py` / `server.py` 要**重启 serve**，Python 进程里已经 import 的模块不会重新加载，
会出现「HTML 是旧的、JS 是新的」这种半新半旧的页面。

**text-to-cad skill 的功能保住了**：模型页签左侧新增「内核文件」——
「导出 STEP」「STEP + IFC」把当前图纸交给 build123d / ifcopenshell（**独立进程**，约 35 秒），
「在查看器打开」跳 `127.0.0.1:3245`（skill 自带的 CAD Viewer，加载 27 MB 的 STEP 约 15 秒）。
内核已装进项目 venv（cadgen + build123d + ifcopenshell），不再依赖 uvx。
UI 仍然是这套自设计的三栏；查看器用**新窗口**打开，不是 iframe。

**首页**（`/`，平台名「重装武神」）：上半是两条历史——**最近打开的图纸**
（`tmp\history.json`，点一下重读并进入）和**生成过的模型**（`tmp\models.json` +
`tmp\models\*.step`，一句话建模和图纸导出都记在这里，点一下直接在查看器打开）；
下半是三个模块「识图」「一句话生成建模」「文件预览」。一句话建模不是 LLM，是**本地规则解析**：
从「3 层，层高 3.6，40×18，柱距 7.2」里读层数/层高/长宽/柱距，生成楼板+外墙+柱网的体块
（`preset.py`，约 10 秒，61 个实体），并把它**理解成了什么**回显给用户。
识图页的品牌名可点回首页。

**PDF（位图）识图**：八中那张 PDF 是**纯位图**——6 页、每页一张 7200×12592 灰度图，
没有矢量、没有文字层。新模块 `pdfread.py` 换了一套办法：房间 = 墨线**围出来的空洞**
（对白底做连通域，按"像房间的大小"筛），长线（墙/轴/尺寸）单独抽出来叠在图上给人核对。
比例用 PDF 页宽（1066 mm）+ 出图比例 1:100 算，页面上可以改比例、面积跟着重算。
6 页约 97 秒，识别出 586 个房间候选；页面在 `/pdf.html`。

**默认查看器是自己做的**：`/view?m=<step 路径>` 用同一套 three.js 画 `*.scene.json`
（一句话建模和导出都会在旁边留一份场景），点模型卡直接进这里；cadgen 的 CAD 查看器（3245）
只在「用 CAD 查看器看 STEP」这个次要按钮上出现。老模型没有场景文件的，退回 3245。

## 环境状态

| 项目 | 状态 |
|---|---|
| 项目 venv | `D:\CAD-Viewer\bim\.venv`（3.13）——ezdxf / shapely / numpy / **pillow** / playwright，**外加内核**：cadgen 0.7.26 / build123d 0.11 / ifcopenshell |
| three.js | `D:\CAD-Viewer\bim\vendor\three.module.js`（0.160.0，1.27 MB），写页面时自动复制到 html 同目录的 `vendor\` |
| **识图前端** | `http://127.0.0.1:3246/reading.html`，由 `python -m massing serve` 提供（**不再是 `http.server`**） |
| CAD 查看器 | `http://127.0.0.1:3245/`，只在没有三维场景、退回 `--step` iframe 时才用 |
| 启动器 | `launch-bim-ui.vbs`（纯 ASCII）→ 起 serve → **独立配置目录**的 Edge 应用窗口；不带图纸路径，靠 `tmp\last.json` 记住上次打开的图 |
| 快捷方式 | `cad转三维前端.lnk` → 上面的 vbs；`CAD 查看器.lnk` 照旧 |
| 图纸位置 | `C:\Users\jxgm\Desktop\项目\cad图纸\一教\`（2026-10-27 从 `项目\一教` 移过来的；同目录还有新的 `建筑_删改版.dwg`、`结构_删改版.dwg`） |
| 插件 | text-to-cad `earthtojake` 0.7.26，13 个 skill；grill skill 在 `D:\claude-skills` |
| 镜像 | `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple` |

**注意**：`settings.json` 曾被 CC Switch 之类的工具重写过（`enabledPlugins` 和镜像变量丢失）。
skill 消失或 MCP 超时，先跑 `claude plugin list`。

**网络**：GitHub 直连被重置。`https://codeload.github.com/<owner>/<repo>/tar.gz/refs/heads/main`
能下，`raw.githubusercontent.com` 可用，`git clone` 不可用，jsDelivr 可用。

## 架构

```
DXF ──► pipeline.read_drawing ──► views（SVG 用）+ scene（three.js 用）
                                        │
                     webui.write_html ◄─┘
                            │
                     tmp/reading.html ──► 浏览器
                            ▲
       server /api/jobs ◄───┘   浏览器把 DXF 交给本机 Python，识完自动刷新页面
```

`pipeline.read_drawing` 是唯一的读图入口：`recognize` 命令和 `serve` 都走它，
读一次同时得到 2D 识图和 3D 场景（重复读要 27 秒，而且可能自己和自己对不上）。

## 主要产物

```
src/massing/
  document.py    DXF 读取、单位、图层、块/文字/标注、图幅聚类
  layers.py      图层角色分类
  walls.py       墙体提取（proxy/closed/pairs）+ 柱（SWR_COLUMN）
  floors.py      楼层平面聚类
  building.py    整栋楼装配：归位、拉伸、楼板、楼梯、女儿墙、门窗扇/框
  openings.py    门窗解析、无编号洞口按门窗表反推、与墙匹配
  schedule.py    门窗表解析、与图面对账、图纸自身尺寸矛盾
  stairs.py      楼梯井 + 踏步提取 + 双跑梯段几何
  axes.py        轴号（SWR_AXIS_LABEL）→ 轴网、楼层对齐基准
  align.py       墙体重合度对齐（FFT 栅格互相关）
  recognize.py   识图：图名/比例、轴网、房间、墙体分类、门窗统计、尺寸分档
  scene.py       构件 → 棱柱（轮廓环 + 底 + 顶），米，给浏览器
  pipeline.py    一次读取：storeys + scene + 每张平面的识图结果
  preset.py      一句话 → 体块（层数/层高/长宽/柱距 → 楼板+外墙+柱网 → STEP）
  pdfread.py     位图 PDF：房间 = 墨线围出的空洞，长线叠加，比例可调
  pdf_page.html  PDF 识图页（逐页看叠加图 + 房间表）
  history.py     最近打开的图纸 + 生成过的模型（tmp\history.json / tmp\models.json）
  home_page.html 首页「重装武神」：两条历史 + 识图 / 一句话建模 / 文件预览
  server.py      页面服务 + /api（status/jobs/history/models/build/preview/export/viewer）+ 作业（一次一个）
  webui.py       单文件页面（SVG 识图 + three.js 模型 + 构件检查器）
  model_view.js  三维视图：棱柱合并、点选、楼层、爆炸、剖切、视角
  shell_view.js  打开图纸：拖放/选文件 → POST → 轮询进度 → 刷新
  ifc_export.py  IFC4 / IFC2X3 导出
  massing.py     拉伸与 STEP 导出、层高读取
  overlay.py     原图 + 墙体对比图
config/layer-roles.default.json
skill/SKILL.md   已装进插件（含 serve 与模型页签说明）
install_skill.py 更新插件后重装 skill + 文档补丁
vendor/          three.module.js
tests/           58 个测试，全过
tmp/             中间产物（reading.html / scene.json / last.json / uploads/ / *.log）
```

一次性脚本：`D:\CAD-Viewer\skill-fixes\apply.py`、`launch-cad-viewer.vbs`、`launch-bim-ui.vbs`。

## 常用命令

```powershell
$py = "D:\CAD-Viewer\bim\.venv\Scripts\python.exe"
$env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
Set-Location "D:\CAD-Viewer\bim"

# 识图 + 页面（含三维场景），约 15 秒
& $py -m massing recognize "C:\Users\jxgm\Desktop\项目\cad图纸\一教\建施图.dxf" `
  --ground-dxf "C:\Users\jxgm\Desktop\项目\cad图纸\一教\一层块.dxf" --html tmp\reading.html

# 前端服务（浏览器里可以再打开别的图）
& $py -m massing serve "C:\Users\jxgm\Desktop\项目\cad图纸\一教\建施图.dxf" `
  --ground-dxf "C:\Users\jxgm\Desktop\项目\cad图纸\一教\一层块.dxf" --port 3246 --root tmp

# 一句话生成体块（约 10 秒）
& $py -m massing.preset "3 层，层高 3.6，40×18，柱距 7.2" --step tmp\generated.step

# 门窗表对账 / 墙体 / 对比图 / 测试
& $py -m massing schedule <图.dxf>
& $py -m massing walls <图.dxf> --out walls.json
& $py -m massing overlay <图.dxf> --out overlay.png
& $py -m unittest discover -s tests
```

STEP / IFC 需要内核，**已经在 venv 里了**（cadgen + build123d + ifcopenshell），直接跑：

```powershell
& $py -m massing massing "C:\Users\jxgm\Desktop\项目\cad图纸\一教\建施图.dxf" --building `
  --ground-dxf "C:\Users\jxgm\Desktop\项目\cad图纸\一教\一层块.dxf" `
  --ifc tmp\building.ifc --ifc-schema both --out tmp\building.step
```

约 35 秒。前端「模型 → 内核文件」里的两个按钮就是执行这条命令（独立进程，
日志流回页面），「在查看器打开」把生成的 STEP 交给 `127.0.0.1:3245`。

## 技术要点（踩过的坑）

1. **智能对象**：中望建筑把墙/柱/门窗/楼梯存成 `SWR_*`，几何在 `proxy_graphic` 里，
   用 `ezdxf.proxygraphic.ProxyGraphic(...).virtual_entities()` 取。
2. **洞口编号**：`C1516` = 1500×1600，前两位宽、后两位高。`GC` 高窗、`FM` 防火门、`MD` 门洞。
3. **无编号洞口**：先锁最接近的表格宽度，再在该宽度内按数量投票，唯一高度占比 ≥60% 才采用。
4. **门窗表**：按表头各列的 x 坐标切列（同一基线混着别的表文字）；编号可能被拆成多格。
5. **楼层归位**：轴号 → 墙体重合 → 楼梯 → 包围盒中心，四种候选各自按墙体压墙体的面积打分取最高。
6. **偏移只能应用一次**：`scene._place()` 是唯一入口，`_Collector.add` 不再收 dx/dy。
   第一版先 translate 再在 collector 里加 dx，四层楼散成相距 100 m 的四块。
7. **three.js 的作用域**：`THREE` 必须在 IIFE 顶层（`buildPrism` 在 `init3d` 外面用它）。
   写成 `var THREE = await import(...)` 放在 init3d 里，就是 `THREE is not defined`。
8. **合并图元**：按 (楼层, 类别) 合并成一个 `BufferGeometry`，顶点属性 `item` 存构件索引，
   点选时 `face.a` 反查；高亮用 `setDrawRange(start, count)` 共享同一份 position，不复制几何。
9. **三角化**：`THREE.ShapeUtils.triangulateShape(contour, holes)` 支持洞口；材质用 `DoubleSide`，绕向不用管。
10. **Windows 的 SO_REUSEADDR**：`http.server` 默认允许同一端口重复绑定，快捷方式点几次就有
    7 个监听进程抢连接（实测）。`allow_reuse_address = False`，并给出「端口被占用」的提示。
11. **一次只跑一个作业**：两个 pipeline 在同一解释器里不是并行，是互相拖慢——27 秒变 198 秒。
12. **`cgi` 在 3.13 已删除**：multipart 用 `email.parser.BytesParser` 解。
13. **控制台是 GBK**：打印 `²` 会 `UnicodeEncodeError`；`cli.main()` 里 `reconfigure(errors="replace")`。
14. **图层名是 `"0"`** 时不要拿来做构件名（等于没有命名），回退到「墙」。
15. **IFC 单位**：`ifcopenshell` 几何接口吃 SI（米），尺寸要**乘** `calculate_unit_scale()`。
    漏了就是 4.2 km 高的墙，Revit 静默导入失败。
16. **IFC 空间**：`IfcSpace` 用 `IfcRelAggregates` 挂楼层，不能用 `ContainedInStructure`。
17. **IFC2X3**：要先建 person/organisation/application 和 owner history；模块名是英式 `add_organisation`。
18. **build123d**：先 `import cadgen` 再 `import build123d`（字体守卫）。模型源码必须**纯 ASCII**。
19. **前端必须走 http**：`file://` 是不透明源，动态 `import()` 和 iframe 都会被拒。
20. **识图分图幅**：一张 DXF 并排放着好几层，必须按图幅分组分别识读，分组间距 cap 在 8000。
21. **浏览器会缓存页面**：`SimpleHTTPRequestHandler` 默认不发 `Cache-Control`，Edge 拿 `Last-Modified`
    猜新鲜度，直接复用旧页面——**改了 UI 也看不到**（这次就栽在这上面）。现在所有响应都带
    `no-store`；启动器用独立 Edge 配置目录（`%LOCALAPPDATA%\massing-ui-profile`）；
    页面每 5 秒比对 `/api/status` 的 `version` 与自己的加载时刻，服务端重写过就自己刷新。
22. **内核要独立进程**：build123d 一 import 就是 15 秒、常驻几百 MB。导出跑在子进程里
    （`sys.executable -m massing massing …`，`PYTHONPATH` 指到 `src`），页面服务不跟着卡，
    内核崩了也不会把页面带走。`import cadgen` 必须在 `import build123d` 之前（字体守卫）。

## 未完成 / 已知问题

| # | 问题 | 线索 |
|---|---|---|
| 1 | ~~二三四层没有柱~~ **已修 2026-10-28** | 代理图形把轮廓画两遍、还带一条回到块原点的引线（几十米长），`polygonize` 因此不成环。`_column_polygon` 现在丢掉 >3m 的线段并去重 → 285 根全出，四层 64/55/55/55 |
| 2 | **首层楼板比墙大一圈** | 1F 楼板 1347 m² vs 上部 1044 m²，台阶/坡道被闭运算并进来 |
| 3 | **STEP 中文构件名可能乱码** | build123d 写裸 UTF-8，STEP 规范要 `\X2\` 转义 |
| 4 | **`IfcWindow.OperationType` 写不进** | IFC4 用 `PartitioningType` |
| 5 | **`IfcPlate` 未做** | 窗玻璃分件需要 `IfcRelAggregates` + `IfcPlate` |
| 6 | **楼梯未导出为 `IfcStair`** | 场景里有 190 个踏步棱柱，只有 STEP 里是真几何 |
| 7 | **首层识图两处不对** | `2#楼梯间` 报 242.8 m²；墙只认出 30 面（长宽比过滤太严） |
| 8 | **房间轮廓是闭运算推的** | 面积略偏大；应用墙体多边形扣真实墙内表面 |
| 9 | **上传的 DXF 会留在 `tmp\uploads\`** | 每上传一次多一份；`last.json` 会指向副本 |
| 10 | **只吃 DXF，不吃 DWG** | `建筑_删改版.dwg`、`结构_删改版.dwg` 要先在中望里「另存为 → DXF」；ezdxf 读不了 DWG |
| 11 | **一句话建模只认矩形体块** | 数字 + 层数 + 长宽 + 柱距；「L 形」「中庭」「退台」都认不了，回显的 spec 就是它真正理解的 |
| 12 | **位图 PDF 的房间会误报** | 尺寸线围成的框、家具之间的缝也会被当成房间；墙只有长线、没有中心线和厚度；房名/比例/门窗表都没有 OCR |
| 13 | 未做卫生间降板、反坎、梁 | 梁需结施图 |

## 下一步建议顺序

1. 柱的形态诊断（统计建施图 `SWR_COLUMN` 的代理图形情况）
2. 首层楼板/房间轮廓精确化（墙体多边形扣除）
3. IFC4 窗改 `PartitioningType` + `IfcPlate` 分件
4. STEP 中文名转义
5. `IfcStair`
6. 施工图 PDF 的其余规则（立面/剖面识读）
