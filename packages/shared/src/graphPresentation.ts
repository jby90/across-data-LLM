/** Shared canvas presentation only. No evidence or review record is modified. */
export interface FgNode {
  id: string
  name: string
  type: string
  degree: number
}
export interface FgEdge {
  source: string
  target: string
  label: string
  approved: boolean
}
export interface FgCategory {
  type: string
  name: string
  color: string
}

function cleanGraphNodeLabel(raw: string): string {
  let s = (raw || '').trim()
  s = s.replace(/\s*[（(][^）)]*(?:修订版|合订本|综合版|汇编版|非官方|官方文本|转载版)[^）)]*[）)]$/u, '').trim()
  const m = s.match(/^(.*[\u4e00-\u9fff])\s*[（(]\s*([A-Za-zÀ-ÿ\u0600-\u06FF][^）)]*)\s*[）)]$/)
  return m ? m[1].trim() : s
}

export function graphSeries(
  nodes: FgNode[], edges: FgEdge[], categories: FgCategory[],
  options: {
    overview?: boolean
    selectedId?: string
    reducedMotion?: boolean
    viewportWidth?: number
    viewportHeight?: number
  } = {},
) {
  const overview = !!options.overview
  const vw = options.viewportWidth || 960
  const vh = options.viewportHeight || 520
  const aspectScale = Math.max(0.76, Math.min(1, vh / Math.max(1, vw * 0.45)))
  const labelBudget = Math.max(2, Math.min(12, Math.floor(vw / 140)))
  const labelled = new Set(nodes
    .filter(n => n.type === 'INSTRUMENT' || (!overview && n.degree >= 10))
    .sort((a, b) => b.degree - a.degree || a.id.localeCompare(b.id))
    .slice(0, labelBudget).map(n => n.id))
  return {
    type: 'graph' as const,
    layout: 'force' as const,
    top: 52,
    bottom: 38,
    left: 38,
    right: 38,
    // Larger graphs and wide/short viewports start farther out so top clusters & labels never clip.
    zoom: Math.max(0.12, Math.min(0.48, (4.8 * aspectScale) / Math.sqrt(Math.max(1, nodes.length)))),
    scaleLimit: { min: 0.08, max: 3 },
    roam: true,
    draggable: true,
    // Keep node sizes stable when zooming; labels are screen-sized as well.
    nodeScaleRatio: 0,
    categories: categories.map(c => ({ name: c.name, itemStyle: { color: c.color } })),
    data: nodes.map(n => {
      const selected = n.id === options.selectedId
      const cleanedLabel = cleanGraphNodeLabel(n.name)
      return {
        id: n.id, name: n.name,
        category: categories.findIndex(c => c.type === n.type),
        symbolSize: selected ? 32 : Math.min(30, 9 + Math.sqrt(Math.max(0, n.degree)) * 3),
        itemStyle: selected ? { borderColor: '#0f172a', borderWidth: 2 } : undefined,
        label: {
          show: selected || n.type === 'COUNTRY' || labelled.has(n.id),
          formatter: cleanedLabel !== n.name ? cleanedLabel : undefined,
        },
      }
    }),
    links: edges.map(e => ({
      source: e.source, target: e.target, name: e.label, value: 1,
      // Small graphs must not automatically turn into rotated walls of edge text.
      label: { show: false },
      emphasis: { label: { show: false } },
      lineStyle: {
        color: e.approved ? '#16a34a' : '#94a3b8',
        curveness: 0.08, opacity: overview ? 0.45 : 0.7,
      },
    })),
    force: {
      repulsion: 380, edgeLength: [85, 165], gravity: 0.14, friction: 0.6,
      layoutAnimation: !options.reducedMotion,
    },
    edgeSymbol: ['none', 'arrow'],
    edgeSymbolSize: 4,
    edgeLabel: { show: false, fontSize: 13 },
    labelLayout: { hideOverlap: true, moveOverlap: 'shiftY' as const },
    // Preserve normal label visibility on adjacent nodes; tooltip reveals the hovered name.
    emphasis: { focus: 'adjacency' as const, label: { fontSize: 13 } },
    lineStyle: { width: overview ? 0.8 : 1 },
    label: {
      position: 'top' as const, fontSize: 13, color: '#334155',
      overflow: 'break' as const, width: 176,
    },
  }
}
