// Keep this small pure module in parity with src/storyboard/motion.py.
export function pathPoint(points, t) {
  if (points.length <= 4) {
    let values = points.map(p => [p.x, p.y]);
    while (values.length > 1) values = values.slice(0, -1).map((a, i) =>
      a.map((v, j) => v + (values[i + 1][j] - v) * t));
    return values[0];
  }
  const index = Math.min(points.length - 2, Math.floor(t * (points.length - 1)));
  const ratio = t * (points.length - 1) - index;
  return ['x', 'y'].map(key => points[index][key] + (points[index + 1][key] - points[index][key]) * ratio);
}
export function objectState(object, motions, time) {
  const state = {x: object.x, y: object.y, scale: 1, opacity: object.opacity, rotate: 0};
  for (const m of motions) {
    if (m.object_id !== object.id || time < m.start) continue;
    let t = (time - m.start) / m.duration;
    t = m.loop ? t % 1 : Math.min(1, t);
    if (m.easing === 'smooth') t = t * t * (3 - 2 * t);
    if (m.action === 'move') [state.x, state.y] = pathPoint(m.path, t);
    else state[m.action] = m.from_value + (m.to_value - m.from_value) * t;
  }
  if (time < (object.visible_from ?? 0) || (object.visible_until != null && time >= object.visible_until)) state.opacity=0;
  return state;
}
export function sceneAt(board, time) {
  let start = 0;
  for (let i = 0; i < board.scenes.length; i++) {
    const scene = board.scenes[i];
    if (time < start + scene.duration || i === board.scenes.length - 1)
      return {scene, index: i, local: Math.max(0, Math.min(scene.duration, time - start))};
    start += scene.duration;
  }
}
export function activeNarration(scene, time) {
  return scene.narration.find(c => c.start <= time && time < c.start + c.duration) ?? scene.narration.at(-1);
}
