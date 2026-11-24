export interface FlagShape {
  tag: 'rect' | 'circle' | 'polygon' | 'path'
  attrs: Record<string, string | number>
}

function star(cx: number, cy: number, rOut: number, rIn = rOut * 0.382): string {
  const pts: string[] = []
  for (let i = 0; i < 10; i++) {
    const a = ((i * 36 - 90) * Math.PI) / 180
    const r = i % 2 === 0 ? rOut : rIn
    pts.push(`${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`)
  }
  return pts.join(' ')
}

const r = (x: number, y: number, w: number, h: number, fill: string, extra: Record<string, string | number> = {}): FlagShape => ({
  tag: 'rect',
  attrs: { x, y, width: w, height: h, fill, ...extra },
})
const c = (cx: number, cy: number, rad: number, fill: string, extra: Record<string, string | number> = {}): FlagShape => ({
  tag: 'circle',
  attrs: { cx, cy, r: rad, fill, ...extra },
})
const p = (points: string, fill: string, extra: Record<string, string | number> = {}): FlagShape => ({
  tag: 'polygon',
  attrs: { points, fill, ...extra },
})
const d = (pathD: string, attrs: Record<string, string | number>): FlagShape => ({
  tag: 'path',
  attrs: { d: pathD, ...attrs },
})

const h3 = (c1: string, c2: string, c3: string): FlagShape[] => [
  r(0, 0, 24, 5.34, c1),
  r(0, 5.33, 24, 5.34, c2),
  r(0, 10.66, 24, 5.34, c3),
]
const v3 = (c1: string, c2: string, c3: string): FlagShape[] => [
  r(0, 0, 8, 16, c1),
  r(8, 0, 8, 16, c2),
  r(16, 0, 8, 16, c3),
]

const EU_STARS: FlagShape[] = Array.from({ length: 12 }, (_, i) => {
  const a = ((i * 30 - 90) * Math.PI) / 180
  return c(+(12 + 5 * Math.cos(a)).toFixed(2), +(8 + 5 * Math.sin(a)).toFixed(2), 0.72, '#ffcc00')
})

const FLAGS: Record<string, FlagShape[]> = {
  CN: [
    r(0, 0, 24, 16, '#de2910'),
    p(star(4.8, 4.8, 2.5), '#ffde00'),
    c(9.4, 2.1, 0.7, '#ffde00'),
    c(11.1, 3.8, 0.7, '#ffde00'),
    c(11.1, 6.2, 0.7, '#ffde00'),
    c(9.4, 7.9, 0.7, '#ffde00'),
  ],
  SG: [
    r(0, 0, 24, 8, '#ef3340'),
    r(0, 8, 24, 8, '#ffffff'),
    c(5.2, 4, 2.6, '#ffffff'),
    c(6.1, 4, 2.4, '#ef3340'),
    c(6.8, 2.8, 0.45, '#ffffff'),
    c(8.1, 3.6, 0.45, '#ffffff'),
    c(7.6, 5.0, 0.45, '#ffffff'),
    c(6.0, 5.0, 0.45, '#ffffff'),
    c(5.5, 3.6, 0.45, '#ffffff'),
  ],
  EG: [...h3('#ce1126', '#ffffff', '#000000'), d('M10.7 6.3h2.6l-.4 3.4h-1.8z', { fill: '#c09300' })],
  TN: [
    r(0, 0, 24, 16, '#e70013'),
    c(12, 8, 4.2, '#ffffff'),
    c(11.8, 8, 2.8, '#e70013'),
    c(12.7, 8, 2.3, '#ffffff'),
    p(star(13.0, 8, 1.45), '#e70013'),
  ],
  MA: [r(0, 0, 24, 16, '#c1272d'), p(star(12, 8, 3.6, 1.4), 'none', { stroke: '#006233', 'stroke-width': 0.95 })],
  LB: [
    r(0, 0, 24, 4, '#ed1c24'),
    r(0, 4, 24, 8, '#ffffff'),
    r(0, 12, 24, 4, '#ed1c24'),
    p('12,4.8 9.3,10.3 14.7,10.3', '#00a651'),
    r(11.3, 9.8, 1.4, 1.5, '#00a651'),
  ],
  EU: [r(0, 0, 24, 16, '#003399'), ...EU_STARS],
  DE: h3('#000000', '#dd0000', '#ffce00'),
  FR: v3('#002395', '#ffffff', '#ed2939'),
  IT: v3('#009246', '#ffffff', '#ce2b37'),
  ES: [
    r(0, 0, 24, 4, '#aa151b'),
    r(0, 4, 24, 8, '#f1bf00'),
    r(0, 12, 24, 4, '#aa151b'),
    r(6, 6, 2.4, 4, '#aa151b', { rx: 0.6 }),
  ],
  NL: h3('#ae1c28', '#ffffff', '#21468b'),
  BE: v3('#000000', '#fae042', '#ed2939'),
  SE: [r(0, 0, 24, 16, '#006aa7'), r(6.8, 0, 3, 16, '#fecc02'), r(0, 6.5, 24, 3, '#fecc02')],
  PL: [r(0, 0, 24, 8, '#ffffff'), r(0, 8, 24, 8, '#dc143c')],
  GB: [
    r(0, 0, 24, 16, '#012169'),
    d('M0,0 L24,16 M24,0 L0,16', { stroke: '#ffffff', 'stroke-width': 3.2, fill: 'none' }),
    d('M0,0 L24,16 M24,0 L0,16', { stroke: '#c8102e', 'stroke-width': 1.5, fill: 'none' }),
    d('M12,0 V16 M0,8 H24', { stroke: '#ffffff', 'stroke-width': 5, fill: 'none' }),
    d('M12,0 V16 M0,8 H24', { stroke: '#c8102e', 'stroke-width': 3, fill: 'none' }),
  ],
  RU: h3('#ffffff', '#0039a6', '#d52b1e'),
  TR: [r(0, 0, 24, 16, '#e30a17'), c(9.2, 8, 3.8, '#ffffff'), c(10.2, 8, 3.0, '#e30a17'), p(star(14.3, 8, 1.8), '#ffffff')],
  JP: [r(0, 0, 24, 16, '#ffffff'), c(12, 8, 4.6, '#bc002d')],
  KR: [
    r(0, 0, 24, 16, '#ffffff'),
    c(12, 8, 3.8, '#cd2e3a'),
    d('M12,8 A1.9,1.9 0 0,1 12,11.8 A3.8,3.8 0 0,1 8.2,8 A1.9,1.9 0 0,0 12,8 Z', { fill: '#0047a0' }),
    d('M5.2,4.6 L7.2,2.8 M16.8,2.8 L18.8,4.6 M5.2,11.4 L7.2,13.2 M16.8,13.2 L18.8,11.4', { stroke: '#111111', 'stroke-width': 1.1, fill: 'none' }),
  ],
  TH: [
    r(0, 0, 24, 2.67, '#a51931'),
    r(0, 2.67, 24, 2.66, '#f4f5f8'),
    r(0, 5.33, 24, 5.34, '#2d2a4a'),
    r(0, 10.67, 24, 2.66, '#f4f5f8'),
    r(0, 13.33, 24, 2.67, '#a51931'),
  ],
  MY: [
    r(0, 0, 24, 16, '#ffffff'),
    ...Array.from({ length: 7 }, (_, i) => r(0, +(i * 2.28).toFixed(2), 24, 1.14, '#cc0001')),
    r(0, 0, 12, 9.12, '#010066'),
    c(4.8, 4.56, 2.7, '#ffcc00'),
    c(5.5, 4.56, 2.3, '#010066'),
    c(8.0, 4.56, 1.45, '#ffcc00'),
  ],
  ID: [r(0, 0, 24, 8, '#ce1126'), r(0, 8, 24, 8, '#ffffff')],
  VN: [r(0, 0, 24, 16, '#da251d'), p(star(12, 8, 4.4), '#ffff00')],
  PH: [r(0, 0, 24, 8, '#0038a8'), r(0, 8, 24, 8, '#ce1126'), p('0,0 10.6,8 0,16', '#ffffff'), c(3.8, 8, 1.5, '#fcd116')],
  IN: [
    ...h3('#ff9933', '#ffffff', '#138808'),
    c(12, 8, 2.0, 'none', { stroke: '#000080', 'stroke-width': 0.9 }),
    c(12, 8, 0.6, '#000080'),
  ],
  PK: [
    r(0, 0, 6, 16, '#ffffff'),
    r(6, 0, 18, 16, '#01411c'),
    c(14.6, 8.2, 3.6, '#ffffff'),
    c(15.6, 7.4, 3.2, '#01411c'),
    p(star(16.8, 6.2, 1.45), '#ffffff'),
  ],
  BD: [r(0, 0, 24, 16, '#006a4e'), c(10.6, 8, 4.2, '#f42a41')],
  KZ: [r(0, 0, 24, 16, '#00afca'), r(1.2, 1, 1.6, 14, '#fec50c'), c(12.6, 7.6, 2.8, '#fec50c')],
  KG: [r(0, 0, 24, 16, '#e8112d'), c(12, 8, 3.6, '#ffef00'), c(12, 8, 2.1, '#e8112d')],
  AE: [
    r(6, 0, 18, 5.34, '#00732f'),
    r(6, 5.33, 18, 5.34, '#ffffff'),
    r(6, 10.66, 18, 5.34, '#000000'),
    r(0, 0, 6, 16, '#ff0000'),
  ],
  SA: [r(0, 0, 24, 16, '#006c35'), r(5.5, 5.4, 13, 2.2, '#ffffff', { rx: 0.8 }), r(6.5, 10.2, 11, 1.1, '#ffffff', { rx: 0.5 })],
  QA: [
    r(0, 0, 24, 16, '#8a1538'),
    p('0,0 7,0 8.5,1 7,2 8.5,3 7,4 8.5,5 7,6 8.5,7 7,8 8.5,9 7,10 8.5,11 7,12 8.5,13 7,14 8.5,15 7,16 0,16', '#ffffff'),
  ],
  KW: [...h3('#007a3d', '#ffffff', '#ce1126'), p('0,0 6.5,5.33 6.5,10.67 0,16', '#000000')],
  OM: [
    r(6.5, 0, 17.5, 5.34, '#ffffff'),
    r(6.5, 5.33, 17.5, 5.34, '#db161b'),
    r(6.5, 10.66, 17.5, 5.34, '#008000'),
    r(0, 0, 6.5, 16, '#db161b'),
    c(3.25, 3.4, 1.4, '#ffffff'),
  ],
  IL: [
    r(0, 0, 24, 16, '#ffffff'),
    r(0, 1.6, 24, 2.2, '#0038b8'),
    r(0, 12.2, 24, 2.2, '#0038b8'),
    p('12,5.1 9.5,9.5 14.5,9.5', 'none', { stroke: '#0038b8', 'stroke-width': 0.9 }),
    p('12,10.9 9.5,6.5 14.5,6.5', 'none', { stroke: '#0038b8', 'stroke-width': 0.9 }),
  ],
  DZ: [
    r(0, 0, 12, 16, '#006233'),
    r(12, 0, 12, 16, '#ffffff'),
    c(12, 8, 3.8, '#d21034'),
    c(13.0, 8, 3.0, '#ffffff'),
    d('M12,5 A3,3 0 0,0 12,11 Z', { fill: '#006233' }),
    p(star(13.4, 8, 1.6), '#d21034'),
  ],
  ZA: [
    r(0, 0, 24, 8, '#de3831'),
    r(0, 8, 24, 8, '#002395'),
    d('M0,0 L10.5,8 L0,16 M9.5,8 H24', { stroke: '#ffffff', 'stroke-width': 5.2, fill: 'none' }),
    d('M0,0 L10.5,8 L0,16 M9.5,8 H24', { stroke: '#007a4d', 'stroke-width': 3.2, fill: 'none' }),
    p('0,2.2 7.2,8 0,13.8', '#ffb612'),
    p('0,3.6 5.5,8 0,12.4', '#000000'),
  ],
  NG: v3('#008751', '#ffffff', '#008751'),
  US: [
    r(0, 0, 24, 16, '#ffffff'),
    ...Array.from({ length: 7 }, (_, i) => r(0, +(i * 2.46).toFixed(2), 24, 1.23, '#b22234')),
    r(0, 0, 10.2, 8.62, '#3c3b6e'),
    c(2.2, 2.2, 0.55, '#ffffff'),
    c(5.1, 2.2, 0.55, '#ffffff'),
    c(8.0, 2.2, 0.55, '#ffffff'),
    c(2.2, 4.3, 0.55, '#ffffff'),
    c(5.1, 4.3, 0.55, '#ffffff'),
    c(8.0, 4.3, 0.55, '#ffffff'),
    c(2.2, 6.4, 0.55, '#ffffff'),
    c(5.1, 6.4, 0.55, '#ffffff'),
    c(8.0, 6.4, 0.55, '#ffffff'),
  ],
  CA: [
    r(0, 0, 6, 16, '#ff0000'),
    r(6, 0, 12, 16, '#ffffff'),
    r(18, 0, 6, 16, '#ff0000'),
    p('12,3.4 13.4,6.4 15.8,6.0 14.0,8.8 14.8,10.6 12.4,10.3 12.4,12.8 11.6,12.8 11.6,10.3 9.2,10.6 10.0,8.8 8.2,6.0 10.6,6.4', '#ff0000'),
  ],
  MX: [...v3('#006847', '#ffffff', '#ce1126'), c(12, 8, 1.8, '#8c5a2b')],
  BR: [
    r(0, 0, 24, 16, '#009c3b'),
    p('12,1.8 21.6,8 12,14.2 2.4,8', '#ffdf00'),
    c(12, 8, 3.4, '#002776'),
    d('M8.8,7.4 Q12,6.4 15.2,8.5', { stroke: '#ffffff', 'stroke-width': 0.9, fill: 'none' }),
  ],
  AR: [...h3('#74acdf', '#ffffff', '#74acdf'), c(12, 8, 1.8, '#f6b40e')],
  CL: [r(0, 0, 24, 8, '#ffffff'), r(0, 0, 8, 8, '#0039a6'), p(star(4, 4, 2.1), '#ffffff'), r(0, 8, 24, 8, '#d52b1e')],
  CO: [r(0, 0, 24, 8, '#fcd116'), r(0, 8, 24, 4, '#003893'), r(0, 12, 24, 4, '#ce1126')],
  PE: v3('#d91023', '#ffffff', '#d91023'),
  AU: [
    r(0, 0, 24, 16, '#012169'),
    d('M0,0 L12,8 M12,0 L0,8', { stroke: '#ffffff', 'stroke-width': 1.8, fill: 'none' }),
    d('M6,0 V8 M0,4 H12', { stroke: '#ffffff', 'stroke-width': 2.6, fill: 'none' }),
    d('M6,0 V8 M0,4 H12', { stroke: '#c8102e', 'stroke-width': 1.5, fill: 'none' }),
    c(6, 12, 1.5, '#ffffff'),
    c(18, 3.5, 0.9, '#ffffff'),
    c(15.5, 7.2, 0.9, '#ffffff'),
    c(20.5, 6.8, 0.9, '#ffffff'),
    c(18, 12.2, 0.9, '#ffffff'),
  ],
  NZ: [
    r(0, 0, 24, 16, '#012169'),
    d('M0,0 L12,8 M12,0 L0,8', { stroke: '#ffffff', 'stroke-width': 1.8, fill: 'none' }),
    d('M6,0 V8 M0,4 H12', { stroke: '#ffffff', 'stroke-width': 2.6, fill: 'none' }),
    d('M6,0 V8 M0,4 H12', { stroke: '#c8102e', 'stroke-width': 1.5, fill: 'none' }),
    c(18, 3.5, 0.9, '#c8102e'),
    c(15.5, 7.2, 0.9, '#c8102e'),
    c(20.5, 6.8, 0.9, '#c8102e'),
    c(18, 12.2, 0.9, '#c8102e'),
  ],
  CH: [r(0, 0, 24, 16, '#ff0000'), r(10.4, 3.2, 3.2, 9.6, '#ffffff'), r(7.2, 6.4, 9.6, 3.2, '#ffffff')],
}

const FALLBACK_SHAPES: FlagShape[] = [
  r(0, 0, 24, 16, '#f1f5f9'),
  c(12, 8, 4.8, 'none', { stroke: '#475569', 'stroke-width': 1.1 }),
  d('M7.2,8 H16.8 M12,3.2 V12.8', { stroke: '#475569', 'stroke-width': 1, fill: 'none' }),
]

export function countryFlagShapes(code?: string | null): FlagShape[] {
  const cc = (code || '').trim().toUpperCase()
  if (!cc) return []
  return FLAGS[cc] || FALLBACK_SHAPES
}
