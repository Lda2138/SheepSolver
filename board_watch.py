"""Detect a persistent board change while a solver owns the worker thread."""
from pathlib import Path
import numpy as np
from PIL import Image


class BoardWatch:
    def __init__(self,image):
        self.reference=self.read(image)
        self.candidate=None

    @staticmethod
    def read(image):
        with Image.open(image) as frame:
            return np.asarray(frame.convert('RGB').crop((20,118,316,466)),dtype=np.int16)

    def changed(self,image):
        current=self.read(image)
        difference=np.abs(current-self.reference).mean(axis=2)
        if difference.mean()<4 or (difference>30).mean()<.04:
            self.candidate=None;return False
        stable=self.candidate is not None and np.abs(current-self.candidate).mean()<3
        self.candidate=current
        return bool(stable)
