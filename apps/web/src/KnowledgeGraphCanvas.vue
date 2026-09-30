<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { KnowledgeGraphData } from './api'
import { createGraphLayout, type GraphLayoutMode, type GraphPoint } from './graphLayout'

type GraphNode = KnowledgeGraphData['nodes'][number]
type GraphEdge = KnowledgeGraphData['edges'][number]

const props = defineProps<{ graph: KnowledgeGraphData; processedChunks?: number }>()
const canvas = ref<HTMLCanvasElement | null>(null)
const canvasSize = ref({ width: 1000, height: 700 })
const camera = ref({ x: 0, y: 0, zoom: 1 })
const layoutMode = ref<GraphLayoutMode>('diffusion')
const search = ref('')
const activeType = ref('全部')
const selectedNodeId = ref('')
const selectedEdgeId = ref('')
const hoveredNodeId = ref('')
const hoveredEdgeId = ref('')
let resizeObserver: ResizeObserver | null = null
let drawFrame = 0
let drag: { lastX: number; lastY: number; startX: number; startY: number; moved: boolean } | null = null

const palette: Record<string, string> = {
  人物: '#bc8bff', 组织: '#43c9f6', 地点: '#56d9b4', 产品: '#ffbf73',
  技术: '#67a9ff', 概念: '#f48bb9', 事件: '#9caaff', 其他: '#91a9c8',
}
const colorOf = (type: string): string => palette[type] ?? palette.其他
const nodeById = computed(() => new Map(props.graph.nodes.map(node => [node.id, node])))
const edgeById = computed(() => new Map(props.graph.edges.map(edge => [edge.id, edge])))
const degree = computed(() => {
  const counts = new Map<string, number>()
  for (const edge of props.graph.edges) {
    counts.set(edge.source, (counts.get(edge.source) ?? 0) + 1)
    counts.set(edge.target, (counts.get(edge.target) ?? 0) + 1)
  }
  return counts
})
const types = computed(() => {
  const counts = new Map<string, number>()
  for (const node of props.graph.nodes) counts.set(node.type, (counts.get(node.type) ?? 0) + 1)
  return [...counts.entries()].sort((a, b) => b[1] - a[1])
})
const layout = computed(() => createGraphLayout(layoutMode.value, props.graph.nodes, props.graph.edges, degree.value))
const matchedIds = computed(() => {
  const term = search.value.trim().toLocaleLowerCase()
  return new Set(term ? props.graph.nodes.filter(node => node.name.toLocaleLowerCase().includes(term)).map(node => node.id) : [])
})
const shownIds = computed(() => {
  const term = search.value.trim()
  const ids = new Set(matchedIds.value)
  if (term) for (const edge of props.graph.edges) {
    if (ids.has(edge.source) || ids.has(edge.target)) {
      ids.add(edge.source)
      ids.add(edge.target)
    }
  }
  return new Set(props.graph.nodes.filter(node =>
    (activeType.value === '全部' || node.type === activeType.value) && (!term || ids.has(node.id)),
  ).map(node => node.id))
})
const shownEdges = computed(() => props.graph.edges.filter(edge => shownIds.value.has(edge.source) && shownIds.value.has(edge.target)))
const selectedNode = computed(() => nodeById.value.get(selectedNodeId.value))
const selectedEdge = computed(() => edgeById.value.get(selectedEdgeId.value))
const connectedEdges = computed(() => selectedNode.value
  ? props.graph.edges.filter(edge => edge.source === selectedNodeId.value || edge.target === selectedNodeId.value) : [])
const searchResults = computed(() => props.graph.nodes.filter(node => matchedIds.value.has(node.id)).slice(0, 12))
const nameOf = (id: string): string => nodeById.value.get(id)?.name ?? '未知实体'
const shortName = (name: string): string => name.length > 12 ? `${name.slice(0, 11)}…` : name
const bounds = computed(() => layout.value.bounds)
const fitScale = computed(() => Math.max(.01, Math.min(
  (canvasSize.value.width - 60) / Math.max(1, bounds.value.maxX - bounds.value.minX),
  (canvasSize.value.height - 60) / Math.max(1, bounds.value.maxY - bounds.value.minY),
)))
const scale = computed(() => fitScale.value * camera.value.zoom)
const visibleCount = computed(() => shownIds.value.size)
const selectedOrHoveredNode = computed(() => hoveredNodeId.value || selectedNodeId.value)
const selectedOrHoveredEdge = computed(() => hoveredEdgeId.value || selectedEdgeId.value)

function project(point: GraphPoint): GraphPoint {
  return {
    x: canvasSize.value.width / 2 + (point.x - camera.value.x) * scale.value,
    y: canvasSize.value.height / 2 + (point.y - camera.value.y) * scale.value,
  }
}
function worldAt(x: number, y: number): GraphPoint {
  return { x: camera.value.x + (x - canvasSize.value.width / 2) / scale.value,
           y: camera.value.y + (y - canvasSize.value.height / 2) / scale.value }
}
function screenAt(event: PointerEvent | WheelEvent): GraphPoint {
  const rect = canvas.value!.getBoundingClientRect()
  return { x: event.clientX - rect.left, y: event.clientY - rect.top }
}
function fitGraph(): void {
  camera.value = { x: (bounds.value.minX + bounds.value.maxX) / 2,
                   y: (bounds.value.minY + bounds.value.maxY) / 2, zoom: 1 }
}
function zoomBy(factor: number): void {
  camera.value = { ...camera.value, zoom: Math.min(40, Math.max(.8, camera.value.zoom * factor)) }
}
function focusNode(id: string): void {
  const point = layout.value.points.get(id)
  if (!point) return
  selectedNodeId.value = id
  selectedEdgeId.value = ''
  camera.value = { x: point.x, y: point.y, zoom: Math.max(camera.value.zoom, 7) }
}
function selectNode(id: string): void { selectedNodeId.value = id; selectedEdgeId.value = '' }
function selectEdge(id: string): void { selectedEdgeId.value = id; selectedNodeId.value = '' }
function clearSelection(): void { selectedNodeId.value = ''; selectedEdgeId.value = '' }

function draw(): void {
  const element = canvas.value
  const ctx = element?.getContext('2d')
  if (!element || !ctx) return
  const { width, height } = canvasSize.value
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  const background = ctx.createRadialGradient(width * .48, height * .46, 20,
    width * .48, height * .46, Math.max(width, height) * .75)
  background.addColorStop(0, '#142d49')
  background.addColorStop(.65, '#0b1c32')
  background.addColorStop(1, '#071426')
  ctx.fillStyle = background
  ctx.fillRect(0, 0, width, height)
  ctx.fillStyle = 'rgba(111, 178, 222, .10)'
  for (let x = 24; x < width; x += 38) for (let y = 24; y < height; y += 38) ctx.fillRect(x, y, 1, 1)

  if (layoutMode.value !== 'ring') {
    const origin = project({ x: 0, y: 0 })
    ctx.setLineDash([4, 10])
    for (const guide of layout.value.guides) {
      const end = project({ x: Math.cos(guide.angle) * (guide.radius - 50),
                            y: Math.sin(guide.angle) * (guide.radius - 50) })
      ctx.strokeStyle = 'rgba(99, 185, 228, .16)'
      ctx.beginPath(); ctx.moveTo(origin.x, origin.y); ctx.lineTo(end.x, end.y); ctx.stroke()
    }
    ctx.setLineDash([])
  }

  const focusNode = selectedOrHoveredNode.value
  const focusEdge = selectedOrHoveredEdge.value
  ctx.strokeStyle = 'rgba(107, 184, 221, .20)'
  ctx.lineWidth = 1
  ctx.beginPath()
  for (const edge of shownEdges.value) {
    if (focusNode && (edge.source === focusNode || edge.target === focusNode) || edge.id === focusEdge) continue
    const source = layout.value.points.get(edge.source)
    const target = layout.value.points.get(edge.target)
    if (!source || !target) continue
    const a = project(source); const b = project(target)
    ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y)
  }
  ctx.stroke()
  for (const edge of shownEdges.value) {
    if (!(focusNode && (edge.source === focusNode || edge.target === focusNode) || edge.id === focusEdge)) continue
    const source = layout.value.points.get(edge.source)
    const target = layout.value.points.get(edge.target)
    if (!source || !target) continue
    const a = project(source); const b = project(target)
    ctx.strokeStyle = edge.id === focusEdge ? '#a3eaff' : 'rgba(111, 215, 244, .75)'
    ctx.lineWidth = edge.id === focusEdge ? 2.6 : 1.6
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke()
    if (edge.id === focusEdge) {
      ctx.fillStyle = '#e0f7ff'; ctx.font = '600 12px Inter, sans-serif'
      ctx.fillText(edge.relation, (a.x + b.x) / 2 + 5, (a.y + b.y) / 2 - 7)
    }
  }

  const core = project({ x: 0, y: 0 })
  const coreRadius = Math.min(48, Math.max(12, 58 * scale.value))
  ctx.shadowColor = '#2d9ddc'; ctx.shadowBlur = 16
  ctx.fillStyle = '#103b60'; ctx.strokeStyle = 'rgba(126, 216, 250, .75)'; ctx.lineWidth = 1.4
  ctx.beginPath(); ctx.arc(core.x, core.y, coreRadius, 0, Math.PI * 2); ctx.fill(); ctx.stroke()
  ctx.shadowBlur = 0
  if (coreRadius >= 28) {
    ctx.fillStyle = '#e3f8ff'; ctx.font = '700 13px Inter, sans-serif'
    ctx.textAlign = 'center'; ctx.fillText('知识网络', core.x, core.y + 4); ctx.textAlign = 'left'
  }

  const radius = Math.max(2, Math.min(16, 14 * scale.value))
  const showLabels = scale.value >= .55
  for (const node of props.graph.nodes) {
    if (!shownIds.value.has(node.id)) continue
    const point = layout.value.points.get(node.id)
    if (!point) continue
    const pos = project(point)
    if (pos.x < -65 || pos.y < -65 || pos.x > width + 65 || pos.y > height + 65) continue
    const focused = node.id === focusNode || matchedIds.value.has(node.id)
    ctx.fillStyle = focused ? colorOf(node.type) : '#102d49'
    ctx.strokeStyle = colorOf(node.type)
    ctx.globalAlpha = focused ? 1 : .82
    ctx.lineWidth = focused ? 2.4 : 1.3
    if (focused) { ctx.shadowColor = colorOf(node.type); ctx.shadowBlur = 17 }
    ctx.beginPath(); ctx.arc(pos.x, pos.y, focused ? radius + 3 : radius, 0, Math.PI * 2)
    ctx.fill(); ctx.stroke(); ctx.shadowBlur = 0; ctx.globalAlpha = 1
    if (showLabels || focused) {
      ctx.fillStyle = '#e5f5ff'; ctx.font = focused ? '700 12px Inter, sans-serif' : '11px Inter, sans-serif'
      ctx.textAlign = 'center'; ctx.fillText(shortName(node.name), pos.x, pos.y + radius + 17)
    }
  }
  ctx.textAlign = 'left'
  if (!visibleCount.value) {
    ctx.textAlign = 'center'; ctx.fillStyle = '#d3eafb'; ctx.font = '700 18px Inter, sans-serif'
    ctx.fillText('暂无匹配的实体', width / 2, height / 2)
  }
}
function scheduleDraw(): void {
  if (drawFrame) return
  drawFrame = requestAnimationFrame(() => { drawFrame = 0; draw() })
}
function resizeCanvas(): void {
  const element = canvas.value
  if (!element) return
  const width = Math.max(1, element.clientWidth)
  const height = Math.max(1, element.clientHeight)
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  element.width = Math.round(width * dpr)
  element.height = Math.round(height * dpr)
  canvasSize.value = { width, height }
  scheduleDraw()
}
function hitTest(x: number, y: number): { node?: string; edge?: string } {
  const radius = Math.max(9, Math.min(22, 14 * scale.value + 7))
  for (const node of props.graph.nodes) {
    if (!shownIds.value.has(node.id)) continue
    const point = layout.value.points.get(node.id)
    if (!point) continue
    const pos = project(point)
    if (Math.hypot(pos.x - x, pos.y - y) <= radius) return { node: node.id }
  }
  let nearest = 7
  let edgeId = ''
  for (const edge of shownEdges.value) {
    const source = layout.value.points.get(edge.source)
    const target = layout.value.points.get(edge.target)
    if (!source || !target) continue
    const a = project(source); const b = project(target)
    const dx = b.x - a.x; const dy = b.y - a.y
    const t = Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / (dx * dx + dy * dy || 1)))
    const distance = Math.hypot(x - (a.x + dx * t), y - (a.y + dy * t))
    if (distance < nearest) { nearest = distance; edgeId = edge.id }
  }
  return edgeId ? { edge: edgeId } : {}
}
function onPointerDown(event: PointerEvent): void {
  if (event.button !== 0) return
  canvas.value?.setPointerCapture(event.pointerId)
  drag = { startX: event.clientX, startY: event.clientY,
           lastX: event.clientX, lastY: event.clientY, moved: false }
}
function onPointerMove(event: PointerEvent): void {
  if (drag) {
    const dx = event.clientX - drag.lastX; const dy = event.clientY - drag.lastY
    drag.moved ||= Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 4
    if (drag.moved) camera.value = { ...camera.value,
      x: camera.value.x - dx / scale.value, y: camera.value.y - dy / scale.value }
    drag.lastX = event.clientX; drag.lastY = event.clientY
    return
  }
  const point = screenAt(event)
  const hit = hitTest(point.x, point.y)
  hoveredNodeId.value = hit.node ?? ''
  hoveredEdgeId.value = hit.edge ?? ''
  if (canvas.value) canvas.value.style.cursor = hit.node || hit.edge ? 'pointer' : 'grab'
}
function onPointerUp(event: PointerEvent): void {
  if (!drag) return
  const moved = drag.moved
  drag = null
  if (moved) return
  const point = screenAt(event)
  const hit = hitTest(point.x, point.y)
  if (hit.node) selectNode(hit.node)
  else if (hit.edge) selectEdge(hit.edge)
  else clearSelection()
}
function onWheel(event: WheelEvent): void {
  event.preventDefault()
  const point = screenAt(event)
  const anchor = worldAt(point.x, point.y)
  const zoom = Math.min(40, Math.max(.8, camera.value.zoom * Math.exp(-event.deltaY * .001)))
  const nextScale = fitScale.value * zoom
  camera.value = { zoom, x: anchor.x - (point.x - canvasSize.value.width / 2) / nextScale,
                   y: anchor.y - (point.y - canvasSize.value.height / 2) / nextScale }
}

watch([layout, camera, canvasSize, shownIds, selectedNodeId, selectedEdgeId,
  hoveredNodeId, hoveredEdgeId], scheduleDraw)
watch(layoutMode, () => nextTick(fitGraph))
watch(() => props.graph, () => { clearSelection(); nextTick(fitGraph) })
onMounted(() => {
  resizeObserver = new ResizeObserver(resizeCanvas)
  if (canvas.value) resizeObserver.observe(canvas.value)
  resizeCanvas()
  fitGraph()
})
onUnmounted(() => { resizeObserver?.disconnect(); if (drawFrame) cancelAnimationFrame(drawFrame) })
</script>

<template>
  <div class="kg-shell">
    <div class="kg-header">
      <div class="kg-identity"><span class="kg-mark">✧</span><div><span class="kg-eyebrow">KNOWLEDGE INTELLIGENCE</span><h4>知识关系网络</h4></div></div>
      <div class="kg-stats"><div><strong>{{ graph.nodes.length.toLocaleString() }}</strong><span>实体</span></div><i></i><div><strong>{{ graph.edges.length.toLocaleString() }}</strong><span>关系</span></div><i></i><div><strong>{{ processedChunks ?? graph.processed_chunks }}</strong><span>已抽取片段</span></div></div>
    </div>
    <div class="kg-controls">
      <label class="kg-search"><span>⌕</span><input v-model="search" placeholder="搜索实体并定位" aria-label="搜索知识图谱实体" /><button v-if="search" type="button" aria-label="清空搜索" @click="search = ''">×</button></label>
      <div class="kg-layouts" role="group" aria-label="图谱布局">
        <button v-for="item in ([['diffusion', '扩散图'], ['octopus', '八爪鱼'], ['ring', '同心环']] as const)" :key="item[0]" type="button" :class="{ active: layoutMode === item[0] }" :aria-pressed="layoutMode === item[0]" @click="layoutMode = item[0]">{{ item[1] }}</button>
      </div>
      <div class="kg-filters"><button type="button" :class="{ active: activeType === '全部' }" @click="activeType = '全部'">全部</button><button v-for="[type, count] in types" :key="type" type="button" :class="{ active: activeType === type }" @click="activeType = type"><span class="kg-filter-dot" :style="{ background: colorOf(type) }"></span>{{ type }} <small>{{ count }}</small></button></div>
    </div>
    <div class="kg-body">
      <div class="kg-visual">
        <div class="kg-canvas-caption"><span class="kg-live-dot"></span> ENTITY NETWORK <span class="kg-caption-divider">/</span> {{ layoutMode === 'diffusion' ? '扩散图' : layoutMode === 'octopus' ? '八爪鱼' : '同心环' }}</div>
        <canvas ref="canvas" role="img" aria-label="完整知识图谱画布，可拖动和滚轮缩放" @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="drag = null" @pointerleave="hoveredNodeId = ''; hoveredEdgeId = ''" @wheel="onWheel" />
        <div class="kg-zoom"><button type="button" aria-label="放大图谱" @click="zoomBy(1.6)">+</button><button type="button" aria-label="缩小图谱" @click="zoomBy(1 / 1.6)">−</button><button type="button" @click="fitGraph">适应</button></div>
        <div class="kg-canvas-footer"><span>同一画布展示 {{ visibleCount.toLocaleString() }} / {{ graph.nodes.length.toLocaleString() }} 个实体</span><span>滚轮缩放 · 拖动平移 · 点击查看证据</span></div>
      </div>
      <aside class="kg-inspector">
        <template v-if="selectedEdge">
          <div class="kg-detail-label"><span class="kg-live-dot"></span> RELATION EVIDENCE</div>
          <div class="kg-entity-pair"><span>{{ nameOf(selectedEdge.source) }}</span><b>→</b><span>{{ nameOf(selectedEdge.target) }}</span></div>
          <div class="kg-relation-tag">{{ selectedEdge.relation }}</div>
          <div class="kg-section-caption">原文证据 <span>{{ selectedEdge.evidence.length }}</span></div>
          <article v-for="(item, index) in selectedEdge.evidence" :key="`${item.chunk_id}-${index}`" class="kg-evidence"><div class="kg-evidence-number">{{ String(index + 1).padStart(2, '0') }}</div><div><strong>{{ item.document_title }}</strong><small>版本 v{{ item.version_no }}<template v-if="item.page_no"> · 第 {{ item.page_no }} 页</template></small><p>{{ item.quote }}</p></div></article>
        </template>
        <template v-else-if="selectedNode">
          <div class="kg-detail-label"><span class="kg-live-dot"></span> ENTITY PROFILE</div>
          <div class="kg-profile-icon" :style="{ '--node-color': colorOf(selectedNode.type) }">{{ selectedNode.type.slice(0, 1) }}</div>
          <h3>{{ selectedNode.name }}</h3><div class="kg-profile-meta"><span>{{ selectedNode.type }}</span><span>{{ connectedEdges.length }} 条关系</span></div>
          <div class="kg-section-caption">相关连接</div>
          <button v-for="edge in connectedEdges" :key="edge.id" type="button" class="kg-relation-row" @click="selectEdge(edge.id)"><span>{{ nameOf(edge.source) }}</span><b>{{ edge.relation }}</b><span>{{ nameOf(edge.target) }}</span><em>↗</em></button>
        </template>
        <template v-else>
          <div class="kg-detail-label"><span class="kg-live-dot"></span> GRAPH EXPLORER</div>
          <div v-if="searchResults.length" class="kg-search-results"><h3>搜索结果</h3><button v-for="node in searchResults" :key="node.id" type="button" @click="focusNode(node.id)"><span class="kg-filter-dot" :style="{ background: colorOf(node.type) }"></span>{{ node.name }}<small>{{ node.type }}</small></button></div>
          <template v-else><div class="kg-guide-icon">⌘</div><h3>探索知识之间的联系</h3><p class="kg-guide-text">切换扩散图、八爪鱼或同心环。滚轮缩放、拖动画布；搜索实体可直接定位。点击节点或关系后在这里核对来源。</p></template>
          <div class="kg-section-caption">实体分类</div>
          <div class="kg-legend"><div v-for="[type, count] in types" :key="type"><span class="kg-filter-dot" :style="{ background: colorOf(type) }"></span><span>{{ type }}</span><strong>{{ count }}</strong></div></div>
        </template>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.kg-shell{overflow:hidden;border:1px solid #173755;border-radius:20px;background:#09182b;color:#e8f4ff;box-shadow:0 22px 45px rgba(8,30,55,.18)}
.kg-header{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:22px 26px;border-bottom:1px solid rgba(133,194,229,.14);background:linear-gradient(105deg,#102a47,#0b1d34 70%)}
.kg-identity{display:flex;align-items:center;gap:13px}.kg-mark{display:grid;place-items:center;width:42px;height:42px;border:1px solid #3987b4;border-radius:12px;background:#123c60;color:#9ce8ff;font-size:27px;box-shadow:inset 0 0 18px #2175a84d}.kg-eyebrow,.kg-detail-label{color:#62b8dd;font-size:10px;font-weight:800;letter-spacing:.2em}.kg-identity h4{margin:5px 0 0;font-size:18px;letter-spacing:.03em}.kg-stats{display:flex;align-items:center;gap:20px}.kg-stats>div{display:grid;gap:2px}.kg-stats strong{font-size:23px;line-height:1;font-variant-numeric:tabular-nums}.kg-stats span{color:#88a8bf;font-size:11px}.kg-stats i{width:1px;height:26px;background:#36516b}
.kg-controls{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:13px 20px;border-bottom:1px solid rgba(133,194,229,.12);background:#0b1d32}.kg-search{display:flex;align-items:center;gap:8px;flex:0 1 260px;min-width:190px;height:37px;padding:0 12px;border:1px solid #2b4c69;border-radius:9px;background:#0c253e}.kg-search:focus-within{border-color:#69c5ed;box-shadow:0 0 0 3px #49a7dc24}.kg-search>span{color:#77b9db;font-size:21px}.kg-search input{width:100%;border:0;outline:0;background:transparent;color:#eff8ff;font-size:12px}.kg-search input::placeholder{color:#7795aa}.kg-search button{border:0;background:none;color:#a9c5d9;cursor:pointer;font-size:18px}.kg-layouts{display:flex;gap:3px;padding:3px;border:1px solid #294863;border-radius:9px;background:#0a1a2e}.kg-layouts button{padding:7px 11px;border:0;border-radius:6px;background:transparent;color:#86a8be;cursor:pointer;font-size:11px;white-space:nowrap}.kg-layouts button.active,.kg-layouts button:hover{background:#1a5271;color:#e2f8ff}.kg-filters{display:flex;flex:1;gap:4px;overflow:auto;scrollbar-width:thin}.kg-filters button{display:flex;align-items:center;gap:6px;flex:none;padding:8px 9px;border:1px solid transparent;border-radius:8px;background:transparent;color:#89a8be;cursor:pointer;font-size:11px;white-space:nowrap}.kg-filters button:hover,.kg-filters button.active{border-color:#3c7195;background:#123955;color:#d7f4ff}.kg-filters small{color:#6c98b3;font-size:10px}.kg-filter-dot{display:inline-block;flex:none;width:7px;height:7px;border-radius:50%;box-shadow:0 0 8px currentColor}
.kg-body{display:grid;grid-template-columns:minmax(0,1fr) 315px;min-height:690px}.kg-visual{position:relative;min-width:0;background:#09182b}.kg-visual canvas{display:block;width:100%;height:690px;touch-action:none;cursor:grab}.kg-visual canvas:active{cursor:grabbing}.kg-canvas-caption{position:absolute;top:17px;left:22px;pointer-events:none;color:#73b7d7;font-size:10px;font-weight:700;letter-spacing:.15em}.kg-caption-divider{margin:0 8px;color:#427390}.kg-live-dot{display:inline-block;width:6px;height:6px;margin-right:8px;border-radius:50%;background:#5be4d5;box-shadow:0 0 9px #5be4d5}.kg-zoom{position:absolute;right:18px;bottom:54px;display:flex;gap:5px}.kg-zoom button{min-width:29px;height:29px;padding:0 7px;border:1px solid #3a6a85;border-radius:7px;background:#123b55;color:#d9f5ff;cursor:pointer;font-size:13px}.kg-zoom button:hover{background:#1b5a76}.kg-canvas-footer{position:absolute;right:18px;bottom:14px;left:18px;display:flex;justify-content:space-between;gap:12px;color:#93b4ca;font-size:10px;pointer-events:none}
.kg-inspector{min-width:0;max-height:690px;overflow:auto;padding:24px 22px;border-left:1px solid rgba(133,194,229,.14);background:linear-gradient(180deg,#0d233b,#0a1b2f);scrollbar-color:#335776 transparent}.kg-inspector h3{margin:17px 0 8px;font-size:18px;line-height:1.4;overflow-wrap:anywhere}.kg-entity-pair{display:grid;gap:7px;margin-top:27px}.kg-entity-pair span{font-size:17px;font-weight:700;overflow-wrap:anywhere}.kg-entity-pair b{color:#65c8ec;font-size:18px}.kg-relation-tag{display:inline-block;margin-top:19px;padding:7px 12px;border:1px solid #397aa0;border-radius:7px;background:#164366;color:#b9edff;font-size:12px;font-weight:700}.kg-section-caption{display:flex;justify-content:space-between;align-items:center;margin:30px 0 12px;padding-bottom:10px;border-bottom:1px solid #24405a;color:#8eb7cd;font-size:11px;font-weight:700;letter-spacing:.08em}.kg-section-caption span{color:#6fd5f3}.kg-evidence{display:flex;gap:12px;padding:15px 0;border-bottom:1px solid #1e3851}.kg-evidence-number{color:#4ebbdc;font-size:11px;font-weight:800}.kg-evidence strong{display:block;color:#e6f3ff;font-size:12px;line-height:1.5;overflow-wrap:anywhere}.kg-evidence small{display:block;margin:5px 0 9px;color:#78a3bb;font-size:10px}.kg-evidence p{margin:0;padding:10px 11px;border-left:2px solid #3ea7ce;border-radius:0 6px 6px 0;background:#11304b;color:#accfe2;font-size:11px;line-height:1.7;overflow-wrap:anywhere}.kg-profile-icon{display:grid;place-items:center;width:55px;height:55px;margin-top:26px;border:1px solid var(--node-color);border-radius:15px;background:#143652;color:var(--node-color);font-size:22px;font-weight:800}.kg-profile-meta{display:flex;gap:8px}.kg-profile-meta span{padding:5px 9px;border-radius:5px;background:#173650;color:#8fcce8;font-size:10px}.kg-relation-row{display:grid;grid-template-columns:1fr auto 1fr auto;align-items:center;gap:7px;width:100%;padding:12px 0;border:0;border-bottom:1px solid #1f3b54;background:none;color:#c0ddec;text-align:left;cursor:pointer;font-size:11px}.kg-relation-row:hover{color:#fff}.kg-relation-row span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.kg-relation-row b{color:#69cee9;font-weight:600}.kg-relation-row em{color:#69cee9;font-style:normal}.kg-guide-icon{display:grid;place-items:center;width:62px;height:62px;margin-top:36px;border:1px solid #3a779b;border-radius:17px;background:#123c5a;color:#82d8f4;font-size:30px}.kg-guide-text{color:#94b4c7;font-size:12px;line-height:1.8}.kg-legend{display:grid;gap:12px}.kg-legend>div{display:flex;align-items:center;gap:9px;color:#a9c8d9;font-size:11px}.kg-legend strong{margin-left:auto;color:#dcefff;font-variant-numeric:tabular-nums}.kg-search-results{display:grid;gap:4px}.kg-search-results button{display:flex;align-items:center;gap:8px;width:100%;padding:9px 5px;border:0;border-bottom:1px solid #23435a;background:transparent;color:#d1e9f7;text-align:left;cursor:pointer;font-size:11px}.kg-search-results button:hover{color:#fff;background:#153a55}.kg-search-results small{margin-left:auto;color:#7aa7bd}
@media(max-width:1120px){.kg-body{grid-template-columns:minmax(0,1fr) 270px}.kg-filters{flex-basis:100%}}@media(max-width:850px){.kg-header{align-items:flex-start;flex-direction:column}.kg-body{grid-template-columns:1fr}.kg-inspector{max-height:420px;border-top:1px solid rgba(133,194,229,.14);border-left:0}.kg-visual canvas{height:560px}.kg-canvas-footer{font-size:9px}}@media(max-width:540px){.kg-header{padding:18px}.kg-stats{gap:12px}.kg-stats strong{font-size:19px}.kg-search{flex-basis:100%}.kg-controls{padding:12px}.kg-layouts{width:100%}.kg-layouts button{flex:1}.kg-canvas-footer span:last-child{display:none}.kg-visual canvas{height:480px}}
</style>
