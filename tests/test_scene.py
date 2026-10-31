"""The scene: prisms a browser can draw, placed once and placed together.

The one bug that matters here is the offset being applied twice -- the storeys
stay level with each other on the sheet and the building tears into four blocks
a hundred metres apart. These tests pin that, and the component counts.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shapely.geometry import Polygon  # noqa: E402

from massing.building import BuildingPlan, Storey  # noqa: E402
from massing.model import WallSegment  # noqa: E402
from massing.openings import Opening  # noqa: E402
from massing.recognize import Room  # noqa: E402
from massing.scene import build_scene  # noqa: E402
from massing.webui import PlanView, write_html  # noqa: E402


def rect(x0, y0, x1, y1) -> Polygon:
    return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def storey(number: int, base: float, shift: float = 0.0) -> Storey:
    walls = [
        rect(shift, 0, shift + 6000, 200),
        rect(shift, 3800, shift + 6000, 4000),
        rect(shift, 0, shift + 200, 4000),
        rect(shift + 5800, 0, shift + 6000, 4000),
    ]
    column = rect(shift + 100, 100, shift + 600, 600)
    room = Room(
        name=f"教室{number}",
        area=20.0,
        center=(shift + 3000, 2000),
        polygon=rect(shift + 200, 200, shift + 5800, 3800),
        labeled_by="文字",
    )
    segments = [
        WallSegment(
            start=(shift, 100), end=(shift + 6000, 100), thickness=200,
            source="pairs", layer="建-墙",
        ),
        WallSegment(
            start=(shift, 3900), end=(shift + 6000, 3900), thickness=200,
            source="pairs", layer="建-墙",
        ),
    ]
    opening = Opening(
        tag="C1516", kind="window", width=1500, height=1600,
        center=(shift + 3000, 100), angle=0.0, sill=900.0,
        segments=[((shift + 2250, 100), (shift + 3750, 100))],
    )
    return Storey(
        number=number,
        walls=walls + [column],
        base=base,
        height=3000,
        title=f"{number}层平面图",
        center=(shift + 3000, 2000),
        bounds=(shift, 0, shift + 6000, 4000),
        openings=[opening],
        segments=segments,
        columns=[column],
        rooms=[room],
    )


class SceneTest(unittest.TestCase):
    def build(self) -> dict:
        # The second storey is drawn 60 m to the right on the same sheet, the
        # way a real 建施图 lays four plans side by side.
        plan = BuildingPlan(storeys=[storey(1, 0.0), storey(2, 3000.0, shift=60_000.0)])
        return build_scene(plan)

    def test_storeys_land_on_each_other(self) -> None:
        scene = self.build()
        spans = {}
        for item in scene["items"]:
            xs = [point[0] for ring in item["r"] for point in ring]
            low, high = min(xs), max(xs)
            current = spans.setdefault(item["f"], [low, high])
            current[0] = min(current[0], low)
            current[1] = max(current[1], high)
        self.assertEqual(set(spans), {1, 2})
        self.assertAlmostEqual(spans[1][0], spans[2][0], delta=0.05)
        self.assertAlmostEqual(spans[1][1], spans[2][1], delta=0.05)
        # and the whole building fits a building, not a sheet
        self.assertLess(scene["bounds"]["x"][1] - scene["bounds"]["x"][0], 40)

    def test_columns_are_not_also_walls(self) -> None:
        scene = self.build()
        self.assertEqual(scene["counts"]["column"], 2)
        self.assertTrue(all(item["c"] != "column" or item["z"][1] - item["z"][0] == 3.0
                            for item in scene["items"]))
        # the wall that contained the column was cut, so walls still exist
        self.assertGreaterEqual(scene["counts"]["wall"], 8)

    def test_openings_sit_at_their_sill(self) -> None:
        scene = self.build()
        windows = [item for item in scene["items"] if item["c"] == "window"]
        self.assertEqual(len(windows), 2)
        first = min(windows, key=lambda item: item["z"][0])
        self.assertAlmostEqual(first["z"][0], 0.9, places=2)
        self.assertAlmostEqual(first["z"][1], 2.5, places=2)

    def test_names_come_from_the_layer(self) -> None:
        scene = self.build()
        walls = [item for item in scene["items"] if item["c"] == "wall"]
        self.assertTrue(any(item["n"].startswith("建-墙") for item in walls))

    def test_page_embeds_the_scene(self) -> None:
        scene = self.build()
        view = PlanView(title="一层平面图", bounds=(0, 0, 6000, 4000))
        out = Path(__file__).resolve().parent / "_scene_test.html"
        try:
            write_html([view], str(out), source="test.dxf", scene=scene)
            text = out.read_text(encoding="utf-8")
            self.assertIn('id="sceneData"', text)
            self.assertIn("function init3d", text)
            start = text.index('id="sceneData">') + len('id="sceneData">')
            payload = json.loads(text[start:text.index("</script>", start)])
            self.assertEqual(len(payload["items"]), len(scene["items"]))
        finally:
            if out.exists():
                out.unlink()


if __name__ == "__main__":
    unittest.main()
