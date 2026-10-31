"""Write the assembled building as IFC4.

IFC models walls the way a model author thinks about them: an extruded
rectangle along a centreline, with openings as separate elements that void the
wall. That is exactly what the extraction already produces — a centreline, a
thickness, and openings with a width and a sill — so this module is a mapping,
not a reconstruction.
"""

from __future__ import annotations

import math
from typing import Any

from shapely.affinity import translate

from .building import BuildingPlan, slab_polygons, storey_offsets
from .openings import Opening, openings_in, place_openings


def _matrix(x: float, y: float, z: float, angle: float):
    import numpy as np

    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return np.array(
        [
            [cos_a, -sin_a, 0.0, x],
            [sin_a, cos_a, 0.0, y],
            [0.0, 0.0, 1.0, z],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def _point_segment_distance(point, start, end) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.dist(point, start)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.dist(point, (ax + t * dx, ay + t * dy))


def _nearest_wall(segments: list[Any], point: tuple[float, float], limit: float = 400.0):
    """``(index, segment)`` of the wall the point sits on, or ``(None, None)``.

    Distance is to the segment, not to its midpoint: a long wall's midpoint can
    be metres away from the window in it.
    """
    best_index = None
    best = None
    best_distance = float("inf")
    for index, segment in enumerate(segments):
        distance = _point_segment_distance(point, segment.start, segment.end)
        if distance < best_distance:
            best_distance = distance
            best_index, best = index, segment
    if best is None or best_distance > limit:
        return None, None
    return best_index, best


def build_ifc(
    plan: BuildingPlan,
    openings: list[Opening],
    segments: list[Any],
    out_path: str,
    *,
    scale_to_mm: float = 1.0,
    slab_thickness: float = 120.0,
    roof_thickness: float = 150.0,
    project_name: str = "建筑",
    stairs: list[Any] | None = None,
    schema: str = "IFC4",
) -> dict[str, Any]:
    import ifcopenshell
    import ifcopenshell.api
    from ifcopenshell.api import run
    from ifcopenshell.util.unit import calculate_unit_scale

    from .stairs import align_wells, stairs_in

    if not plan.storeys:
        raise ValueError("没有可导出的楼层。")

    model = ifcopenshell.file(schema=schema)
    if schema.upper() != "IFC4":
        # IFC2X3 makes OwnerHistory mandatory on every root entity, and
        # ifcopenshell builds it from a user and an application that a new file
        # does not have. Register both before creating anything else.
        from ifcopenshell.api.owner import settings as owner_settings

        person = run("owner.add_person", model, identification="massing", family_name="massing")
        organisation = run("owner.add_organisation", model, identification="massing", name="massing")
        person_org = run(
            "owner.add_person_and_organisation", model, person=person, organisation=organisation
        )
        application = run(
            "owner.add_application",
            model,
            application_developer=organisation,
            application_full_name="massing",
            application_identifier="massing",
            version="1.0",
        )
        owner_settings.get_user = lambda _file: person_org
        owner_settings.get_application = lambda _file: application
    project = run("root.create_entity", model, ifc_class="IfcProject", name=project_name)
    run("unit.assign_unit", model, length={"is_metric": True, "raw": "MILLIMETERS"}, area={}, volume={})
    # ifcopenshell's geometry helpers take SI units and convert them back to the
    # file's unit by dividing by this scale. This model is in millimetres, so a
    # dimension in millimetres is multiplied by the scale (0.001) on its way in:
    # passing millimetres straight through makes a 4.2 m wall 4.2 km tall, which
    # is exactly what Revit silently refuses to import.
    mm_to_si = calculate_unit_scale(model)
    model_context = run("context.add_context", model, context_type="Model")
    body = run(
        "context.add_context",
        model,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=model_context,
    )

    site = run("root.create_entity", model, ifc_class="IfcSite", name="场地")
    building = run("root.create_entity", model, ifc_class="IfcBuilding", name=project_name)
    run("aggregate.assign_object", model, relating_object=project, products=[site])
    run("aggregate.assign_object", model, relating_object=site, products=[building])

    offsets, _sources, _scores = storey_offsets(plan)
    counts = {
        "walls": 0,
        "columns": 0,
        "openings": 0,
        "doors": 0,
        "windows": 0,
        "slabs": 0,
        "storeys": 0,
        "stairVoids": 0,
        "spaces": 0,
    }
    notes: list[str] = []
    last_storey = None

    for storey in plan.storeys:
        dx, dy = offsets[storey.number]

        ifc_storey = run(
            "root.create_entity",
            model,
            ifc_class="IfcBuildingStorey",
            name=f"{storey.number}层 {storey.title}".strip(),
        )
        ifc_storey.Elevation = float(storey.base)
        run(
            "geometry.edit_object_placement",
            model,
            product=ifc_storey,
            matrix=_matrix(0.0, 0.0, float(storey.base), 0.0),
            is_si=False,
        )
        run("aggregate.assign_object", model, relating_object=building, products=[ifc_storey])
        counts["storeys"] += 1
        last_storey = ifc_storey

        storey_segments = storey.segments or [
            segment
            for segment in segments
            if storey.bounds[0] - 500 <= segment.midpoint[0] <= storey.bounds[2] + 500
            and storey.bounds[1] - 500 <= segment.midpoint[1] <= storey.bounds[3] + 500
        ]

        wall_by_segment: dict[int, Any] = {}
        for index, segment in enumerate(storey_segments):
            sx, sy = segment.start
            angle = math.atan2(segment.end[1] - segment.start[1], segment.end[0] - segment.start[0])
            wall = run(
                "root.create_entity",
                model,
                ifc_class="IfcWallStandardCase",
                name=f"墙 {storey.number}F-{index + 1}",
            )
            representation = run(
                "geometry.add_wall_representation",
                model,
                context=body,
                length=segment.length * scale_to_mm * mm_to_si,
                height=storey.height * mm_to_si,
                thickness=segment.thickness * scale_to_mm * mm_to_si,
            )
            run("geometry.assign_representation", model, product=wall, representation=representation)
            run(
                "geometry.edit_object_placement",
                model,
                product=wall,
                matrix=_matrix(
                    (sx + dx) * scale_to_mm,
                    (sy + dy) * scale_to_mm,
                    storey.base,
                    angle,
                ),
                is_si=False,
            )
            run("spatial.assign_container", model, products=[wall], relating_structure=ifc_storey)
            wall_by_segment[id(segment)] = wall
            counts["walls"] += 1

        # Columns are their own component: a column is not a short wall, and
        # sinking it into the wall count loses it in every schedule.
        for number, polygon in enumerate(storey.columns):
            rect = polygon.minimum_rotated_rectangle
            corners = list(rect.exterior.coords)[:4]
            if len(corners) < 4:
                continue
            ux, uy = corners[1][0] - corners[0][0], corners[1][1] - corners[0][1]
            length = math.hypot(ux, uy)
            depth = math.dist(corners[1], corners[2])
            if length <= 0 or depth <= 0:
                continue
            angle = math.atan2(uy, ux)
            # The wall representation runs from its placement along +x and is
            # centred on y, so the placement sits half a depth off the corner.
            px = corners[0][0] - math.sin(angle) * depth / 2.0
            py = corners[0][1] + math.cos(angle) * depth / 2.0
            column = run(
                "root.create_entity",
                model,
                ifc_class="IfcColumn",
                name=f"柱 {storey.number}F-{number + 1}",
            )
            representation = run(
                "geometry.add_wall_representation",
                model,
                context=body,
                length=length * scale_to_mm * mm_to_si,
                height=storey.height * mm_to_si,
                thickness=depth * scale_to_mm * mm_to_si,
            )
            run("geometry.assign_representation", model, product=column, representation=representation)
            run(
                "geometry.edit_object_placement",
                model,
                product=column,
                matrix=_matrix((px + dx) * scale_to_mm, (py + dy) * scale_to_mm, storey.base, angle),
                is_si=False,
            )
            run("spatial.assign_container", model, products=[column], relating_structure=ifc_storey)
            counts["columns"] += 1

        source_openings = storey.openings or openings_in(openings, storey.bounds)
        placed = place_openings(source_openings, storey_segments)
        for opening, host, center, angle in placed:
            if opening.sill + opening.height > storey.height + 1:
                continue
            entity = run("root.create_entity", model, ifc_class="IfcOpeningElement", name=opening.tag)
            representation = run(
                "geometry.add_wall_representation",
                model,
                context=body,
                length=opening.width * mm_to_si,
                height=opening.height * mm_to_si,
                thickness=max(host.thickness * scale_to_mm, 400.0) * mm_to_si,
            )
            run("geometry.assign_representation", model, product=entity, representation=representation)
            # The wall representation starts at its centreline's first point;
            # so does its opening, half a width back from the opening's centre.
            cx = center[0] + dx - math.cos(angle) * opening.width / (2 * scale_to_mm)
            cy = center[1] + dy - math.sin(angle) * opening.width / (2 * scale_to_mm)
            run(
                "geometry.edit_object_placement",
                model,
                product=entity,
                matrix=_matrix(cx * scale_to_mm, cy * scale_to_mm, storey.base + opening.sill, angle),
                is_si=False,
            )
            run("spatial.assign_container", model, products=[entity], relating_structure=ifc_storey)
            host_wall = wall_by_segment.get(id(host))
            if host_wall is not None:
                run("feature.add_feature", model, feature=entity, element=host_wall)

            # The opening is the void; the door or window is the thing that
            # fills it. Schedules in Revit and 广联达 count the filling, not
            # the void, so the tag and the sizes go on the filling.
            is_door = opening.kind in {"door", "fire_door"}
            filling = run(
                "root.create_entity",
                model,
                ifc_class="IfcDoor" if is_door else "IfcWindow",
                name=opening.tag,
            )
            filling.Tag = opening.tag
            filling.OverallWidth = float(opening.width)
            filling.OverallHeight = float(opening.height)
            try:
                filling.PredefinedType = "DOOR" if is_door else "WINDOW"
            except Exception:  # noqa: BLE001 - enum name differs across schemas
                pass
            # How it opens, inferred from the tag: a 推拉 window is TC, a wide
            # door is a double leaf. The drawing's plan symbol would say more,
            # but the tag is what the file reliably carries.
            try:
                if is_door:
                    filling.OperationType = (
                        "DOUBLE_DOOR_SINGLE_SWING" if opening.width >= 1500 else "SINGLE_SWING_LEFT"
                    )
                else:
                    filling.OperationType = (
                        "SLIDING" if opening.tag.upper().startswith("TC") else "SINGLE_PANEL"
                    )
            except Exception:  # noqa: BLE001 - enum differs across schemas
                pass
            if "乙" in opening.tag:
                # IFC4's IfcDoor has no FireRating attribute; the grade belongs
                # on ObjectType as free text (a pset would be the formal home).
                filling.ObjectType = "乙级防火门"
            panel = run(
                "geometry.add_wall_representation",
                model,
                context=body,
                length=opening.width * mm_to_si,
                height=opening.height * mm_to_si,
                thickness=40.0 * mm_to_si,
            )
            run("geometry.assign_representation", model, product=filling, representation=panel)
            run(
                "geometry.edit_object_placement",
                model,
                product=filling,
                matrix=_matrix(cx * scale_to_mm, cy * scale_to_mm, storey.base + opening.sill, angle),
                is_si=False,
            )
            run("spatial.assign_container", model, products=[filling], relating_structure=ifc_storey)
            run("feature.add_filling", model, opening=entity, element=filling)
            counts["doors" if is_door else "windows"] += 1
            counts["openings"] += 1

        # Floor plate: draw the storey's own outline below its walls.
        walls = [translate(polygon, xoff=dx, yoff=dy) for polygon in storey.walls]
        wells = align_wells(
            storey.stairs or (stairs_in(stairs, storey.bounds) if stairs else []),
            dx,
            dy,
        )
        for plate in slab_polygons(walls):
            points = [(x * scale_to_mm, y * scale_to_mm) for x, y in plate.exterior.coords]
            slab = run(
                "root.create_entity",
                model,
                ifc_class="IfcSlab",
                name=f"{storey.number}层楼板",
            )
            representation = run(
                "geometry.add_slab_representation",
                model,
                context=body,
                depth=slab_thickness * mm_to_si,
                polyline=points,
            )
            run("geometry.assign_representation", model, product=slab, representation=representation)
            run(
                "geometry.edit_object_placement",
                model,
                product=slab,
                matrix=_matrix(0.0, 0.0, storey.base - slab_thickness, 0.0),
                is_si=False,
            )
            run("spatial.assign_container", model, products=[slab], relating_structure=ifc_storey)
            counts["slabs"] += 1

            # A stair well is a void in the slab, the same relation a window
            # has with a wall — not a hole cut into the slab's own profile,
            # which IFC represents separately from the slab entity.
            for well in wells:
                if not well.polygon().intersects(plate):
                    continue
                x0, y0, x1, y1 = well.bounds
                length = (x1 - x0) * scale_to_mm
                depth = (y1 - y0) * scale_to_mm
                if length <= 0 or depth <= 0:
                    continue
                hole = run("root.create_entity", model, ifc_class="IfcOpeningElement", name="楼梯洞口")
                hole_rep = run(
                    "geometry.add_wall_representation",
                    model,
                    context=body,
                    length=length * mm_to_si,
                    height=slab_thickness * mm_to_si,
                    thickness=depth * mm_to_si,
                )
                run("geometry.assign_representation", model, product=hole, representation=hole_rep)
                run(
                    "geometry.edit_object_placement",
                    model,
                    product=hole,
                    matrix=_matrix(
                        (x0 + x1) / 2 * scale_to_mm - length / 2,
                        (y0 + y1) / 2 * scale_to_mm,
                        storey.base - slab_thickness,
                        0.0,
                    ),
                    is_si=False,
                )
                run("spatial.assign_container", model, products=[hole], relating_structure=ifc_storey)
                run("feature.add_feature", model, feature=hole, element=slab)
                counts["stairVoids"] += 1

        # Rooms as IfcSpace: a named, measurable volume, which is what a space
        # schedule in Revit or 广联达 reads. Geometry is the room's own outline
        # extruded to the storey height.
        for room in storey.rooms:
            if room.polygon is None or room.area < 2.0:
                continue
            space = run(
                "root.create_entity",
                model,
                ifc_class="IfcSpace",
                name=room.name or f"房间 {storey.number}F-{counts['spaces'] + 1}",
            )
            if room.name:
                space.LongName = room.name
            try:
                space.PredefinedType = "SPACE"
            except Exception:  # noqa: BLE001 - enum differs across schemas
                pass
            points = [
                ((x + dx) * scale_to_mm, (y + dy) * scale_to_mm)
                for x, y in room.polygon.exterior.coords
            ]
            representation = run(
                "geometry.add_slab_representation",
                model,
                context=body,
                depth=storey.height * mm_to_si,
                polyline=points,
            )
            run("geometry.assign_representation", model, product=space, representation=representation)
            run(
                "geometry.edit_object_placement",
                model,
                product=space,
                matrix=_matrix(0.0, 0.0, storey.base, 0.0),
                is_si=False,
            )
            # A space is a spatial element, so it decomposes its storey rather
            # than being contained in it — IfcSpace has no ContainedInStructure.
            run("aggregate.assign_object", model, relating_object=ifc_storey, products=[space])
            counts["spaces"] += 1

    # Roof plate at the top of the last storey.
    top = plan.storeys[-1]
    dx, dy = offsets[top.number]
    roof_walls = [translate(polygon, xoff=dx, yoff=dy) for polygon in top.walls]
    for roof in slab_polygons(roof_walls):
        points = [(x * scale_to_mm, y * scale_to_mm) for x, y in roof.exterior.coords]
        slab = run("root.create_entity", model, ifc_class="IfcSlab", name="屋面板")
        representation = run(
            "geometry.add_slab_representation",
            model,
            context=body,
            depth=roof_thickness * mm_to_si,
            polyline=points,
        )
        run("geometry.assign_representation", model, product=slab, representation=representation)
        run(
            "geometry.edit_object_placement",
            model,
            product=slab,
            matrix=_matrix(0.0, 0.0, top.base + top.height, 0.0),
            is_si=False,
        )
        if last_storey is not None:
            run("spatial.assign_container", model, products=[slab], relating_structure=last_storey)
        counts["slabs"] += 1

    model.write(out_path)
    notes.append(
        f"导出 {counts['storeys']} 层、{counts['walls']} 面墙、{counts['columns']} 根柱、"
        f"{counts['openings']} 个洞口（门 {counts['doors']} / 窗 {counts['windows']}）、"
        f"{counts['slabs']} 块楼板、{counts['stairVoids']} 处楼梯洞口、{counts['spaces']} 个房间"
    )
    return {"path": out_path, **counts, "notes": notes}
