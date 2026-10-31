---
name: bim
description: Turn a 2D architectural floor plan (DXF) into a stacked building with walls, slabs, doors and windows, exported as STEP and IFC4. Use when the user has a 建施图 / 平面图 / floor plan DXF and wants a 3D building model, a massing model, or an IFC file for Revit, Tekla, BIMvision or 广联达.
license: MIT
---

# Floor plans to a building

Reads a floor-plan DXF, extracts walls and openings, matches each plan to its
storey title and elevation, and assembles a building: walls extruded per storey,
a floor slab under each with the stair wells punched out, a roof plate with a
parapet, door and window openings cut and filled, exported as STEP and IFC4.
The drawing's own 门窗表 is read and reconciled against the openings found.

## Setup

The tool lives at `D:\CAD-Viewer\bim`. Every command below means:

```powershell
$env:PYTHONPATH = "D:\CAD-Viewer\bim\src"
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.26 python -m massing <command> ...
```

IFC export additionally needs `ifcopenshell`:

```powershell
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.26 --with ifcopenshell python -m massing ...
```

## The input

Ask for a **DXF**, not a DWG: export from the CAD app (Save As → DXF 2013/2018).
DWG cannot be read. Keep layers unmerged and bind any external references —
walls drawn in an XREF are not in the file.

Drawings produced with ZWCAD Architecture carry walls, columns, doors and
windows as custom objects (`SWR_WALL`, `SWR_COLUMN`, `SWR_OPENING`). Their
proxy graphics hold the real geometry — both wall faces, the centreline and the
thickness — so those readings are exact, not inferred from stray lines.

## Workflow

1. **Look first.**

   ```powershell
   python -m massing inspect <plan.dxf>
   ```

   Reports units, entity counts, layers, block references, text and the
   drawing sheets found in model space. Check `$INSUNITS`: a unitless drawing is
   reported as such and its coordinates are taken as drawing units.

2. **Check the layer reading.**

   ```powershell
   python -m massing classify <plan.dxf>
   ```

   Lists every layer with its assigned role and, more usefully, the layers it
   could not place. Edit `D:\CAD-Viewer\bim\config\layer-roles.default.json` or
   pass `--config` when the office naming is unusual.

3. **Extract walls and look at the comparison image.**

   ```powershell
   python -m massing walls <plan.dxf> --out walls.json
   python -m massing overlay <plan.dxf> --out overlay.png
   ```

   The overlay draws the original linework in grey and the detected wall areas
   in translucent red, in the same coordinate system. Red off the linework means
   a unit or scale problem; red missing means an unmatched layer; red filling a
   room means the room outline was read as a wall.

4. **Check the door and window schedule.**

   ```powershell
   python -m massing schedule <plan.dxf>
   ```

   Reads the 门窗表 (type, tag, size, count) and compares it with the openings
   actually drawn. A difference of a few is normal when the building's plans are
   split across files. It also flags rows whose size contradicts the tag's own
   naming rule — that is an inconsistency in the drawing, and it is reported,
   never silently resolved.

5. **Read the drawing the way a 建施图 is read.**

   ```powershell
   python -m massing recognize <plan.dxf>
   ```

   Reports, per plan on the sheet: the title and scale, the axis grid (numbers
   across, letters down, I/O/Z never used), the rooms with their names and
   areas, walls split into external and internal with a thickness table, the
   openings grouped by tag, stairs, levels and the three tiers of dimension
   strings. Every plan is read on its own — a sheet holds several floors side by
   side, and reading them together mixes four floors into one answer.

   Whatever could not be determined is listed under 注意 rather than guessed:
   no rooms found, no axis bubbles, or a sheet that is not a plan at all.

   Add `--html reading.html` to write the same reading as a page: one SVG per
   plan in the drawing's own coordinates, with walls, rooms, openings, stair
   wells, grid and room names as layers that can be switched off one at a time,
   and the plan's conclusions beside it. The page also has a 模型 tab that draws
   the assembled building with three.js — walls, columns, slabs, stairs, doors
   and windows as extruded prisms, floor by floor, with an explosion view, a
   cut plane, and a component inspector: click a prism and it names itself.

   To let the browser ask for a read as well as show one:

   ```powershell
   python -m massing serve <plan.dxf> --ground-dxf <ground.dxf> --port 3246
   ```

   The reading is Python's to do and the browser cannot do it, so the page asks
   the server: drop a DXF on it (or type a path) and the machine that drew the
   page reads it, rewrites the page and reloads it. The job reports the step it
   is on — about eleven seconds to open a 建施图, about twenty-seven for the
   whole read. One job at a time; two pipelines in one interpreter are slower
   than either alone.

   The model tab also keeps the kernel's half of the job: 导出 STEP and
   STEP + IFC run `massing --building` in a child process and stream its log,
   and 在查看器打开 hands the STEP to the CAD Viewer on port 3245.

6. **Assemble the building.**

   ```powershell
   python -m massing massing <plan.dxf> --building --out building.step
   ```

   Finds the plan titles (二层平面图 …), reads the storey heights from the
   elevation labels (0.000 → 4.200 → 7.800 …), aligns every plan onto the same
   position (plans are drawn side by side on the sheet), extrudes each storey,
   adds a slab per floor with the stair wells punched, a roof plate with a
   parapet, and cuts door and window openings.

   Alignment goes by axis labels first, then by stair wells, and only then by
   the bounding-box centre — a ground floor with steps or a ramp has a
   different outline, and centring it against the upper floors shifts the whole
   storey. Say which datum was used; the report names it per storey.

   When the ground floor is a separate drawing:

   ```powershell
   python -m massing massing <plan.dxf> --building --ground-dxf <ground.dxf> --out building.step
   ```

   Without a real plan for a storey, the nearest floor is borrowed and the
   output says so. Never present a borrowed storey as measured.

   The parapet height is not dimensioned on a plan, so it defaults to 1200 mm:
   say it is an assumption, and pass `--parapet 0` to leave it off.

7. **Export IFC for Revit / Tekla / 广联达.**

   ```powershell
   python -m massing massing <plan.dxf> --building --ground-dxf <ground.dxf> `
     --ifc building.ifc --out building.step
   ```

   `--ifc-schema IFC2X3` for Revit 2018, which does not import IFC4 reliably;
   `--ifc-schema both` writes both files. The file is in millimetres, and
   ifcopenshell's geometry helpers take SI units — every dimension is scaled on
   the way in. Get that wrong and Revit imports nothing, without an error.

## What the drawing provides

| Piece | Where it comes from |
| --- | --- |
| Wall thickness | distance between the two faces in the wall object's proxy graphic |
| Storey height | modal difference between elevation labels (0.000, 4.200, 7.800 …) |
| Ground-floor height | first gap in that chain, reported separately |
| Opening size | the schedule tag: `C1516` = 1500 × 1600, `M1021` = 1000 × 2100 |
| Opening kind | tag prefix: `C`/`TC`/`MC` window, `GC` high window, `M`/`MD`/`MLC` door, `FM` fire door |
| Unlabelled opening | width from the symbol's own width line, height from the 门窗表 row with the same width — only when exactly one height fits |
| Opening count | the 门窗表's 数量 column, reconciled against what was drawn |
| Stair wells | the outline in each `SWR_RECTSTAIR` object |
| Columns | the closed loop in each `SWR_COLUMN` object's proxy graphic |
| Rooms | the enclosed faces between walls, named by the text inside them |
| Room names | text inside `SWR_TEXT` objects and block references |

## Limits to state in the answer

- An unlabelled opening is cut only when its measured width matches one
  scheduled width unambiguously; the rest are reported, not guessed.
- Window sills use defaults (window 900, high window 2400, door 0), not the
  drawing's sill elevations.
- The parapet is a 1200 mm assumption — a plan does not dimension it.
- A missing storey plan is borrowed from the nearest floor and labelled as an
  assumption.
- No beams — they are not drawn on a plan; they need the 结施图 or a section.
- Stairs are modelled as two flights of treads plus a half landing in the STEP,
  with the tread count and depth read from the drawing's own tread lines; the
  riser is the storey height split over both runs. The IFC carries the well as a
  slab opening — the flights are not yet written as IfcStair.
- The output is a massing model: good for volume, area and coordination, not for
  construction documents.

## Output

| Command | Produces |
| --- | --- |
| `inspect`, `classify`, `walls` | JSON on stdout, or `--out file.json` |
| `massing --building` | STEP plus a `.json` sidecar recording every choice |
| `massing --ifc` | IFC4 or IFC2X3 with storeys, walls, columns, slabs, openings, door/window fillings, stair voids and rooms as IfcSpace |
| `overlay` | PNG and SVG comparison image |
| `recognize --html` | one self-contained page: the plans as SVG, the building as three.js |
| `serve` | the same page, plus `/api` so the browser can hand it a DXF |

Always report which storeys came from which file, the height source, how many
openings were cut versus skipped, and anything that was assumed.
