"""Short real SVG -> frames -> MP4 -> audio test, no LLM or downloaded TTS."""
import io
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import numpy as np
import soundfile
from PIL import Image
from src.storyboard.models import Storyboard,NarrationCue
from src.storyboard.water_cycle import PHASES,water_scene
from src.storyboard.themes import load_theme
from src.video.storyboard_renderer import render_storyboard_manim,probe_video


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe') and importlib.util.find_spec('manim'),'Manim and FFmpeg required')
class StoryboardRenderTests(unittest.TestCase):
    def test_fixed_four_phase_storyboard_renders_motion_previews_and_audio(self):
        scenes=[water_scene(p,p,[NarrationCue(id=p+'-cue',text=f'Observe {p}.',start=0,duration=2,concepts=[p])],load_theme()) for p in PHASES]
        board=Storyboard(title='The water cycle',topic='water_cycle',required_concepts=list(PHASES),scenes=scenes)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            wav=root/'audio.wav'
            soundfile.write(wav,.05*np.sin(np.arange(24000*8)*2*np.pi*220/24000),24000)
            output=render_storyboard_manim(board,root/'lesson.mp4',narration=wav)
            probe=probe_video(output)
            self.assertEqual({s['codec_type'] for s in probe['streams']},{'video','audio'})
            self.assertAlmostEqual(float(probe['format']['duration']),8,delta=.1)
            self.assertEqual(len(list((root/'lesson_previews').glob('*.png'))),4)
            frames=[]
            for time in (.3,1.3):
                png=subprocess.check_output(['ffmpeg','-v','error','-ss',str(time),'-i',str(output),'-frames:v','1','-f','image2pipe','-vcodec','png','-threads','1','-'])
                frames.append(np.asarray(Image.open(io.BytesIO(png))).astype(float))
            self.assertGreater(np.abs(frames[0]-frames[1]).mean(),.05,'Water particles must move, not freeze between narration changes')
            self.assertGreater(frames[0][:,:,:3].mean(),100,'Illustrated renderer must not revert to a black slide')
