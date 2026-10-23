"""Generic SVG/primitive Manim renderer for a validated storyboard scene graph."""
import json
import math
import os
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from manim import Scene, SVGMobject, Text, VGroup, VMobject, Rectangle, RoundedRectangle, Ellipse, Arrow, UpdateFromAlphaFunc, config, linear
from src.storyboard.models import Storyboard
from src.storyboard.assets import asset_path
from src.storyboard.motion import scene_at, object_state, active_narration
from src.storyboard.validation import fit_text, validate_storyboard,header_box


class StoryboardManim(Scene):
    def point(self, x, y):
        return np.array([(x-0.5)*config.frame_width, (0.5-y)*config.frame_height, 0])

    def text_box(self, text, x, y, width, height, size, color, *, panel=True):
        value, size = fit_text(text, width, height, font_size=size,
                              canvas_width=1280, canvas_height=720)
        # Pango sizes are points; storyboard typography is specified in 720p
        # design pixels. The fixed frame has 90 design pixels per Manim unit.
        label = Text(value, font='DejaVu Sans', font_size=size*0.75, color=color, line_spacing=0.8)
        label.move_to(self.point(x,y))
        if label.width > width*config.frame_width or label.height > height*config.frame_height:
            raise ValueError(f'Manim text exceeds its readable layout box: {text!r}')
        if not panel: return label
        box = RoundedRectangle(width=width*config.frame_width, height=height*config.frame_height,
            corner_radius=0.10, fill_color=self.board.theme.panel, fill_opacity=0.94, stroke_width=0)
        box.move_to(label)
        return VGroup(box, label)

    def construct(self):
        self.board = validate_storyboard(json.loads(Path(os.environ['EDUGEN_STORYBOARD']).read_text()))
        if any(s.native_scene is not None for s in self.board.scenes):
            raise ValueError('Native mathematical scenes must be dispatched through the enhanced Manim pipeline.')
        self.camera.background_color = self.board.theme.background
        self.frame_groups, self.caption_groups = [], []
        for scene in self.board.scenes:
            group = VGroup()
            if scene.background == 'sky':
                # Deterministic gradient strips; same endpoint colors as CSS.
                from manim import interpolate_color, ManimColor
                for i in range(24):
                    strip = Rectangle(width=config.frame_width, height=config.frame_height/24+0.01,
                        fill_color=interpolate_color(ManimColor(self.board.theme.sky_top), ManimColor(self.board.theme.background), i/23),
                        fill_opacity=1, stroke_width=0).move_to(self.point(0.5,(i+0.5)/24))
                    group.add(strip)
            else:
                group.add(Rectangle(width=config.frame_width, height=config.frame_height,
                    fill_color=self.board.theme.background, fill_opacity=1, stroke_width=0))
            heading = self.text_box(self.board.title,*header_box(self.board.title,heading=True),self.board.theme.foreground,panel=False)
            badge = self.text_box(scene.title,*header_box(scene.title),self.board.theme.foreground)
            group.add(heading, badge)
            objects = {}
            for obj in sorted(scene.objects, key=lambda o:o.z):
                if obj.kind == 'asset':
                    mob = SVGMobject(str(asset_path(obj.asset)), should_center=True)
                    mob.stretch_to_fit_width(obj.width*config.frame_width)
                    mob.stretch_to_fit_height(obj.height*config.frame_height)
                elif obj.kind == 'circle':
                    mob = Ellipse(width=obj.width*config.frame_width,height=obj.height*config.frame_height,
                                  fill_color=obj.color,fill_opacity=1,stroke_width=0)
                elif obj.kind == 'rectangle':
                    mob = Rectangle(width=obj.width*config.frame_width,height=obj.height*config.frame_height,
                                    fill_color=obj.color,fill_opacity=1,stroke_width=0)
                elif obj.kind == 'arrow':
                    mob = Arrow(self.point(-obj.width/2,obj.height/2),self.point(obj.width/2,-obj.height/2),
                                color=obj.color,buff=0,stroke_width=4)
                elif obj.kind=='polyline':
                    mob=VMobject(color=obj.color,stroke_width=4)
                    mob.set_points_as_corners([self.point(p.x,p.y) for p in obj.points])
                else:
                    mob = self.text_box(obj.text,obj.x,obj.y,obj.width,obj.height,obj.font_size,obj.color)
                if obj.kind!='polyline': mob.move_to(self.point(obj.x,obj.y))
                offset=mob.get_center()-self.point(obj.x,obj.y)
                objects[obj.id] = (mob, {'scale':1.0,'rotate':0.0,'offset':offset})
                group.add(mob)
            captions = [self.text_box(c.text,0.5,0.90,0.91,0.14,30,self.board.theme.foreground) for c in scene.narration]
            self.frame_groups.append((group,objects))
            self.caption_groups.append(captions)
        self.current = -1
        self.previous_caption = None
        self.snapshots = set()
        self.last_frame_bucket=-1
        stage = os.environ.get('EDUGEN_STORYBOARD_PREFLIGHT') == '1'
        def draw(_, alpha):
            time = min(self.board.duration-1e-7, alpha*self.board.duration)
            index, scene, local = scene_at(self.board,time)
            self.clear()
            if index != self.current:
                self.current = index
                self.previous_caption = None
                print(json.dumps({'storyboard_scene':index+1,'scene_id':scene.id,'frame':round(time*self.board.fps)}), flush=True)
            if not stage and int(time*self.board.fps)//60 != self.last_frame_bucket:
                self.last_frame_bucket=int(time*self.board.fps)//60
                print(json.dumps({'storyboard_frame':round(time*self.board.fps),'scene_index':index+1,
                                  'scene_frame':round(local*self.board.fps),'scene_frames':round(scene.duration*self.board.fps)}),flush=True)
            group, objects = self.frame_groups[index]
            factor = 1.0
            if scene.transition.kind == 'crossfade' and index and local < scene.transition.duration:
                factor = local/scene.transition.duration
                previous, _ = self.frame_groups[index-1]
                previous.set_opacity(1-factor)
                self.add(previous)
            group.set_opacity(factor)
            for obj in scene.objects:
                mob, old = objects[obj.id]
                state = object_state(obj,scene.motions,local)
                mob.scale(state['scale']/old['scale']).rotate(state['rotate']-old['rotate'])
                mob.move_to(self.point(state['x'],state['y'])+old['offset'])
                mob.set_opacity(state['opacity']*factor)
                if obj.kind=='polyline':
                    # Group opacity sets both fill and stroke. Open function
                    # curves must stay unfilled, not become area polygons.
                    mob.set_fill(opacity=0)
                old.update(scale=state['scale'],rotate=state['rotate'])
            self.add(group)
            cue = active_narration(scene,local)
            caption_index = scene.narration.index(cue)
            if self.previous_caption is not None:
                self.remove(self.previous_caption)
            caption = self.caption_groups[index][caption_index]
            self.add(caption)
            self.previous_caption = caption
            if stage:
                key = (index,caption_index)
                if key not in self.snapshots:
                    self.snapshots.add(key)
                    preview = Path(os.environ['EDUGEN_STORYBOARD_PREVIEWS'])
                    preview.mkdir(parents=True,exist_ok=True)
                    self.renderer.update_frame(self,ignore_skipping=True)
                    from PIL import Image
                    Image.fromarray(self.renderer.get_frame()).save(preview/f'{scene.id}-{caption_index+1:02d}.png')
        if stage:
            start = 0.0
            for scene in self.board.scenes:
                for cue in scene.narration:
                    draw(None,(start+cue.start+min(cue.duration/2,1.2))/self.board.duration)
                start += scene.duration
        else:
            anchor = VGroup()
            self.add(anchor)
            self.play(UpdateFromAlphaFunc(anchor,draw),run_time=self.board.duration,rate_func=linear)
