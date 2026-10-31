"""The schedule, the stair wells, and the width-based opening inference.

These are the parts that need a table drawn out of text entities and a slab
with a hole in it — both easy to get subtly wrong, both cheap to fake.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shapely.geometry import box  # noqa: E402

from massing.model import TextItem  # noqa: E402
from massing.schedule import Schedule, ScheduleRow, read_schedule, tag_conflicts  # noqa: E402
from massing.stairs import StairWell, slab_with_wells  # noqa: E402


def text(value: str, x: float, y: float) -> TextItem:
    return TextItem(text=value, layer="0", handle="", position=(x, y), height=300.0, kind="TEXT")


def schedule_texts() -> list[TextItem]:
    """A 门窗表 the way a drawing exports one: one TEXT per cell."""
    items = [
        text("类型", 1000, 0),
        text("设计编号", 1750, 0),
        text("洞口尺寸", 2800, 0),
        text("(mm)", 3200, 0),
        text("数量", 4200, 0),
        text("图集名称", 5000, 0),
    ]
    items += [
        text("门", 1070, -324),
        text("M0821", 1800, -324),
        text("800X2100", 2900, -324),
        text("16", 4200, -324),
    ]
    # A fire door's tag is split across three cells by the drawing.
    items += [
        text("FM", 1790, -648),
        text("乙", 2000, -648),
        text("1021", 2130, -648),
        text("1000X2100", 2900, -648),
        text("8", 4200, -648),
    ]
    return items


class ScheduleTable(unittest.TestCase):
    def setUp(self) -> None:
        self.schedule = read_schedule(schedule_texts())

    def test_rows_are_read(self) -> None:
        self.assertEqual(len(self.schedule.rows), 2)

    def test_simple_row(self) -> None:
        row = self.schedule.by_tag()["M0821"]
        self.assertEqual((row.width, row.height), (800.0, 2100.0))
        self.assertEqual(row.count, 16)
        self.assertEqual(row.kind, "door")

    def test_split_tag_is_rejoined(self) -> None:
        row = self.schedule.by_tag()["FM乙1021"]
        self.assertEqual((row.width, row.height), (1000.0, 2100.0))
        self.assertEqual(row.count, 8)
        self.assertEqual(row.kind, "fire_door")

    def test_a_number_in_the_next_column_is_not_the_count(self) -> None:
        # The row's own count sits in the 数量 column; a dimension string to
        # its right must not be mistaken for it.
        texts = schedule_texts() + [text("2400", 5200, -324)]
        schedule = read_schedule(texts)
        self.assertEqual(schedule.by_tag()["M0821"].count, 16)


class TagConflicts(unittest.TestCase):
    def test_schedule_disagreeing_with_the_tag_is_reported(self) -> None:
        schedule = Schedule(
            rows=[
                ScheduleRow(tag="C1516", kind="window", width=1500, height=1600, count=4),
                ScheduleRow(tag="C2116", kind="window", width=2100, height=2100, count=12),
            ]
        )
        conflicts = tag_conflicts(schedule)
        self.assertEqual([item["tag"] for item in conflicts], ["C2116"])
        self.assertEqual(conflicts[0]["tagSize"], [2100.0, 1600.0])


class WidthMatching(unittest.TestCase):
    def setUp(self) -> None:
        self.schedule = Schedule(
            rows=[
                ScheduleRow(tag="C1816", kind="window", width=1800, height=1600, count=36),
                ScheduleRow(tag="C1822", kind="window", width=1800, height=2200, count=10),
                ScheduleRow(tag="M1824", kind="door", width=1800, height=2400, count=10),
                ScheduleRow(tag="GC1806", kind="high_window", width=1800, height=600, count=36),
                ScheduleRow(tag="C1916", kind="window", width=1900, height=1600, count=6),
                ScheduleRow(tag="C1931", kind="window", width=1900, height=3100, count=2),
            ]
        )

    def test_nearest_width_wins_and_does_not_borrow_neighbours(self) -> None:
        # A 1900 hole must not be outvoted by the 1800 windows next to it in
        # the table, which is what a percentage band would do.
        row = self.schedule.match_width(1900.0)
        self.assertIsNotNone(row)
        self.assertEqual(row.tag, "C1916")

    def test_a_shared_width_with_several_heights_is_left_alone(self) -> None:
        self.assertIsNone(self.schedule.match_width(1800.0))

    def test_a_width_the_table_does_not_list_is_left_alone(self) -> None:
        self.assertIsNone(self.schedule.match_width(1089.0))

    def test_a_narrow_measurement_is_left_alone(self) -> None:
        self.assertIsNone(self.schedule.match_width(100.0))


class StairWells(unittest.TestCase):
    def test_a_well_punches_the_plate(self) -> None:
        plate = box(0, 0, 10000, 10000)
        well = StairWell(bounds=(1000, 1000, 3000, 3000))
        punched = slab_with_wells(plate, [well])
        self.assertAlmostEqual(punched.area, plate.area - 4_000_000.0, delta=1.0)
        self.assertEqual(len(punched.interiors), 1)

    def test_a_well_off_the_plate_changes_nothing(self) -> None:
        plate = box(0, 0, 10000, 10000)
        well = StairWell(bounds=(50_000, 50_000, 52_000, 52_000))
        punched = slab_with_wells(plate, [well])
        self.assertAlmostEqual(punched.area, plate.area, delta=1.0)

    def test_two_wells_both_cut(self) -> None:
        plate = box(0, 0, 20000, 20000)
        wells = [
            StairWell(bounds=(1000, 1000, 3000, 3000)),
            StairWell(bounds=(10000, 10000, 12000, 12000)),
        ]
        punched = slab_with_wells(plate, wells)
        self.assertAlmostEqual(punched.area, plate.area - 8_000_000.0, delta=1.0)


class ExtendedTags(unittest.TestCase):
    def test_md_is_a_door(self) -> None:
        from massing.openings import parse_tag

        kind, width, height = parse_tag("MD1827")
        self.assertEqual(kind, "door")
        self.assertEqual((width, height), (1800.0, 2700.0))

    def test_tc_is_a_window(self) -> None:
        from massing.openings import parse_tag

        self.assertEqual(parse_tag("TC1816")[0], "window")


class ParapetAndSlabs(unittest.TestCase):
    def test_slab_plate_fills_the_rooms_inside_the_wall_ring(self) -> None:
        from massing.building import slab_polygons

        # Four 200 mm walls that meet only at their corners: the union is a
        # cloud, so the plate has to come from closing the ring, not the union.
        walls = [
            box(0, 0, 10_000, 200),
            box(0, 9_800, 10_000, 10_000),
            box(0, 0, 200, 10_000),
            box(9_800, 0, 10_000, 10_000),
        ]
        plates = slab_polygons(walls)
        self.assertEqual(len(plates), 1)
        self.assertAlmostEqual(plates[0].area, 100_000_000.0, delta=1_000_000.0)

    def test_parapet_band_is_a_ring(self) -> None:
        roof = box(0, 0, 10_000, 10_000)
        inner = roof.buffer(-200, join_style="mitre")
        band = roof.difference(inner)
        self.assertEqual(len(band.interiors), 1)
        self.assertAlmostEqual(band.area, 10_000 * 10_000 - 9_600 * 9_600, delta=1000.0)


class AxisAlignment(unittest.TestCase):
    def test_unique_labels_give_the_offset(self) -> None:
        from massing.axes import AxisLabel, align_offset

        reference = [
            AxisLabel("1", 0, 0),
            AxisLabel("2", 6000, 0),
            AxisLabel("A", 0, 0),
            AxisLabel("B", 0, 5000),
        ]
        target = [
            AxisLabel("1", 1000, 2000),
            AxisLabel("2", 7000, 2000),
            AxisLabel("A", 1000, 2000),
            AxisLabel("B", 1000, 7000),
        ]
        self.assertEqual(align_offset(reference, target), (-1000.0, -2000.0))

    def test_a_label_at_both_ends_of_an_axis_still_aligns(self) -> None:
        from massing.axes import AxisLabel, align_offset

        reference = [
            AxisLabel("1", 0, 0),
            AxisLabel("1", 0, 20_000),
            AxisLabel("2", 6000, 0),
            AxisLabel("2", 6000, 20_000),
        ]
        target = [
            AxisLabel("1", 1000, 500),
            AxisLabel("1", 1000, 20_500),
            AxisLabel("2", 7000, 500),
            AxisLabel("2", 7000, 20_500),
        ]
        self.assertEqual(align_offset(reference, target), (-1000.0, -500.0))

    def test_no_shared_label_returns_nothing(self) -> None:
        from massing.axes import AxisLabel, align_offset

        self.assertIsNone(align_offset([AxisLabel("1", 0, 0)], [AxisLabel("B", 0, 0)]))
        self.assertIsNone(align_offset([], [AxisLabel("1", 0, 0)]))

    def test_doubled_bubble_text_is_halved(self) -> None:
        from massing.axes import _normalise

        self.assertEqual(_normalise("11"), "1")
        self.assertEqual(_normalise("1010"), "10")
        self.assertEqual(_normalise("12"), "12")


class StoreyOffsets(unittest.TestCase):
    def _storey(self, number, centre, axes):
        from massing.building import Storey

        return Storey(
            number=number,
            walls=[],
            base=(number - 1) * 3000.0,
            height=3000.0,
            center=centre,
            axes=axes,
        )

    def test_axes_beat_the_bounding_box_centre(self) -> None:
        from massing.axes import AxisLabel
        from massing.building import BuildingPlan, storey_offsets

        plan = BuildingPlan(
            storeys=[
                self._storey(1, (0, 0), [AxisLabel("A", 0, 0), AxisLabel("B", 0, 5000)]),
                # Deliberately offset centre, as a plan with a ramp would be.
                self._storey(2, (5000, 5000), [AxisLabel("A", 1000, 500), AxisLabel("B", 1000, 5500)]),
            ]
        )
        offsets, sources, _scores = storey_offsets(plan)
        self.assertEqual(offsets[2], (-1000.0, -500.0))
        self.assertEqual(sources[2], "轴号")

    def test_stairs_are_the_fallback_when_the_axes_do_not_agree(self) -> None:
        from massing.building import BuildingPlan, storey_offsets
        from massing.stairs import StairWell

        one = self._storey(1, (0, 0), [])
        one.stairs = [StairWell(bounds=(1000, 1000, 4000, 8000))]
        two = self._storey(2, (9000, 9000), [])
        two.stairs = [StairWell(bounds=(3000, 4000, 6000, 11_000))]
        offsets, sources, _scores = storey_offsets(BuildingPlan(storeys=[one, two]))
        self.assertEqual(sources[2], "楼梯")
        self.assertEqual(offsets[2], (-2000.0, -3000.0))

    def test_without_axes_it_falls_back_to_the_centre(self) -> None:
        from massing.building import BuildingPlan, storey_offsets

        plan = BuildingPlan(
            storeys=[
                self._storey(1, (0, 0), []),
                self._storey(2, (5000, 5000), []),
            ]
        )
        offsets, sources, _scores = storey_offsets(plan)
        self.assertEqual(offsets[2], (-5000.0, -5000.0))
        self.assertEqual(sources[2], "包围盒中心")


class WallOverlapAlignment(unittest.TestCase):
    def test_a_shifted_copy_aligns_onto_the_original(self) -> None:
        from shapely.affinity import translate

        from massing.align import offset

        reference = [box(0, 0, 20_000, 200), box(0, 0, 200, 12_000), box(0, 11_800, 20_000, 12_000)]
        target = [translate(polygon, xoff=5000, yoff=3000) for polygon in reference]
        found, score = offset(reference, target)
        self.assertAlmostEqual(found[0], -5000, delta=250)
        self.assertAlmostEqual(found[1], -3000, delta=250)
        self.assertGreater(score, 0.9)

    def test_every_candidate_is_scored_and_the_walls_win(self) -> None:
        from massing.building import BuildingPlan, Storey, storey_offsets
        from massing.stairs import StairWell

        walls = [
            box(0, 0, 20_000, 200),
            box(0, 0, 200, 12_000),
            box(19_800, 0, 20_000, 12_000),
            box(0, 11_800, 20_000, 12_000),
        ]
        one = Storey(number=1, walls=walls, base=0, height=3000, center=(10_000, 6000))
        one.stairs = [StairWell(bounds=(2000, 2000, 6000, 9000))]
        # Same walls moved by (3000, 2000), but the stair object is drawn
        # somewhere else entirely — as a single flight would be.
        two = Storey(
            number=2,
            walls=[box(p.bounds[0] + 3000, p.bounds[1] + 2000, p.bounds[2] + 3000, p.bounds[3] + 2000) for p in walls],
            base=3000,
            height=3000,
            center=(13_000, 8000),
        )
        two.stairs = [StairWell(bounds=(10_000, 3000, 12_000, 5000))]

        offsets, sources, _scores = storey_offsets(BuildingPlan(storeys=[one, two]))
        self.assertEqual(sources[2], "墙体重合")
        self.assertAlmostEqual(offsets[2][0], -3000, delta=250)
        self.assertAlmostEqual(offsets[2][1], -2000, delta=250)


if __name__ == "__main__":
    unittest.main()
