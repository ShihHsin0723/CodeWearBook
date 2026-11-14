import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

spec = importlib.util.spec_from_file_location(
    "uncertainty", Path(__file__).resolve().parents[1] / "scripts/eval/summarize_uncertainty.py"
)
uncertainty = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uncertainty)
sys.modules["summarize_uncertainty"] = uncertainty
paired_spec = importlib.util.spec_from_file_location(
    "paired_uncertainty", Path(__file__).resolve().parents[1] / "scripts/eval/summarize_ablation_uncertainty.py"
)
paired = importlib.util.module_from_spec(paired_spec)
paired_spec.loader.exec_module(paired)


class UncertaintyTests(unittest.TestCase):
    def test_pairing_uses_questions_and_retains_dependence(self):
        with tempfile.TemporaryDirectory() as directory:
            full, ablation = [Path(directory) / name for name in ("full.jsonl", "ablation.jsonl")]
            rows = [dict(question="one", expected_sql="a", df_match_score=0.4, dataframe_match=False),
                    dict(question="two", expected_sql="b", df_match_score=0.8, dataframe_match=True)]
            full.write_text("\n".join(json.dumps(r) for r in rows))
            other = [dict(r, df_match_score=r["df_match_score"] - 0.2) for r in reversed(rows)]
            ablation.write_text("\n".join(json.dumps(r) for r in other))
            result = paired.compare(full, ablation, 100)
            self.assertAlmostEqual(result["dataframe_similarity"]["difference_full_minus_ablation"], 0.2)
            for bound in result["dataframe_similarity"]["paired_bootstrap_95_ci"]:
                self.assertAlmostEqual(bound, 0.2)
            self.assertEqual(result["exact_match_rate"]["paired_bootstrap_95_ci"], [0, 0])
            other[0]["expected_sql"] = "changed"
            ablation.write_text("\n".join(json.dumps(r) for r in other))
            with self.assertRaisesRegex(ValueError, "Reference SQL"):
                paired.compare(full, ablation, 100)
            ablation.write_text(json.dumps(other[1]))
            with self.assertRaisesRegex(ValueError, "question sets differ"):
                paired.compare(full, ablation, 100)

    def test_constant_scores_have_zero_width_interval(self):
        self.assertEqual(uncertainty.bootstrap_mean_interval([0.75] * 5, 100), [0.75, 0.75])

    def test_reproducible_bootstrap_and_linear_percentiles(self):
        scores = [0, 0.2, 0.7, 1]
        self.assertEqual(uncertainty.bootstrap_mean_interval(scores, 1000, 42),
                         uncertainty.bootstrap_mean_interval(scores, 1000, 42))
        self.assertEqual(uncertainty.percentile([0, 1], 0.025), 0.025)

    def test_wilson_reference(self):
        lo, hi = uncertainty.wilson_interval(35, 53)
        self.assertAlmostEqual(lo, 0.525943, places=5)
        self.assertAlmostEqual(hi, 0.773130, places=5)

    def test_failures_included_and_missing_scores_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.jsonl"
            rows = [dict(question="one", df_match_score=1, dataframe_match=True, error=None),
                    dict(question="two", df_match_score=0, dataframe_match=False, error="failed")]
            path.write_text("\n".join(json.dumps(row) for row in rows))
            result = uncertainty.summarize(path, 100)
            self.assertEqual(result["mean_dataframe_similarity"], 0.5)
            self.assertEqual(result["errors"], 1)
            rows[1]["df_match_score"] = None
            path.write_text("\n".join(json.dumps(row) for row in rows))
            with self.assertRaises(ValueError):
                uncertainty.summarize(path, 100)


if __name__ == "__main__":
    unittest.main()
