"""Adverse second-pass candidates must preserve text, reliable anchors and coverage."""
from copy import deepcopy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wordlyrics import plain_refine as P, timing as T


def rows():
    return [{"text":"alpha beta gamma delta","raw":"alpha beta gamma delta","ts":0.0,"plain":True,
             "letters":[5,4,5,5],"w":[(0,3,.01),(120,125,.4),(130,135,.4),(140,145,.4)]}]


class RefinementTests(unittest.TestCase):
    def refine(self,coarse,mutate):
        def candidate(em,spf,fine,duration,band,**kwargs):
            mutate(fine)
            return True
        with patch.object(T,"_one_pass",side_effect=candidate):
            return P.refine(coarse,np.zeros((500,32),np.float32),.02,10)

    def test_supported_weak_outlier_can_move_without_moving_anchors(self):
        coarse=rows()
        def change(fine):
            fine[0]["w"][0]=(40,45,.5)
        fine,stats=self.refine(coarse,change)
        self.assertEqual(stats["plain_refinement"],"used refined alignment")
        self.assertEqual(fine[0]["w"][1:],coarse[0]["w"][1:])
        self.assertEqual(coarse[0]["w"][0],(0,3,.01))

    def test_reliable_anchor_movement_is_rejected(self):
        coarse=rows()
        def change(fine):
            fine[0]["w"][0]=(40,45,.5)
            fine[0]["w"][1]=(140,145,.8)
        result,_=self.refine(coarse,change)
        self.assertIs(result,coarse)

    def test_missing_word_or_text_change_is_rejected(self):
        for mutation in (lambda fine:fine[0].update(text="wrong words"),
                         lambda fine:fine[0]["w"].pop(),
                         lambda fine:fine[0]["w"].__setitem__(1,None)):
            coarse=rows()
            result,_=self.refine(coarse,mutation)
            self.assertIs(result,coarse)

    def test_unsupported_improvement_claim_does_not_replace_coarse(self):
        coarse=rows()
        def change(fine):
            fine[0]["w"][0]=(40,45,.005)
        result,_=self.refine(coarse,change)
        self.assertIs(result,coarse)

    def test_held_note_is_not_shortened_by_the_prior(self):
        coarse=rows()
        coarse[0]["w"][-1]=(140,490,.4)
        def change(fine):
            fine[0]["w"][0]=(40,45,.5)
            fine[0]["w"][-1]=(140,450,.8)
        result,_=self.refine(coarse,change)
        self.assertIs(result,coarse)

    def test_unchanged_good_song_avoids_redundant_alignment(self):
        coarse=rows()
        coarse[0]["w"][0]=(110,115,.4)
        with patch.object(T,"_one_pass") as alignment:
            result,stats=P.refine(coarse,np.zeros((500,32),np.float32),.02,10)
            self.assertIs(result,coarse)
            self.assertEqual(stats["plain_passes"],2)
            alignment.assert_not_called()

    def test_failed_candidate_preserves_coarse(self):
        coarse=rows()
        with patch.object(T,"_one_pass",return_value=False):
            self.assertIs(P.refine(coarse,np.zeros((500,32),np.float32),.02,10)[0],coarse)


if __name__ == "__main__":
    unittest.main()
