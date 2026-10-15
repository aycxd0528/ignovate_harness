// Retain nodes that already represent the same content, including native focus,
// text selection, details expansion and output scroll offsets.
const nodeKey=node=>node.nodeType===1?node.dataset.renderKey||node.dataset.toolId||'':null;
export function synchronize(parent,nodes){
  const wanted=new Set(nodes);
  for(const child of [...parent.childNodes])if(!wanted.has(child))child.remove();
  for(let index=0;index<nodes.length;index++)if(parent.childNodes[index]!==nodes[index])parent.insertBefore(nodes[index],parent.childNodes[index]||null);
}
export function patchElement(current,next){
  if(current._reactIsland&&next._reactIsland){current._reactIsland.update(next._reactIsland.props);next._reactIsland.destroy();return current;}
  if(current.nodeType!==next.nodeType||current.nodeName!==next.nodeName){current.replaceWith(next);return next;}
  if(current.nodeType===3){if(current.data!==next.data)current.data=next.data;return current;}
  if(current.nodeType!==1)return current;
  for(const attribute of [...current.attributes])if(attribute.name!=='open'&&!next.hasAttribute(attribute.name))current.removeAttribute(attribute.name);
  for(const attribute of next.attributes)if(attribute.name!=='open'&&current.getAttribute(attribute.name)!==attribute.value)current.setAttribute(attribute.name,attribute.value);
  if(current.nodeName==='INPUT'){current.checked=next.checked;current.disabled=next.disabled;}
  const old=[...current.childNodes],keys=new Map(old.filter(node=>nodeKey(node)).map(node=>[nodeKey(node),node])),used=new Set(),children=[];
  for(const [index,child] of [...next.childNodes].entries()){
    const key=nodeKey(child);let match=key?keys.get(key):old[index];
    if(used.has(match)||nodeKey(match||{})||match?.nodeName!==child.nodeName){if(!key||nodeKey(match||{})!==key)match=null;}
    if(match){used.add(match);children.push(patchElement(match,child));}else children.push(child);
  }
  synchronize(current,children);return current;
}
const proseValues=new WeakMap();
export function updateProse(root,text,render){
  if(proseValues.get(root)===text)return false;
  const next=render(text,root.className.replace(/^prose\s*/,''));if(root.dataset.renderKey)next.dataset.renderKey=root.dataset.renderKey;
  patchElement(root,next);proseValues.set(root,text);return true;
}
