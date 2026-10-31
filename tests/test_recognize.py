"""The 识图 rules: sheet naming, the grid, rooms, walls, openings."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shapely.geometry import box  # noqa: E402

from massing.axes import AxisLabel  # noqa: E402
from massing.model import DimItem, TextItem  # noqa: E402
from massing.recognize import (  # noqa: E402
    dimension_tiers,
    find_rooms,
    grid_from_labels,
    shape_of,
    sheet,
    split_walls,
    summarise_openings,
)


def dim(measurement: float, x: float, y: float) -> DimItem:
    return DimItem(kind="DIMENSION", layer="0", handle="", measurement=measurement, position=(x, y))


def text(value: str, x: float, y: float) -> TextItem:
    return TextItem(text=value, layer="0", handle="", position=(x, y), height=300.0, kind="TEXT")


class SheetNaming(unittest.TestCase):
    def test_plan_title_and_scale(self) -> None:
        title, scale, kinds = sheet(["二层平面图", "1:100", "说明"])
        self.assertEqual(title, "二层平面图")
        self.assertEqual(scale, "1:100")
        self.assertEqual(kinds, ["平面图"])

    def test_section_is_named_not_the_plan(self) -> None:
        title, _scale, kinds = sheet(["1-1剖面图", "比例 1:50"])
        self.assertEqual(title, "1-1剖面图")
        self.assertEqual(kinds, ["剖面图"])

    def test_a_drawing_can_hold_several_kinds(self) -> None:
        _title, _scale, kinds = sheet(["一层平面图", "南立面图", "楼梯大样"])
        self.assertEqual(set(kinds), {"平面图", "立面图", "详图"})


class GridReading(unittest.TestCase):
    def test_numbers_are_columns_and_letters_are_rows(self) -> None:
        labels = [
            AxisLabel("3", 12_000, 0),
            AxisLabel("1", 0, 0),
            AxisLabel("2", 6000, 0),
            AxisLabel("B", 0, 8000),
            AxisLabel("A", 0, 0),
        ]
        grid = grid_from_labels(labels)
        self.assertEqual(grid.column_labels, ["1", "2", "3"])
        self.assertEqual(grid.columns, [0, 6000, 12_000])
        self.assertEqual(grid.row_labels, ["A", "B"])
        self.assertEqual(grid.column_spacing, [6000, 6000])
        self.assertEqual(grid.row_spacing, [8000])

    def test_i_o_z_are_never_axis_labels(self) -> None:
        grid = grid_from_labels([AxisLabel("I", 0, 0), AxisLabel("O", 0, 1000), AxisLabel("Z", 0, 2000)])
        self.assertEqual(grid.rows, [])


class WallShapes(unittest.TestCase):
    def test_a_long_rectangle_is_a_wall(self) -> None:
        thickness, length = shape_of(box(0, 0, 5000, 200))
        self.assertAlmostEqual(thickness, 200.0, delta=0.5)
        self.assertAlmostEqual(length, 5000.0, delta=0.5)

    def test_a_rotated_wall_keeps_its_true_thickness(self) -> None:
        from shapely.affinity import rotate

        thickness, length = shape_of(rotate(box(0, 0, 5000, 200), 45))
        self.assertAlmostEqual(thickness, 200.0, delta=5.0)
        self.assertAlmostEqual(length, 5000.0, delta=5.0)

    def test_columns_and_blobs_are_not_walls(self) -> None:
        walls, others = split_walls(
            [
                box(0, 0, 5000, 200),        # wall
                box(0, 0, 400, 400),         # column
                box(0, 0, 5000, 2000),       # a merged blob, 2 m thick
            ]
        )
        self.assertEqual(len(walls), 1)
        self.assertEqual(len(others), 2)


class Rooms(unittest.TestCase):
    def _ring(self):
        return [
            box(0, 0, 10_000, 200),
            box(0, 7_800, 10_000, 8_000),
            box(0, 0, 200, 8_000),
            box(9_800, 0, 10_000, 8_000),
        ]

    def test_a_closed_ring_makes_one_room(self) -> None:
        rooms = find_rooms(self._ring(), [])
        self.assertEqual(len(rooms), 1)
        self.assertAlmostEqual(rooms[0].area, 9.6 * 7.6, delta=0.5)

    def test_the_name_comes_from_the_text_inside(self) -> None:
        rooms = find_rooms(self._ring(), [text("教室", 5000, 4000)])
        self.assertEqual(rooms[0].name, "教室")
        self.assertEqual(rooms[0].labeled_by, "文字")

    def test_a_name_split_into_characters_is_joined(self) -> None:
        rooms = find_rooms(self._ring(), [text("教", 4800, 4000), text("室", 5100, 4000)])
        self.assertEqual(rooms[0].name, "教室")

    def test_door_tags_and_stair_arrows_are_not_names(self) -> None:
        rooms = find_rooms(
            self._ring(),
            [text("C1516", 5000, 4000), text("上", 5000, 3000), text("下", 5000, 5000)],
        )
        self.assertEqual(rooms[0].name, "")


class OpeningSummary(unittest.TestCase):
    def test_grouped_by_tag(self) -> None:
        class Fake:
            def __init__(self, tag, kind, width, height, inferred=False):
                self.tag = tag
                self.kind = kind
                self.width = width
                self.height = height
                self.inferred = inferred

        rows, summary = summarise_openings(
            [
                Fake("C1516", "window", 1500, 1600),
                Fake("C1516", "window", 1500, 1600),
                Fake("", "window", 1900, 1600, inferred=True),
            ]
        )
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["types"], 2)
        by_tag = {row["tag"]: row for row in rows}
        self.assertEqual(by_tag["C1516"]["count"], 2)
        self.assertEqual(by_tag["无编号"]["inferred"], 1)


class DimensionTiers(unittest.TestCase):
    def test_nearer_dimensions_are_the_detail_tier(self) -> None:
        dims = [
            dim(600, 5000, -800),
            dim(3600, 5000, -1800),
            dim(42_600, 5000, -4800),
        ]
        tiers = dimension_tiers(dims, (0, 0, 10_000, 8000))
        self.assertIn(600.0, tiers["细部"])
        self.assertIn(42_600.0, tiers["总"])


if __name__ == "__main__":
    unittest.main()
