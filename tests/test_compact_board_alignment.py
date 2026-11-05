"""Captured board-only fixture: split frames and two side stacks, no account HUD."""
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image, ImageDraw
from board_align import fit, check_silhouette

FIXTURE=Path(__file__).resolve().parent/'fixtures'


class CompactBoardAlignmentTests(unittest.TestCase):
    def cards(self):
        data=json.loads((FIXTURE/'compact_world_map.json').read_text(encoding='utf-8'))
        return [{**c,'layer':int(k)} for k,v in data['levelData'].items() for c in v]

    def test_split_frames_do_not_block_a_complete_map(self):
        image=FIXTURE/'compact_world_board.png';cards=self.cards()
        calibration,score=fit(image,cards);check_silhouette(image,cards,calibration)
        self.assertGreater(score,5.8)
        for col,row,x,y in [(16,0,117,132),(14,64,109,406),(42,64,227,406),(4,32,67,269)]:
            self.assertLess(abs(calibration['x_origin']+col*calibration['x_unit']-x),2)
            self.assertLess(abs(calibration['y_origin']+row*calibration['y_unit']-y),2)

    def test_missing_exposed_card_still_blocks(self):
        image=Image.open(FIXTURE/'compact_world_board.png').convert('RGB')
        ImageDraw.Draw(image).rectangle((49,248,85,289),fill=(193,255,137))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'missing.png';image.save(path)
            with self.assertRaises(ValueError):
                calibration,_=fit(path,self.cards());check_silhouette(path,self.cards(),calibration)


if __name__=='__main__':unittest.main()
