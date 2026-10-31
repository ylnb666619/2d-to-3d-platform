"""Regression tests against synthetic drawings, independent of any real file.

The proxy path is exercised by the real ZWCAD drawing; these tests cover the
fallback strategies that drawings from other CAD systems need.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ezdxf

from massing.layers import classify_name, load_config
from massing.walls import ExtractOptions, extract


def _write(path: Path, build) -> Path:
    document = ezdxf.new("R2013", setup=True)
    document.header["$INSUNITS"] = 4  # millimetres
    build(document, document.modelspace())
    document.saveas(str(path))
    return path


class LayerClassification(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_chinese_wall_layer(self) -> None:
        self.assertEqual(classify_name("建-墙体", self.config)[0], "wall")
        self.assertEqual(classify_name("外墙", self.config)[0], "wall")

    def test_english_token_layer(self) -> None:
        self.assertEqual(classify_name("A-WALL-INT", self.config)[0], "wall")
        self.assertEqual(classify_name("WINDOW", self.config)[0], "window")

    def test_unknown_layer(self) -> None:
        role, _pattern, confidence = classify_name("A$C1F2B3", self.config)
        self.assertEqual(role, "unknown")
        self.assertEqual(confidence, "none")

    def test_axis_is_not_a_wall(self) -> None:
        self.assertEqual(classify_name("轴线", self.config)[0], "axis")


class PairedLineWalls(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="massing-test-")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _path(self, name: str) -> Path:
        return Path(self._tmp.name) / name

    def test_two_parallel_lines_become_one_wall(self) -> None:
        def build(_doc, msp):
            msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "WALL"})
            msp.add_line((0, 200), (5000, 200), dxfattribs={"layer": "WALL"})
            msp.add_line((0, 3000), (5000, 3000), dxfattribs={"layer": "IGNORE"})

        path = _write(self._path("pairs.dxf"), build)
        result = extract(str(path), options=ExtractOptions(strategy="pairs"))
        self.assertGreaterEqual(len(result.segments), 1)
        thickness = result.segments[0].thickness
        self.assertAlmostEqual(thickness, 200.0, delta=1.0)
        self.assertAlmostEqual(result.segments[0].length, 5000.0, delta=1.0)

    def test_separated_lines_are_not_a_wall(self) -> None:
        def build(_doc, msp):
            msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "WALL"})
            msp.add_line((0, 2000), (5000, 2000), dxfattribs={"layer": "WALL"})

        path = _write(self._path("far.dxf"), build)
        result = extract(str(path), options=ExtractOptions(strategy="pairs"))
        self.assertEqual(result.segments, [])

    def test_closed_polygon_wall(self) -> None:
        def build(_doc, msp):
            msp.add_lwpolyline(
                [(0, 0), (4000, 0), (4000, 200), (0, 200)],
                close=True,
                dxfattribs={"layer": "WALL"},
            )

        path = _write(self._path("closed.dxf"), build)
        result = extract(str(path), options=ExtractOptions(strategy="closed"))
        self.assertEqual(len(result.polygons), 1)
        self.assertAlmostEqual(result.polygons[0].area, 800_000.0, delta=1000.0)


class StoreyHeight(unittest.TestCase):
    def _texts(self, values: list[str]):
        from massing.model import TextItem

        return [
            TextItem(text=value, layer="0", handle=str(index), position=None, height=100, kind="TEXT")
            for index, value in enumerate(values)
        ]

    def test_elevation_series_gives_the_regular_step(self) -> None:
        from massing.massing import read_height

        # Real elevation labels plus the sill/slope values that sit between them.
        texts = self._texts(["±0.000", "4.200", "7.800", "11.400", "15.000", "2.250", "-0.450"])
        reading = read_height(texts)
        self.assertEqual(reading.value_mm, 3600.0)
        self.assertEqual(reading.first_floor_mm, 4200.0)
        self.assertEqual(reading.storeys, 4)
        self.assertEqual(reading.source, "标高文字")

    def test_explicit_height_text_wins(self) -> None:
        from massing.massing import read_height

        reading = read_height(self._texts(["层高 3.300", "±0.000", "4.200"]))
        self.assertEqual(reading.value_mm, 3300.0)
        self.assertEqual(reading.source, "文字")

    def test_falls_back_to_default(self) -> None:
        from massing.massing import read_height

        reading = read_height(self._texts(["教室", "走廊"]))
        self.assertEqual(reading.value_mm, 3000.0)
        self.assertEqual(reading.source, "默认值")


class OpeningTags(unittest.TestCase):
    def test_window_tag(self) -> None:
        from massing.openings import parse_tag

        kind, width, height = parse_tag("C1516")
        self.assertEqual(kind, "window")
        self.assertEqual((width, height), (1500.0, 1600.0))

    def test_fire_door_tag(self) -> None:
        from massing.openings import parse_tag

        kind, width, height = parse_tag("FM乙1824")
        self.assertEqual(kind, "fire_door")
        self.assertEqual((width, height), (1800.0, 2400.0))

    def test_high_window_tag(self) -> None:
        from massing.openings import parse_tag

        kind, _width, _height = parse_tag("GC1806")
        self.assertEqual(kind, "high_window")

    def test_unlabelled_opening_is_ignored(self) -> None:
        from massing.openings import parse_tag

        self.assertIsNone(parse_tag(""))


if __name__ == "__main__":
    unittest.main()
