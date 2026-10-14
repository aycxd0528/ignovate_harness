// A single stroke language keeps the workbench controls quiet and legible.
const paths={
  terminal:['M3 4h18v16H3z','m7 9 3 3-3 3','M13 15h4'],reply:['m9 5-6 6 6 6','M3 11h11a6 6 0 0 1 6 6'],plugin:['M9 3v4h6V3','M5 7h14v7h-4v7H9v-7H5z'],
  sliders:['M4 6h16M4 12h16M4 18h16','M8 3v6M16 9v6M10 15v6'],
  send:['M12 19V5','m5 12 7-7 7 7'],stop:['M7 7h10v10H7z'],plus:['M12 5v14','M5 12h14'],minus:['M5 12h14'],
  sidebar:['M4 4h16v16H4z','M9 4v16','m14 9-3 3 3 3'],panel:['M4 4h16v16H4z','M15 4v16'],
  monitor:['M3 4h18v13H3z','M8 21h8','M12 17v4'],sun:['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8','M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5'],
  moon:['M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z'],cloud:['M6 18a4 4 0 1 1 .3-8A6 6 0 0 1 18 9a4.5 4.5 0 1 1 .5 9Z'],
  showers:['M6 14a3 3 0 1 1 .3-6A5 5 0 0 1 16 7a3.5 3.5 0 1 1 .5 7Z','M8 17l-1 3m6-3-1 3m6-3-1 3'],storm:['m13 2-8 12h6l-1 8 9-12h-6z'],
  settings:['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8','M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z'],
  folder:['M3 7V4h6l3 3h9v13H3z'],archive:['M3 3h18v4H3z','M5 7v14h14V7','M9 11h6'],
  close:['m6 6 12 12','M18 6 6 18'],more:['M5 12h.01M12 12h.01M19 12h.01'],chat:['M4 4h16v13H8l-4 4z'],
  map:['M3 3h6v6H3z','M15 15h6v6h-6z','M15 3h6v6h-6z','M9 6h6M6 9v9h9'],compare:['M5 3v18M19 3v18','m9 7 3-3 3 3','M12 4v16','m9 17 3 3 3-3'],
  file:['M6 3h8l4 4v14H6z','M14 3v5h4'],copy:['M9 8h11v13H9z','M15 8V3H4v13h5'],edit:['m4 16 12-12 4 4-12 12-5 1z','m14 6 4 4'],
  fork:['M7 3v12a4 4 0 0 0 4 4h6','M7 11h4a6 6 0 0 0 6-6V3','m14 6 3-3 3 3','m14 16 3 3-3 3'],regenerate:['M20 7v5h-5','M4 17v-5h5','M6 8a7 7 0 0 1 12-2l2 3M4 15l2 3a7 7 0 0 0 12-2'],
  info:['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18','M12 11v6M12 7h.01'],locate:['M12 3v3m0 12v3M3 12h3m12 0h3','M12 6a6 6 0 1 0 0 12 6 6 0 0 0 0-12'],
  fit:['M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5'],layout:['M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z'],
  list:['M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01'],chevron:['m7 10 5 5 5-5'],expand:['m7 14 5-5 5 5'],grip:['M8 5h.01M16 5h.01M8 12h.01M16 12h.01M8 19h.01M16 19h.01'],
  lock:['M5 11h14v10H5z','M8 11V7a4 4 0 0 1 8 0v4','M12 15v2'],unlock:['M5 11h14v10H5z','M8 11V7a4 4 0 0 1 7.5-2','M12 15v2'],
  shield:['M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z','m8 12 3 3 5-6'],download:['M12 3v12','m7 10 5 5 5-5','M4 17v4h16v-4'],check:['m5 12 4 4L19 6'],search:['M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14','m15 15 6 6']
};
export function icon(name) {
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');
  svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.75');
  svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');svg.setAttribute('aria-hidden','true');svg.classList.add('svg-icon');
  for(const path of paths[name]||paths.info){const child=document.createElementNS(svg.namespaceURI,'path');child.setAttribute('d',path);svg.append(child);}return svg;
}
export const permissionGlyph=mode=>({default:'lock',acceptEdits:'unlock',bypassPermissions:'shield'}[mode]||'lock');
export const permissionIcon=mode=>icon(permissionGlyph(mode));
