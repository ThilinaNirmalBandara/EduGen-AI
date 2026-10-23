import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {StoryboardVideo} from './Storyboard.jsx';
function Root() {
  return <Composition id="Storyboard" component={StoryboardVideo} width={1280} height={720} fps={30} durationInFrames={30}
    calculateMetadata={({props}) => ({width:props.board.width,height:props.board.height,fps:props.board.fps,
      durationInFrames:Math.ceil(props.board.scenes.reduce((total,s)=>total+s.duration,0)*props.board.fps)})}/>;
}
registerRoot(Root);
