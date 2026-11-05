"""The array form of pattern_score must equal the per-offset comparison exactly."""
import unittest

import numpy as np

import app_bridge as bridge


def per_offset(image, point, templates):
    x, y = point
    best = 1000.0
    for template in templates:
        sample = template[7:30:2, 6:29:2]
        for dy in range(-4, 5):
            for dx in range(-3, 4):
                patch = image[y + dy - 11:y + dy + 12:2, x + dx - 11:x + dx + 12:2]
                if patch.shape == sample.shape:best = min(best, float(np.abs(patch - sample).sum(axis=2).mean()))
    return best


class PatternScoreTests(unittest.TestCase):
    def setUp(self):
        random = np.random.default_rng(7)
        self.image = random.integers(0, 256, (622, 336, 3)).astype(np.int16)
        self.templates = [random.integers(0, 256, (36, 34, 3)).astype(np.int16) for _ in range(3)]

    def test_matches_per_offset_scores_inside_and_at_every_edge(self):
        for point in ((168, 311), (43, 511), (14, 15), (15, 16), (321, 606), (322, 607), (3, 4), (333, 619)):
            with self.subTest(point=point):
                self.assertEqual(bridge.pattern_score(self.image, point, self.templates), per_offset(self.image, point, self.templates))

    def test_exact_patch_scores_zero_at_any_searched_offset(self):
        template = np.zeros((36, 34, 3), dtype=np.int16)
        template[7:30, 6:29] = self.image[300 + 2 - 11:300 + 2 + 12, 200 - 3 - 11:200 - 3 + 12]
        self.assertEqual(bridge.pattern_score(self.image, (200, 300), [template]), 0.0)

    def test_no_templates_keeps_the_sentinel(self):
        self.assertEqual(bridge.pattern_score(self.image, (168, 311), []), 1000.0)


class TrayToleranceTests(unittest.TestCase):
    def verdict(self, score, other):
        scores = iter([score, other])
        tray = {1: [np.zeros((36, 34, 3), dtype=np.int16)], 2: [np.zeros((36, 34, 3), dtype=np.int16)]}
        image = np.zeros((622, 336, 3), dtype=np.uint8)
        from PIL import Image
        from unittest.mock import patch
        with patch.object(bridge, 'tray_count', return_value=1), patch.object(bridge, 'pattern_score', side_effect=lambda *a:next(scores)):
            try:bridge.verify_tray_image(Image.fromarray(image), [1], tray);return True
            except ValueError:return False

    def test_clear_winner_slightly_past_the_limit_is_accepted(self):
        self.assertTrue(self.verdict(76.7, 171.2))

    def test_nearby_rival_or_poor_match_is_rejected(self):
        self.assertFalse(self.verdict(76.7, 130))
        self.assertFalse(self.verdict(50, 60))
        self.assertFalse(self.verdict(111, 300))


if __name__ == '__main__':
    unittest.main()
