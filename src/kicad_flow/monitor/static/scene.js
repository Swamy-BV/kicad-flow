// Geometry view of the same scene objects returned to the AI. No raster data.
export function sceneView(svg, info) {
  const ns = 'http://www.w3.org/2000/svg';
  const objects = new Map(), findings = new Map(), nodes = new Map();
  let revision = '', selected = '';
  const make = (tag, attrs = {}, text = '') => {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text) node.textContent = text;
    return node;
  };
  function detail() {
    const obj = objects.get(selected);
    info.textContent = obj
      ? `${obj.kind} · ${obj.properties.ref || obj.properties.text || obj.properties.name || obj.id} · bounds ${obj.bounds.join(', ')} mm · ${obj.id}`
      : `Geometry only · ${objects.size} objects · ${findings.size} potential overlaps · text bounds estimated`;
  }
  function nodeFor(obj) {
    const group = make('g', {'data-object-id': obj.id, class: `scene-object scene-${obj.kind}`});
    const [left, top, right, bottom] = obj.bounds, p = obj.properties;
    group.append(make('title', {}, `${obj.kind}: ${p.ref || p.text || p.name || obj.id}`));
    if (obj.points?.length) {
      group.append(make('polyline', {points: obj.points.map(pt => `${pt.x},${pt.y}`).join(' ')}));
    } else if (obj.kind === 'junction') {
      group.append(make('circle', {cx:obj.x, cy:obj.y, r:Math.max((right-left)/2, 0.25)}));
    } else if (obj.kind === 'no_connect') {
      group.append(make('path', {d:`M${left},${top} L${right},${bottom} M${left},${bottom} L${right},${top}`}));
    } else {
      group.append(make('rect', {x:left, y:top, width:Math.max(right-left,0.15), height:Math.max(bottom-top,0.15), 'pointer-events':'all'}));
    }
    if (p.text) {
      group.append(make('text', {x:(left+right)/2, y:(top+bottom)/2,
        'text-anchor':'middle', 'dominant-baseline':'central', 'font-size':p.size || 1.27,
        transform:`rotate(${-Number(p.rotation || 0)} ${(left+right)/2} ${(top+bottom)/2})`}, p.text));
    }
    if (obj.kind === 'pin' || obj.kind === 'sheet_pin') {
      group.append(make('circle', {cx:obj.x, cy:obj.y, r:0.3}));
    }
    group.addEventListener('click', () => { selected = obj.id; detail(); });
    return group;
  }
  return {
    get revision() { return revision; },
    clear() { objects.clear(); findings.clear(); nodes.clear(); svg.replaceChildren(); revision=''; selected=''; detail(); },
    update(data) {
      if (!data.ok) throw new Error(data.error || 'Scene inspection failed');
      if (data.mode === 'delta' && data.base_revision !== revision) {
        throw new Error('Scene revision mismatch. Select Geometry again to reload.');
      }
      if (data.mode === 'full') this.clear();
      for (const id of data.removed) { objects.delete(id); nodes.get(id)?.remove(); nodes.delete(id); }
      for (const obj of data.objects) {
        objects.set(obj.id, obj);
        const next = nodeFor(obj), old = nodes.get(obj.id);
        if (old) old.replaceWith(next); else svg.append(next);
        nodes.set(obj.id, next);
      }
      for (const id of data.removed_findings) findings.delete(id);
      for (const finding of data.findings) findings.set(finding.id, finding);
      const conflicting = new Set([...findings.values()].flatMap(f => f.objects));
      for (const [id, node] of nodes) node.classList.toggle('scene-conflict', conflicting.has(id));
      const [left,top,right,bottom] = data.page_bounds;
      svg.setAttribute('viewBox', `${left} ${top} ${right-left} ${bottom-top}`);
      svg.setAttribute('width', (right-left)*4);
      svg.setAttribute('height', (bottom-top)*4);
      revision = data.revision;
      detail();
      if (data.unsupported_kinds.length) info.textContent += ` · unsupported: ${data.unsupported_kinds.join(', ')}`;
      return {w:(right-left)*4, h:(bottom-top)*4};
    }
  };
}
