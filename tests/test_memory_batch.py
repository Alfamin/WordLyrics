"""1.6.5: the voice model gets one piece per call (four held ~7 GB more on DirectML for the same speed)."""
import unittest

import numpy as np

from wordlyrics import separate as S


class OnePiecePerCall(unittest.TestCase):
    def test_batches_hold_one_piece(self):
        self.assertEqual(S.BATCH, 1)
        mix = np.zeros((2, S.GEN * 3 + 10), np.float32)
        shapes = [spec.shape for spec in S.spec_batches(mix)]
        self.assertEqual(len(shapes), S.count_batches(mix.shape[1]))
        self.assertTrue(all(shape == (1, 4, S.DIM_F, S.DIM_T) for shape in shapes))


if __name__ == "__main__":
    unittest.main()
