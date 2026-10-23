import {bundle} from '@remotion/bundler';
import {selectComposition, renderMedia, renderStill} from '@remotion/renderer';
import {readFile, mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root = path.dirname(fileURLToPath(import.meta.url));
const [input, output, browserExecutable, previewDir, previewOnly] = process.argv.slice(2);
if (!input || !output || !browserExecutable) throw new Error('Expected validated input JSON, output path and Chrome executable.');
const inputProps = JSON.parse(await readFile(input,'utf8'));
const serveUrl = await bundle({entryPoint:path.join(root,'src/index.jsx'),rootDir:root,publicDir:path.resolve(root,'../../assets')});
const composition = await selectComposition({serveUrl,id:'Storyboard',inputProps,browserExecutable});
const common = {serveUrl, composition, inputProps, browserExecutable, chromiumOptions:{gl:'swangle'}};
if (previewDir) {
  await mkdir(previewDir,{recursive:true});
  let offset = 0;
  for (const scene of inputProps.board.scenes) {
    let i = 0;
    for (const cue of scene.narration) {
      await renderStill({...common,frame:Math.floor((offset+cue.start+Math.min(1.2,cue.duration/2))*composition.fps),
        output:path.join(previewDir,`${scene.id}-${String(++i).padStart(2,'0')}.png`),imageFormat:'png'});
    }
    offset += scene.duration;
  }
}
if (previewOnly !== '1') {
  let last = -60;
  await renderMedia({...common,codec:'h264',outputLocation:output,concurrency:2,
    onProgress:({renderedFrames,encodedFrames,progress}) => {
      if (renderedFrames-last >= 60 || progress===1) {
        last=renderedFrames;
        console.log(JSON.stringify({rendered_frames:renderedFrames,encoded_frames:encodedFrames,progress}));
      }
    }});
}
