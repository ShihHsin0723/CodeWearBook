import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from PIL import Image

import visualization


class VisualizationGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.output_patch = patch.object(visualization, "VISUALIZATION_OUTPUT_DIR", self.directory)
        self.output_patch.start()
        self.addCleanup(self.output_patch.stop)
        self.frame = pd.DataFrame({"group": ["A", "B", "C"], "x": [1, 2, 3], "score": [10.0, 20.0, 15.0]})

    def assert_png(self, filename):
        path = Path(filename)
        self.assertEqual(path.parent, self.directory)
        self.assertRegex(path.name, r"^chart_[0-9a-f]{32}\.png$")
        with Image.open(path) as image:
            self.assertEqual(image.format, "PNG")
            self.assertGreater(image.width, 100)
            image.verify()
        self.assertEqual(plt.get_fignums(), [])

    def test_all_four_charts_render_through_mocked_llm(self):
        for chart in ("bar", "line", "scatter", "histogram"):
            with self.subTest(chart=chart):
                spec = {"chart_type": chart, "x": "x", "y": None if chart == "histogram" else "score", "title": "Synthetic test"}
                original = self.frame.copy(deep=True)
                with patch.object(visualization, "call_llm", return_value=json.dumps(spec)) as call:
                    filename = visualization.auto_visualize_data("Plot scores", "SELECT x, score FROM synthetic", self.frame)
                self.assert_png(filename)
                pd.testing.assert_frame_equal(self.frame, original)
                self.assertIn('"dtype"', call.call_args.args[0])
                self.assertNotIn("20.0", call.call_args.args[0])

    def test_rejects_code_paths_duplicate_keys_and_invalid_columns(self):
        good = {"chart_type": "bar", "x": "group", "y": "score", "title": "Test"}
        responses = [
            "__import__('os').system('echo unsafe')",
            json.dumps({**good, "filename": "../../unsafe.png"}),
            json.dumps({**good, "x": "unknown"}),
            json.dumps({**good, "chart_type": "pie"}),
            json.dumps({**good, "y": "group"}),
            '{"chart_type":"bar","chart_type":"line","x":"x","y":"score","title":"Test"}',
        ]
        for response in responses:
            with self.subTest(response=response), patch.object(visualization, "call_llm", return_value=response):
                with self.assertRaises(ValueError):
                    visualization.auto_visualize_data("Plot scores", "SELECT score FROM synthetic", self.frame)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_does_not_silently_drop_missing_or_infinite_values(self):
        spec = {"chart_type": "histogram", "x": "score", "y": None, "title": "Test"}
        for values in ([1.0, None], [1.0, float("inf")]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                visualization.render_visualization(spec, pd.DataFrame({"score": values}))

    def test_missingness_distribution_and_precomputed_counts(self):
        frame = pd.DataFrame({"record_id": [1, 2, 3], "missingness_percentage": [0.0, 20.0, 100.0]})
        spec = {"chart_type": "histogram", "x": "missingness_percentage", "y": None, "title": "Missingness distribution"}
        self.assert_png(visualization.render_visualization(spec, frame, missingness=True))
        counts = pd.DataFrame({"missingness_percentage": [0, 20], "participant_count": [3, 2]})
        bar = {**spec, "chart_type": "bar", "y": "participant_count"}
        self.assert_png(visualization.render_visualization(bar, counts, missingness=True))
        for invalid in (
            frame.assign(missingness_percentage=[0, 20, 101]),
            frame.assign(record_id=[1, 1, 3]),
            frame.drop(columns="record_id"),
        ):
            with self.assertRaises(ValueError):
                visualization.render_visualization(spec, invalid, missingness=True)
        self.assertEqual(plt.get_fignums(), [])


if __name__ == "__main__":
    unittest.main()
