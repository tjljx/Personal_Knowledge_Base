import type { KnowledgeGraphData } from './api'

export type GraphLayoutMode = 'ring' | 'octopus' | 'diffusion'
export type GraphPoint = { x: number; y: number }
export type GraphGuide = { angle: number; label: string; radius: number }
export type GraphLayout = {
  points: Map<string, GraphPoint>
  guides: GraphGuide[]
  bounds: { minX: number; minY: number; maxX: number; maxY: number }
}

type Node = KnowledgeGraphData['nodes'][number]
type Edge = KnowledgeGraphData['edges'][number]

const TAU = Math.PI * 2
const byRank = (degree: Map<string, number>) => (a: Node, b: Node) =>
  (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0) || a.name.localeCompare(b.name)

function packSector(nodes: Node[], points: Map<string, GraphPoint>, start: number,
                    width: number, startRadius = 190): number {
  let index = 0
  let radius = startRadius
  while (index < nodes.length) {
    const capacity = Math.max(1, Math.floor(radius * width * .84 / 88))
    const count = Math.min(capacity, nodes.length - index)
    for (let j = 0; j < count; j++) {
      const angle = start + width * (.08 + .84 * (j + .5) / count)
      points.set(nodes[index + j].id, { x: radius * Math.cos(angle), y: radius * Math.sin(angle) })
    }
    index += count
    radius += 96
  }
  return radius
}

function diffusionGroups(nodes: Node[], edges: Edge[], degree: Map<string, number>): Node[][] {
  if (!nodes.length) return []
  const hubCount = Math.min(8, Math.max(1, Math.round(Math.sqrt(nodes.length) / 10)))
  const hubs = nodes.slice(0, hubCount)
  const groups = hubs.map(hub => [hub])
  const adjacency = new Map<string, string[]>()
  for (const edge of edges) {
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, [])
    if (!adjacency.has(edge.target)) adjacency.set(edge.target, [])
    adjacency.get(edge.source)!.push(edge.target)
    adjacency.get(edge.target)!.push(edge.source)
  }
  const owner = new Map<string, number>(hubs.map((hub, index) => [hub.id, index]))
  const depth = new Map<string, number>(hubs.map(hub => [hub.id, 0]))
  const queue = hubs.map(hub => hub.id)
  for (let cursor = 0; cursor < queue.length; cursor++) {
    const id = queue[cursor]
    for (const neighbor of adjacency.get(id) ?? []) {
      if (owner.has(neighbor)) continue
      owner.set(neighbor, owner.get(id)!)
      depth.set(neighbor, depth.get(id)! + 1)
      queue.push(neighbor)
    }
  }
  for (const node of nodes.slice(hubCount)) {
    const group = owner.get(node.id) ?? groups.reduce((best, group, index) =>
      group.length < groups[best].length ? index : best, 0)
    groups[group].push(node)
  }
  for (const group of groups) {
    const hub = group.shift()!
    group.sort((a, b) => (depth.get(a.id) ?? 999) - (depth.get(b.id) ?? 999)
      || (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0) || a.name.localeCompare(b.name))
    group.unshift(hub)
  }
  return groups
}

export function createGraphLayout(mode: GraphLayoutMode, nodes: Node[], edges: Edge[],
                                  degree: Map<string, number>): GraphLayout {
  const points = new Map<string, GraphPoint>()
  const guides: GraphGuide[] = []
  const sorted = [...nodes].sort(byRank(degree))
  if (mode === 'ring') {
    let index = 0
    let radius = 175
    let ring = 0
    while (index < sorted.length) {
      const count = Math.min(Math.max(10, Math.floor(TAU * radius / 92)), sorted.length - index)
      for (let j = 0; j < count; j++) {
        const angle = -Math.PI / 2 + TAU * (j + (ring % 2) * .5) / count
        points.set(sorted[index + j].id,
          { x: radius * Math.cos(angle), y: radius * Math.sin(angle) })
      }
      index += count
      radius += 96
      ring++
    }
  } else if (mode === 'octopus') {
    const groups = new Map<string, Node[]>()
    for (const node of sorted) {
      if (!groups.has(node.type)) groups.set(node.type, [])
      groups.get(node.type)!.push(node)
    }
    const entries = [...groups.entries()].sort((a, b) => b[1].length - a[1].length)
    const width = TAU / Math.max(entries.length, 1)
    entries.forEach(([type, group], index) => {
      const start = -Math.PI / 2 + index * width
      const radius = packSector(group, points, start, width)
      guides.push({ angle: start + width / 2, label: type, radius })
    })
  } else {
    const groups = diffusionGroups(sorted, edges, degree)
    const width = TAU / Math.max(groups.length, 1)
    groups.forEach((group, index) => {
      const start = -Math.PI / 2 + index * width
      const center = start + width / 2
      const hub = group.shift()!
      points.set(hub.id, { x: 95 * Math.cos(center), y: 95 * Math.sin(center) })
      const radius = packSector(group, points, start, width, 235)
      guides.push({ angle: center, label: hub.name, radius })
    })
  }
  const xs = [0, ...[...points.values()].map(point => point.x)]
  const ys = [0, ...[...points.values()].map(point => point.y)]
  return { points, guides, bounds: {
    minX: Math.min(...xs) - 150, minY: Math.min(...ys) - 150,
    maxX: Math.max(...xs) + 150, maxY: Math.max(...ys) + 150,
  } }
}
