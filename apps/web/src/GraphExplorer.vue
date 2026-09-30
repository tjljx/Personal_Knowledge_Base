<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { KnowledgeGraphData } from './api'
import { createGraphLayout, type GraphLayoutMode, type GraphPoint } from './graphExplorerLayout'

type Node = KnowledgeGraphData['nodes'][number]
type Edge = KnowledgeGraphData['edges'][number]
type Scope = 'core' | 'focus' | 'all'

const props = defineProps<{ graph: KnowledgeGraphData; processedChunks?: number }>()
const canvas = ref<HTMLCanvasElement | null>(null)
const size = ref({ width: 900, height: 620 })
const camera = ref({ x: 0, y: 0, zoom: 1 })
const scope = ref<Scope>('core')
const mode = ref<GraphLayoutMode>('diffusion')
const query = ref('')
const activeType = ref('全部')
const selectedNodeId = ref('')
const selectedEdgeId = ref('')
const hoveredNodeId = ref('')
const hoveredEdgeId = ref('')
let observer: ResizeObserver | undefined
let frame = 0
let drag: { x: number; y: number; lastX: number; lastY: number; moved: boolean } | null = null

const colors: Record<string, string> = {
  人物: '#8b65c8', 组织: '#2386b8', 地点: '#27a387', 产品: '#d58a31',
  技术: '#4d73d2', 概念: '#c86191', 事件: '#8b80b8', 其他: '#75889c',
}
const colorOf = (type: string) => colors[type] ?? colors.其他
const byId = computed(() => new Map(props.graph.nodes.map(node => [node.id, node])))
const edgeById = computed(() => new Map(props.graph.edges.map(edge => [edge.id, edge])))
const degree = computed(() => {
  const value = new Map<string, number>()
  for (const edge of props.graph.edges) {
    value.set(edge.source, (value.get(edge.source) ?? 0) + 1)
    value.set(edge.target, (value.get(edge.target) ?? 0) + 1)
  }
  return value
})
const ranked = computed(() => [...props.graph.nodes].sort((a, b) =>
  (degree.value.get(b.id) ?? 0) - (degree.value.get(a.id) ?? 0) || a.name.localeCompare(b.name)))
const types = computed(() => {
  const counts = new Map<string, number>()
  for (const node of props.graph.nodes) counts.set(node.type, (counts.get(node.type) ?? 0) + 1)
  return [...counts].sort((a, b) => b[1] - a[1])
})
const matches = computed(() => {
  const term = query.value.trim().toLocaleLowerCase()
  return term ? props.graph.nodes.filter(node => node.name.toLocaleLowerCase().includes(term))
    .sort((a, b) => (degree.value.get(b.id) ?? 0) - (degree.value.get(a.id) ?? 0)).slice(0, 12) : []
})
const selectedNode = computed(() => byId.value.get(selectedNodeId.value))
const selectedEdge = computed(() => edgeById.value.get(selectedEdgeId.value))
const connectedEdges = computed(() => props.graph.edges.filter(edge =>
  edge.source === selectedNodeId.value || edge.target === selectedNodeId.value))
const coreIds = computed(() => {
  const hubs = new Set(ranked.value.slice(0, 16).map(node => node.id))
  const ids = new Set(hubs)
  for (const edge of props.graph.edges) {
    if (hubs.has(edge.source)) ids.add(edge.target)
    if (hubs.has(edge.target)) ids.add(edge.source)
  }
  return new Set(ranked.value.filter(node => ids.has(node.id)).slice(0, 100).map(node => node.id))
})
const focusIds = computed(() => {
  const id = selectedNodeId.value
  if (!id) return coreIds.value
  const ids = new Set([id])
  for (const edge of connectedEdges.value) ids.add(edge.source === id ? edge.target : edge.source)
  const nearest = ranked.value.filter(node => ids.has(node.id)).slice(0, 110)
  return new Set([id, ...nearest.map(node => node.id)])
})
const visibleNodes = computed(() => {
  const ids = scope.value === 'all' ? null : scope.value === 'focus' ? focusIds.value : coreIds.value
  return props.graph.nodes.filter(node => (!ids || ids.has(node.id)) &&
    (activeType.value === '全部' || node.type === activeType.value || node.id === selectedNodeId.value))
})
const visibleIds = computed(() => new Set(visibleNodes.value.map(node => node.id)))
const visibleEdges = computed(() => props.graph.edges.filter(edge =>
  visibleIds.value.has(edge.source) && visibleIds.value.has(edge.target)))
const visibleDegree = computed(() => {
  const value = new Map<string, number>()
  for (const edge of visibleEdges.value) {
    value.set(edge.source, (value.get(edge.source) ?? 0) + 1)
    value.set(edge.target, (value.get(edge.target) ?? 0) + 1)
  }
  return value
})
const labelIds = computed(() => new Set([...visibleNodes.value]
  .sort((a, b) => (visibleDegree.value.get(b.id) ?? 0) - (visibleDegree.value.get(a.id) ?? 0))
  .slice(0, scope.value === 'focus' ? 16 : 14).map(node => node.id)))
const layout = computed(() => {
  if (scope.value !== 'focus' || !selectedNodeId.value) {
    return createGraphLayout(mode.value, visibleNodes.value, visibleEdges.value, visibleDegree.value)
  }
  const points = new Map<string, GraphPoint>([[selectedNodeId.value, { x: 0, y: 0 }]])
  const neighbors = visibleNodes.value.filter(node => node.id !== selectedNodeId.value)
    .sort((a, b) => (visibleDegree.value.get(b.id) ?? 0) - (visibleDegree.value.get(a.id) ?? 0))
  neighbors.forEach((node, index) => {
    const ring = Math.floor(index / 24)
    const start = ring * 24
    const count = Math.min(24, neighbors.length - start)
    const angle = -Math.PI / 2 + Math.PI * 2 * ((index - start) + (ring % 2) * .5) / count
    const radius = 205 + ring * 120
    points.set(node.id, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius })
  })
  const radius = 205 + Math.floor(Math.max(0, neighbors.length - 1) / 24) * 120 + 110
  return { points, guides: [], bounds: { minX: -radius, minY: -radius, maxX: radius, maxY: radius } }
})
const fitScale = computed(() => Math.max(.015, Math.min(
  (size.value.width - 86) / Math.max(1, layout.value.bounds.maxX - layout.value.bounds.minX),
  (size.value.height - 86) / Math.max(1, layout.value.bounds.maxY - layout.value.bounds.minY),
)))
const scale = computed(() => fitScale.value * camera.value.zoom)
const nameOf = (id: string) => byId.value.get(id)?.name ?? '未知实体'
const shortName = (name: string) => name.length > 10 ? `${name.slice(0, 9)}…` : name
const project = (point: GraphPoint): GraphPoint => ({
  x: size.value.width / 2 + (point.x - camera.value.x) * scale.value,
  y: size.value.height / 2 + (point.y - camera.value.y) * scale.value,
})
const at = (event: PointerEvent | WheelEvent): GraphPoint => {
  const rect = canvas.value!.getBoundingClientRect()
  return { x: event.clientX - rect.left, y: event.clientY - rect.top }
}

function fit(): void {
  camera.value = { x: (layout.value.bounds.minX + layout.value.bounds.maxX) / 2,
    y: (layout.value.bounds.minY + layout.value.bounds.maxY) / 2, zoom: 1 }
}
function setScope(next: Scope): void { scope.value = next; nextTick(fit) }
function selectNode(id: string): void {
  selectedNodeId.value = id
  selectedEdgeId.value = ''
  scope.value = 'focus'
  nextTick(fit)
}
function selectEdge(id: string): void { selectedEdgeId.value = id }
function changeMode(next: GraphLayoutMode): void { mode.value = next; if (scope.value === 'focus') scope.value = 'core'; nextTick(fit) }
function clearSelection(): void { selectedNodeId.value = ''; selectedEdgeId.value = ''; setScope('core') }
function zoom(factor: number): void { camera.value = { ...camera.value, zoom: Math.max(.7, Math.min(30, camera.value.zoom * factor)) } }

function draw(): void {
  const ctx = canvas.value?.getContext('2d')
  if (!ctx) return
  const { width, height } = size.value
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = '#f8fbfd'
  ctx.fillRect(0, 0, width, height)
  ctx.fillStyle = '#e4edf3'
  for (let x = 20; x < width; x += 28) for (let y = 20; y < height; y += 28) ctx.fillRect(x, y, 1.2, 1.2)

  const focus = hoveredNodeId.value || selectedNodeId.value
  const edgeFocus = hoveredEdgeId.value || selectedEdgeId.value
  for (const edge of visibleEdges.value) {
    const a = layout.value.points.get(edge.source)
    const b = layout.value.points.get(edge.target)
    if (!a || !b) continue
    const start = project(a); const end = project(b)
    if (Math.max(start.x, end.x) < -20 || Math.min(start.x, end.x) > width + 20 ||
      Math.max(start.y, end.y) < -20 || Math.min(start.y, end.y) > height + 20) continue
    const highlighted = edge.id === edgeFocus || !!focus && (edge.source === focus || edge.target === focus)
    ctx.strokeStyle = highlighted ? '#409fc0' : scope.value === 'all' ? 'rgba(93,125,151,.12)' : 'rgba(83,121,149,.24)'
    ctx.lineWidth = highlighted ? 2 : 1
    ctx.beginPath(); ctx.moveTo(start.x, start.y); ctx.lineTo(end.x, end.y); ctx.stroke()
    if (edge.id === edgeFocus && scale.value > .12) {
      const x = (start.x + end.x) / 2; const y = (start.y + end.y) / 2 - 9
      ctx.font = '600 11px sans-serif'; ctx.textAlign = 'center'
      const label = edge.relation.slice(0, 22)
      const w = ctx.measureText(label).width + 16
      ctx.fillStyle = '#ffffff'; ctx.fillRect(x - w / 2, y - 12, w, 19)
      ctx.fillStyle = '#246987'; ctx.fillText(label, x, y + 2)
    }
  }

  const occupied = new Set<string>()
  const nodes = [...visibleNodes.value].sort((a, b) => (visibleDegree.value.get(a.id) ?? 0) - (visibleDegree.value.get(b.id) ?? 0))
  for (const node of nodes) {
    const point = layout.value.points.get(node.id)
    if (!point) continue
    const pos = project(point)
    if (pos.x < -55 || pos.y < -55 || pos.x > width + 55 || pos.y > height + 55) continue
    const highlighted = node.id === focus || node.id === selectedNodeId.value
    const hub = (visibleDegree.value.get(node.id) ?? 0) >= 4
    const radius = highlighted ? 13 : scope.value === 'all' ? 3.2 : hub ? 8.5 : 6.5
    if (highlighted) {
      ctx.fillStyle = `${colorOf(node.type)}20`
      ctx.beginPath(); ctx.arc(pos.x, pos.y, radius + 9, 0, Math.PI * 2); ctx.fill()
    }
    ctx.fillStyle = colorOf(node.type)
    ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 2
    ctx.beginPath(); ctx.arc(pos.x, pos.y, radius, 0, Math.PI * 2); ctx.fill(); ctx.stroke()
    const showLabel = highlighted || scope.value !== 'all' && labelIds.value.has(node.id)
    if (!showLabel || scale.value < .25 && !highlighted) continue
    const label = shortName(node.name)
    ctx.font = highlighted ? '700 12px sans-serif' : '600 11px sans-serif'
    const textWidth = ctx.measureText(label).width
    const key = `${Math.floor(pos.x / 75)}:${Math.floor((pos.y + radius + 19) / 25)}`
    if (occupied.has(key) && !highlighted) continue
    occupied.add(key)
    ctx.fillStyle = highlighted ? '#173d57' : '#50667b'
    ctx.textAlign = 'center'; ctx.fillText(label, pos.x, pos.y + radius + 18, Math.max(80, textWidth))
  }
  if (!visibleNodes.value.length) {
    ctx.fillStyle = '#526b7f'; ctx.font = '600 15px sans-serif'; ctx.textAlign = 'center'
    ctx.fillText('当前筛选下没有实体', width / 2, height / 2)
  }
}
function schedule(): void { if (!frame) frame = requestAnimationFrame(() => { frame = 0; draw() }) }
function resize(): void {
  if (!canvas.value) return
  const width = Math.max(1, canvas.value.clientWidth)
  const height = Math.max(1, canvas.value.clientHeight)
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  canvas.value.width = Math.round(width * dpr); canvas.value.height = Math.round(height * dpr)
  size.value = { width, height }; schedule()
}
function hitTest(x: number, y: number): { node?: string; edge?: string } {
  for (const node of visibleNodes.value) {
    const point = layout.value.points.get(node.id)
    if (point && Math.hypot(project(point).x - x, project(point).y - y) < Math.max(9, 10 * scale.value)) return { node: node.id }
  }
  if (scope.value === 'all' && visibleEdges.value.length > 1000) return {}
  let nearest = 6; let result = ''
  for (const edge of visibleEdges.value) {
    const a = layout.value.points.get(edge.source); const b = layout.value.points.get(edge.target)
    if (!a || !b) continue
    const start = project(a); const end = project(b)
    const dx = end.x - start.x; const dy = end.y - start.y
    const t = Math.max(0, Math.min(1, ((x - start.x) * dx + (y - start.y) * dy) / (dx * dx + dy * dy || 1)))
    const distance = Math.hypot(x - start.x - dx * t, y - start.y - dy * t)
    if (distance < nearest) { nearest = distance; result = edge.id }
  }
  return result ? { edge: result } : {}
}
function pointerDown(event: PointerEvent): void {
  if (event.button !== 0) return
  canvas.value?.setPointerCapture(event.pointerId)
  drag = { x: event.clientX, y: event.clientY, lastX: event.clientX, lastY: event.clientY, moved: false }
}
function pointerMove(event: PointerEvent): void {
  if (drag) {
    const dx = event.clientX - drag.lastX; const dy = event.clientY - drag.lastY
    drag.moved ||= Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 4
    if (drag.moved) camera.value = { ...camera.value, x: camera.value.x - dx / scale.value, y: camera.value.y - dy / scale.value }
    drag.lastX = event.clientX; drag.lastY = event.clientY
    return
  }
  const point = at(event); const hit = hitTest(point.x, point.y)
  hoveredNodeId.value = hit.node ?? ''; hoveredEdgeId.value = hit.edge ?? ''
  if (canvas.value) canvas.value.style.cursor = hit.node || hit.edge ? 'pointer' : 'grab'
}
function pointerUp(event: PointerEvent): void {
  if (!drag) return
  const moved = drag.moved; drag = null
  if (moved) return
  const point = at(event); const hit = hitTest(point.x, point.y)
  if (hit.node) selectNode(hit.node)
  else if (hit.edge) selectEdge(hit.edge)
}
function wheel(event: WheelEvent): void {
  event.preventDefault()
  const point = at(event)
  const world = { x: camera.value.x + (point.x - size.value.width / 2) / scale.value,
    y: camera.value.y + (point.y - size.value.height / 2) / scale.value }
  const nextZoom = Math.max(.7, Math.min(30, camera.value.zoom * Math.exp(-event.deltaY * .001)))
  const nextScale = fitScale.value * nextZoom
  camera.value = { zoom: nextZoom, x: world.x - (point.x - size.value.width / 2) / nextScale,
    y: world.y - (point.y - size.value.height / 2) / nextScale }
}
watch([layout, camera, size, hoveredNodeId, hoveredEdgeId, selectedNodeId, selectedEdgeId], schedule)
watch([scope, mode, activeType], () => nextTick(fit))
watch(() => props.graph, () => { selectedNodeId.value = ''; selectedEdgeId.value = ''; scope.value = 'core'; nextTick(fit) })
onMounted(() => { observer = new ResizeObserver(resize); if (canvas.value) observer.observe(canvas.value); resize(); fit() })
onUnmounted(() => { observer?.disconnect(); if (frame) cancelAnimationFrame(frame) })
</script>

<template>
  <div class="explorer">
    <header class="explorer-top">
      <div class="explorer-heading"><span class="explorer-icon">✦</span><div><strong>知识关系网络</strong><small>从关键实体出发，逐层查看关系与来源</small></div></div>
      <div class="explorer-stats"><span><b>{{ graph.nodes.length.toLocaleString() }}</b> 实体</span><span><b>{{ graph.edges.length.toLocaleString() }}</b> 关系</span><span><b>{{ (processedChunks ?? graph.processed_chunks).toLocaleString() }}</b> 已抽取片段</span></div>
    </header>
    <div class="explorer-toolbar">
      <div class="explorer-search-wrap">
        <label class="explorer-search"><span>⌕</span><input v-model="query" placeholder="搜索实体名称，定位并查看关系" aria-label="搜索实体" /><button v-if="query" aria-label="清除搜索" @click="query = ''">×</button></label>
        <div v-if="query && matches.length" class="explorer-results"><button v-for="node in matches" :key="node.id" @click="selectNode(node.id); query = ''"><i :style="{ background: colorOf(node.type) }"></i><span>{{ node.name }}</span><small>{{ node.type }}</small></button></div>
      </div>
      <div class="explorer-segment" aria-label="图谱范围"><button :class="{ active: scope === 'core' }" @click="setScope('core')">核心关系</button><button :disabled="!selectedNodeId" :class="{ active: scope === 'focus' }" @click="setScope('focus')">实体邻域</button><button :class="{ active: scope === 'all' }" @click="setScope('all')">全量总览</button></div>
      <div class="explorer-segment explorer-layout" aria-label="图谱布局"><button v-for="item in ([['diffusion', '扩散'], ['octopus', '分类'], ['ring', '同心环']] as const)" :key="item[0]" :class="{ active: mode === item[0] }" @click="changeMode(item[0])">{{ item[1] }}</button></div>
    </div>
    <div class="explorer-filters"><span>实体类型</span><button :class="{ active: activeType === '全部' }" @click="activeType = '全部'">全部 <em>{{ graph.nodes.length }}</em></button><button v-for="[type, count] in types" :key="type" :class="{ active: activeType === type }" @click="activeType = type"><i :style="{ background: colorOf(type) }"></i>{{ type }} <em>{{ count }}</em></button></div>
    <div class="explorer-body">
      <div class="explorer-stage">
        <div class="explorer-stage-caption"><span class="explorer-pulse"></span>{{ scope === 'core' ? '核心关系视图' : scope === 'focus' ? '实体邻域视图' : '全量总览' }}<small>{{ visibleNodes.length.toLocaleString() }} 个实体 · {{ visibleEdges.length.toLocaleString() }} 条关系</small></div>
        <canvas ref="canvas" role="img" aria-label="实体关系图，可拖动平移、滚轮缩放并点击实体" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="drag = null" @pointerleave="hoveredNodeId = ''; hoveredEdgeId = ''" @wheel="wheel" />
        <div class="explorer-zoom"><button aria-label="放大" @click="zoom(1.5)">+</button><button aria-label="缩小" @click="zoom(1 / 1.5)">−</button><button aria-label="适应画布" @click="fit">适应</button></div>
        <div class="explorer-stage-foot"><span>滚轮缩放 · 拖动平移 · 点击实体查看邻域</span><button v-if="scope === 'focus'" @click="clearSelection">返回核心关系 ↗</button><span v-else>所有实体均可通过搜索定位</span></div>
      </div>
      <aside class="explorer-inspector">
        <template v-if="selectedEdge"><div class="inspector-eyebrow">关系证据</div><h3>{{ selectedEdge.relation }}</h3><div class="inspector-pair"><span>{{ nameOf(selectedEdge.source) }}</span><b>→</b><span>{{ nameOf(selectedEdge.target) }}</span></div><div class="inspector-caption">原文来源 <em>{{ selectedEdge.evidence.length }}</em></div><article v-for="(item, index) in selectedEdge.evidence" :key="`${item.chunk_id}-${index}`" class="inspector-evidence"><strong>{{ item.document_title }}</strong><small>版本 v{{ item.version_no }}<template v-if="item.page_no"> · 第 {{ item.page_no }} 页</template></small><p>{{ item.quote }}</p></article></template>
        <template v-else-if="selectedNode"><div class="inspector-eyebrow">实体详情</div><div class="inspector-node-mark" :style="{ color: colorOf(selectedNode.type), background: `${colorOf(selectedNode.type)}16` }">{{ selectedNode.type.slice(0, 1) }}</div><h3>{{ selectedNode.name }}</h3><div class="inspector-meta"><span>{{ selectedNode.type }}</span><span>{{ connectedEdges.length }} 条关系</span></div><div class="inspector-caption">关联实体 <em>{{ connectedEdges.length }}</em></div><div v-if="!connectedEdges.length" class="inspector-muted">暂无可查看的关系</div><button v-for="edge in connectedEdges.slice(0, 80)" :key="edge.id" class="inspector-relation" @click="selectEdge(edge.id)"><span>{{ nameOf(edge.source === selectedNode.id ? edge.target : edge.source) }}</span><small>{{ edge.relation }}</small><b>↗</b></button><p v-if="connectedEdges.length > 80" class="inspector-muted">显示前 80 条关系；可搜索其他实体继续探索。</p></template>
        <template v-else><div class="inspector-eyebrow">探索指南</div><div class="inspector-intro-icon">◎</div><h3>从实体理解知识</h3><p class="inspector-muted">点击画布中的实体，查看它与其他实体之间的关系；点击连线，可追溯到原文证据。</p><div class="inspector-caption">高关联实体</div><button v-for="node in ranked.slice(0, 8)" :key="node.id" class="inspector-relation" @click="selectNode(node.id)"><i :style="{ background: colorOf(node.type) }"></i><span>{{ node.name }}</span><small>{{ degree.get(node.id) ?? 0 }} 条关系</small><b>↗</b></button></template>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.explorer{overflow:hidden;border:1px solid #d9e5ed;border-radius:18px;background:#fff;box-shadow:0 16px 42px #2448660b;color:#17364d}
.explorer-top{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:19px 24px;border-bottom:1px solid #e6edf2}.explorer-heading{display:flex;align-items:center;gap:12px}.explorer-icon{display:grid;place-items:center;width:38px;height:38px;border-radius:11px;background:#e9f7f8;color:#158ca0;font-size:21px}.explorer-heading>div{display:grid;gap:3px}.explorer-heading strong{font-size:16px;letter-spacing:.01em}.explorer-heading small{color:#8599a8;font-size:11px}.explorer-stats{display:flex;align-items:center;gap:19px;color:#728a9a;font-size:11px;white-space:nowrap}.explorer-stats span+span{padding-left:19px;border-left:1px solid #e1e9ef}.explorer-stats b{margin-right:4px;color:#1d516b;font-size:18px;font-variant-numeric:tabular-nums}
.explorer-toolbar{display:flex;align-items:center;gap:10px;padding:12px 18px;border-bottom:1px solid #e8eef2}.explorer-search-wrap{position:relative;flex:1;min-width:190px}.explorer-search{display:flex;align-items:center;gap:8px;height:37px;padding:0 11px;border:1px solid #d5e3ea;border-radius:9px;background:#f8fbfc}.explorer-search:focus-within{border-color:#68b9cc;box-shadow:0 0 0 3px #7ac9d929}.explorer-search>span{color:#5e90a7;font-size:22px}.explorer-search input{width:100%;border:0;outline:0;background:none;color:#21465c;font-size:12px}.explorer-search input::placeholder{color:#91a5b1}.explorer-search button{border:0;background:none;color:#7f9aa8;cursor:pointer;font-size:19px}.explorer-results{position:absolute;z-index:5;top:43px;right:0;left:0;overflow:auto;max-height:320px;padding:5px;border:1px solid #d7e5ec;border-radius:11px;background:#fff;box-shadow:0 14px 30px #1e4b6226}.explorer-results button{display:flex;align-items:center;gap:8px;width:100%;padding:9px 10px;border:0;border-radius:7px;background:none;text-align:left;cursor:pointer;color:#27485b;font-size:12px}.explorer-results button:hover{background:#edf7f9}.explorer-results button span{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.explorer-results small{color:#89a0af}.explorer-results i,.explorer-filters i,.inspector-relation i{flex:none;width:8px;height:8px;border-radius:50%}.explorer-segment{display:flex;flex:none;padding:3px;border:1px solid #e0e9ee;border-radius:9px;background:#f5f8fa}.explorer-segment button{padding:7px 10px;border:0;border-radius:6px;background:none;color:#70899a;cursor:pointer;font-size:11px;white-space:nowrap}.explorer-segment button:hover{color:#22677c}.explorer-segment button.active{background:#fff;box-shadow:0 1px 4px #1b49621e;color:#166d84;font-weight:700}.explorer-segment button:disabled{opacity:.4;cursor:default}.explorer-filters{display:flex;align-items:center;gap:5px;overflow:auto;padding:10px 18px;border-bottom:1px solid #e8eef2;scrollbar-width:thin}.explorer-filters>span{flex:none;margin-right:8px;color:#8a9daa;font-size:11px}.explorer-filters button{display:flex;align-items:center;gap:5px;flex:none;padding:6px 9px;border:1px solid transparent;border-radius:7px;background:none;color:#6a8493;cursor:pointer;font-size:11px;white-space:nowrap}.explorer-filters button:hover,.explorer-filters button.active{border-color:#cfe8ed;background:#eff8fa;color:#1d7389}.explorer-filters em{color:#9aafba;font-size:10px;font-style:normal}
.explorer-body{display:grid;grid-template-columns:minmax(0,1fr) 298px;min-height:620px}.explorer-stage{position:relative;min-width:0;background:#f8fbfd}.explorer-stage canvas{display:block;width:100%;height:620px;cursor:grab;touch-action:none}.explorer-stage canvas:active{cursor:grabbing}.explorer-stage-caption{position:absolute;top:18px;left:22px;display:flex;align-items:center;gap:7px;padding:7px 11px;border:1px solid #e2ebf0;border-radius:7px;background:#ffffffdb;color:#2e5f77;font-size:11px;font-weight:700;pointer-events:none}.explorer-stage-caption small{margin-left:6px;color:#89a0ad;font-size:10px;font-weight:500}.explorer-pulse{width:7px;height:7px;border-radius:50%;background:#37ad9e;box-shadow:0 0 0 3px #37ad9e23}.explorer-zoom{position:absolute;right:17px;bottom:50px;display:flex;gap:4px;padding:4px;border:1px solid #dce8ee;border-radius:8px;background:#fff;box-shadow:0 3px 12px #274d6418}.explorer-zoom button{min-width:29px;height:27px;padding:0 6px;border:0;border-radius:5px;background:none;color:#416f84;cursor:pointer;font-size:14px}.explorer-zoom button:hover{background:#eaf5f8}.explorer-stage-foot{position:absolute;right:18px;bottom:13px;left:20px;display:flex;justify-content:space-between;gap:10px;color:#98aab4;font-size:10px;pointer-events:none}.explorer-stage-foot button{border:0;background:none;color:#2c8ba0;cursor:pointer;font-size:10px;pointer-events:auto}
.explorer-inspector{overflow:auto;max-height:620px;padding:23px 20px;border-left:1px solid #e1eaf0;background:#fff;scrollbar-color:#ccdae3 transparent}.inspector-eyebrow{color:#2d93a4;font-size:10px;font-weight:800;letter-spacing:.12em}.explorer-inspector h3{margin:14px 0 11px;color:#1b4157;font-size:18px;line-height:1.4;overflow-wrap:anywhere}.inspector-intro-icon{display:grid;place-items:center;width:52px;height:52px;margin-top:29px;border:1px solid #b9e5e8;border-radius:14px;background:#ecf8f8;color:#2993a0;font-size:27px}.inspector-muted{color:#849bab;font-size:12px;line-height:1.75}.inspector-caption{display:flex;justify-content:space-between;margin:26px 0 8px;padding-bottom:9px;border-bottom:1px solid #e8eef2;color:#5b798b;font-size:11px;font-weight:700}.inspector-caption em{color:#2e9cad;font-style:normal}.inspector-relation{display:flex;align-items:center;gap:8px;width:100%;padding:10px 3px;border:0;border-bottom:1px solid #eff3f5;background:none;text-align:left;cursor:pointer}.inspector-relation:hover{background:#f4fafb}.inspector-relation span{flex:1;overflow:hidden;color:#3f6072;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.inspector-relation small{max-width:100px;overflow:hidden;color:#8fa3ae;font-size:10px;text-overflow:ellipsis;white-space:nowrap}.inspector-relation b{color:#52a7b4;font-size:12px}.inspector-node-mark{display:grid;place-items:center;width:48px;height:48px;margin-top:24px;border-radius:12px;font-size:19px;font-weight:800}.inspector-meta{display:flex;gap:7px}.inspector-meta span{padding:5px 9px;border-radius:6px;background:#f0f6f8;color:#648596;font-size:10px}.inspector-pair{display:grid;gap:8px;padding:13px;border:1px solid #e3edf1;border-radius:9px;background:#f8fbfc;color:#375b6c;font-size:12px;overflow-wrap:anywhere}.inspector-pair b{color:#41a2b2}.inspector-evidence{padding:13px 0;border-bottom:1px solid #edf1f4}.inspector-evidence strong{display:block;color:#33596b;font-size:12px;line-height:1.5;overflow-wrap:anywhere}.inspector-evidence small{display:block;margin:5px 0;color:#8a9faa;font-size:10px}.inspector-evidence p{margin:8px 0 0;padding:10px 11px;border-left:2px solid #64b7c2;border-radius:0 5px 5px 0;background:#f4f9fa;color:#536f7f;font-size:11px;line-height:1.7;overflow-wrap:anywhere}
@media(max-width:1200px){.explorer-toolbar{flex-wrap:wrap}.explorer-layout{margin-left:auto}}@media(max-width:900px){.explorer-body{grid-template-columns:1fr}.explorer-inspector{max-height:360px;border-top:1px solid #e1eaf0;border-left:0}.explorer-stage canvas{height:520px}}@media(max-width:580px){.explorer-top{align-items:flex-start;flex-direction:column;padding:16px}.explorer-stats{gap:10px}.explorer-stats span+span{padding-left:10px}.explorer-stats b{font-size:15px}.explorer-toolbar,.explorer-filters{padding:10px}.explorer-search-wrap{flex-basis:100%}.explorer-segment{flex:1}.explorer-segment button{flex:1;padding:7px 5px}.explorer-layout{margin-left:0}.explorer-stage canvas{height:440px}.explorer-stage-foot span:last-child{display:none}}
</style>
