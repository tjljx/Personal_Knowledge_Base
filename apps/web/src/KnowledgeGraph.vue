<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { KnowledgeGraphData } from './api'

type GraphNode = KnowledgeGraphData['nodes'][number]
type GraphEdge = KnowledgeGraphData['edges'][number]
type Point = { x: number; y: number }
type DrawnEdge = { edge: GraphEdge; source: Point; target: Point; label: Point }

const props = defineProps<{ graph: KnowledgeGraphData; processedChunks?: number }>()
const search = ref('')
const activeType = ref('全部')
const page = ref(0)
const selectedNodeId = ref('')
const selectedEdgeId = ref('')
const hoveredNodeId = ref('')
const hoveredEdgeId = ref('')
const pageSize = 24

const palette: Record<string, string> = {
  人物: '#bc8bff', 组织: '#43c9f6', 地点: '#56d9b4', 产品: '#ffbf73',
  技术: '#67a9ff', 概念: '#f48bb9', 事件: '#9caaff', 其他: '#91a9c8',
}
const colorOf = (type: string): string => palette[type] ?? palette.其他
const nodeById = computed(() => new Map(props.graph.nodes.map(node => [node.id, node])))
const degree = computed(() => {
  const counts = new Map<string, number>()
  props.graph.edges.forEach(edge => {
    counts.set(edge.source, (counts.get(edge.source) ?? 0) + 1)
    counts.set(edge.target, (counts.get(edge.target) ?? 0) + 1)
  })
  return counts
})
const types = computed(() => {
  const counts = new Map<string, number>()
  props.graph.nodes.forEach(node => counts.set(node.type, (counts.get(node.type) ?? 0) + 1))
  return [...counts.entries()].sort((a, b) => b[1] - a[1])
})
const candidates = computed(() => {
  const term = search.value.trim().toLocaleLowerCase()
  const matches = new Set(props.graph.nodes.filter(node => node.name.toLocaleLowerCase().includes(term)).map(node => node.id))
  const included = new Set(matches)
  if (term) props.graph.edges.forEach(edge => {
    if (matches.has(edge.source) || matches.has(edge.target)) {
      included.add(edge.source)
      included.add(edge.target)
    }
  })
  return props.graph.nodes
    .filter(node => (activeType.value === '全部' || node.type === activeType.value) && (!term || included.has(node.id)))
    .sort((a, b) => Number(matches.has(b.id)) - Number(matches.has(a.id)) || (degree.value.get(b.id) ?? 0) - (degree.value.get(a.id) ?? 0) || a.name.localeCompare(b.name))
})
const pageCount = computed(() => Math.max(1, Math.ceil(candidates.value.length / pageSize)))
const visibleNodes = computed(() => candidates.value.slice(page.value * pageSize, (page.value + 1) * pageSize))
const points = computed(() => new Map<string, Point>(visibleNodes.value.map((node, index) => {
  const angle = index * Math.PI * 2 / Math.max(visibleNodes.value.length, 1) - Math.PI / 2
  return [node.id, { x: 520 + 378 * Math.cos(angle), y: 385 + 288 * Math.sin(angle) }]
})))
const drawnEdges = computed<DrawnEdge[]>(() => props.graph.edges.flatMap(edge => {
  const source = points.value.get(edge.source)
  const target = points.value.get(edge.target)
  return source && target ? [{ edge, source, target, label: { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 - 9 } }] : []
}))
const selectedNode = computed(() => nodeById.value.get(selectedNodeId.value))
const selectedEdge = computed(() => props.graph.edges.find(edge => edge.id === selectedEdgeId.value))
const connectedEdges = computed(() => selectedNode.value ? props.graph.edges.filter(edge => edge.source === selectedNode.value?.id || edge.target === selectedNode.value?.id) : [])
const focusedNodeId = computed(() => hoveredNodeId.value || selectedNodeId.value)
const focusedEdgeId = computed(() => hoveredEdgeId.value || selectedEdgeId.value)
const nameOf = (id: string): string => nodeById.value.get(id)?.name ?? '未知实体'
const shortName = (name: string): string => name.length > 7 ? `${name.slice(0, 6)}…` : name
const isActiveEdge = (edge: GraphEdge): boolean => edge.id === focusedEdgeId.value || !!focusedNodeId.value && (edge.source === focusedNodeId.value || edge.target === focusedNodeId.value)
const isDimEdge = (edge: GraphEdge): boolean => !!(focusedNodeId.value || focusedEdgeId.value) && !isActiveEdge(edge)
const isDimNode = (id: string): boolean => {
  if (focusedEdgeId.value) {
    const edge = props.graph.edges.find(item => item.id === focusedEdgeId.value)
    return !!edge && edge.source !== id && edge.target !== id
  }
  return !!focusedNodeId.value && id !== focusedNodeId.value && !props.graph.edges.some(edge =>
    edge.source === focusedNodeId.value && edge.target === id || edge.target === focusedNodeId.value && edge.source === id,
  )
}
function selectNode(id: string): void { selectedNodeId.value = id; selectedEdgeId.value = '' }
function selectEdge(id: string): void { selectedEdgeId.value = id; selectedNodeId.value = '' }
function clearSelection(): void { selectedNodeId.value = ''; selectedEdgeId.value = '' }
function changePage(next: number): void { page.value = next; clearSelection() }
watch([search, activeType], () => { page.value = 0; clearSelection() })
</script>

<template>
  <div class="kg-shell">
    <div class="kg-header">
      <div class="kg-identity"><span class="kg-mark">✧</span><div><span class="kg-eyebrow">KNOWLEDGE INTELLIGENCE</span><h4>知识关系网络</h4></div></div>
      <div class="kg-stats"><div><strong>{{ graph.nodes.length }}</strong><span>实体</span></div><i></i><div><strong>{{ graph.edges.length }}</strong><span>关系</span></div><i></i><div><strong>{{ processedChunks ?? graph.processed_chunks }}</strong><span>已抽取片段</span></div></div>
    </div>
    <div class="kg-controls">
      <label class="kg-search"><span>⌕</span><input v-model="search" placeholder="搜索实体名称或查看相邻关系" aria-label="搜索知识图谱实体" /><button v-if="search" type="button" aria-label="清空搜索" @click="search = ''">×</button></label>
      <div class="kg-filters"><button type="button" :class="{ active: activeType === '全部' }" @click="activeType = '全部'">全部</button><button v-for="[type, count] in types" :key="type" type="button" :class="{ active: activeType === type }" @click="activeType = type"><span class="kg-filter-dot" :style="{ background: colorOf(type) }"></span>{{ type }} <small>{{ count }}</small></button></div>
    </div>
    <div class="kg-body">
      <div class="kg-visual">
        <div class="kg-canvas-caption"><span class="kg-live-dot"></span> ENTITY NETWORK <span class="kg-caption-divider">/</span> 点击节点或连线查看证据</div>
        <svg viewBox="0 0 1040 780" role="img" aria-label="知识库实体关系图">
          <defs>
            <radialGradient id="kg-background"><stop offset="0" stop-color="#132744" /><stop offset=".62" stop-color="#0b1b32" /><stop offset="1" stop-color="#071426" /></radialGradient>
            <radialGradient id="kg-core"><stop offset="0" stop-color="#174576" /><stop offset=".65" stop-color="#0d284a" /><stop offset="1" stop-color="#092039" /></radialGradient>
            <pattern id="kg-grid" width="38" height="38" patternUnits="userSpaceOnUse"><path d="M 38 0 L 0 0 0 38" fill="none" stroke="#63a9e8" stroke-opacity=".055" stroke-width="1" /></pattern>
            <filter id="kg-glow"><feGaussianBlur stdDeviation="9" /></filter>
          </defs>
          <rect width="1040" height="780" fill="url(#kg-background)" />
          <rect width="1040" height="780" fill="url(#kg-grid)" />
          <ellipse cx="520" cy="385" rx="410" ry="322" fill="none" stroke="#55a3d5" stroke-opacity=".16" stroke-width="1" stroke-dasharray="3 9" />
          <ellipse cx="520" cy="385" rx="322" ry="242" fill="none" stroke="#6bb8e9" stroke-opacity=".13" stroke-width="1" />
          <ellipse cx="520" cy="385" rx="210" ry="154" fill="none" stroke="#7ccdf0" stroke-opacity=".09" stroke-width="1" stroke-dasharray="2 8" />
          <g v-for="item in drawnEdges" :key="item.edge.id" class="kg-edge" :class="{ active: isActiveEdge(item.edge), dim: isDimEdge(item.edge) }" @mouseenter="hoveredEdgeId = item.edge.id" @mouseleave="hoveredEdgeId = ''" @click="selectEdge(item.edge.id)">
            <line class="kg-edge-line" :x1="item.source.x" :y1="item.source.y" :x2="item.target.x" :y2="item.target.y" />
            <line class="kg-edge-hit" :x1="item.source.x" :y1="item.source.y" :x2="item.target.x" :y2="item.target.y" />
            <text v-if="isActiveEdge(item.edge)" class="kg-edge-label" :x="item.label.x" :y="item.label.y" text-anchor="middle">{{ item.edge.relation }}</text>
            <title>{{ nameOf(item.edge.source) }} — {{ item.edge.relation }} — {{ nameOf(item.edge.target) }}</title>
          </g>
          <circle cx="520" cy="385" r="94" fill="#2e9bd5" opacity=".13" filter="url(#kg-glow)" />
          <circle cx="520" cy="385" r="76" fill="url(#kg-core)" stroke="#73c9f3" stroke-opacity=".62" stroke-width="1.5" />
          <circle cx="520" cy="385" r="62" fill="none" stroke="#92d9fa" stroke-opacity=".23" stroke-dasharray="2 6" />
          <text x="520" y="368" text-anchor="middle" class="kg-core-number">{{ graph.nodes.length }}</text>
          <text x="520" y="393" text-anchor="middle" class="kg-core-title">知识实体</text>
          <text x="520" y="414" text-anchor="middle" class="kg-core-subtitle">CONNECTED KNOWLEDGE</text>
          <g v-for="node in visibleNodes" :key="node.id" class="kg-node" :class="{ active: selectedNodeId === node.id || hoveredNodeId === node.id, dim: isDimNode(node.id) }" :style="{ '--node-color': colorOf(node.type) }" :transform="`translate(${points.get(node.id)?.x ?? 0}, ${points.get(node.id)?.y ?? 0})`" role="button" tabindex="0" :aria-label="`${node.name}，${node.type}，${degree.get(node.id) ?? 0} 条关系`" @mouseenter="hoveredNodeId = node.id" @mouseleave="hoveredNodeId = ''" @click="selectNode(node.id)" @keydown.enter.prevent="selectNode(node.id)" @keydown.space.prevent="selectNode(node.id)">
            <circle class="kg-node-halo" r="31" />
            <circle class="kg-node-disc" r="22" />
            <text class="kg-node-initial" text-anchor="middle" dominant-baseline="middle">{{ node.type.slice(0, 1) }}</text>
            <text class="kg-node-name" y="42" text-anchor="middle">{{ shortName(node.name) }}</text>
            <title>{{ node.name }} · {{ node.type }}</title>
          </g>
          <g v-if="!visibleNodes.length"><text x="520" y="367" text-anchor="middle" class="kg-empty-title">暂无匹配的实体</text><text x="520" y="395" text-anchor="middle" class="kg-empty-subtitle">请调整搜索词或实体类型</text></g>
        </svg>
        <div class="kg-canvas-footer"><span>展示 {{ visibleNodes.length }} / {{ candidates.length }} 个实体</span><div v-if="pageCount > 1"><button type="button" :disabled="page === 0" @click="changePage(page - 1)">‹</button><span>{{ page + 1 }} / {{ pageCount }}</span><button type="button" :disabled="page >= pageCount - 1" @click="changePage(page + 1)">›</button></div><span>关系均关联原文证据</span></div>
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
          <div class="kg-guide-icon">⌘</div><h3>探索知识之间的联系</h3><p class="kg-guide-text">选择圆环中的实体，查看它与其他实体的连接；点击连线，核对关系来自哪份文档及对应的原文。</p>
          <div class="kg-section-caption">实体分类</div>
          <div class="kg-legend"><div v-for="[type, count] in types" :key="type"><span class="kg-filter-dot" :style="{ background: colorOf(type) }"></span><span>{{ type }}</span><strong>{{ count }}</strong></div></div>
        </template>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.kg-shell{overflow:hidden;border:1px solid #173755;border-radius:20px;background:#09182b;color:#e8f4ff;box-shadow:0 22px 45px rgba(8,30,55,.18)}
.kg-header{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:24px 28px;border-bottom:1px solid rgba(133,194,229,.14);background:linear-gradient(105deg,#102a47,#0b1d34 70%)}
.kg-identity{display:flex;align-items:center;gap:13px}.kg-mark{display:grid;place-items:center;width:42px;height:42px;border:1px solid #3987b4;border-radius:12px;background:#123c60;color:#9ce8ff;font-size:27px;box-shadow:inset 0 0 18px #2175a84d}.kg-eyebrow,.kg-detail-label{color:#62b8dd;font-size:10px;font-weight:800;letter-spacing:.2em}.kg-identity h4{margin:5px 0 0;font-size:18px;letter-spacing:.03em}.kg-stats{display:flex;align-items:center;gap:20px}.kg-stats>div{display:grid;gap:2px}.kg-stats strong{font-size:23px;line-height:1;font-variant-numeric:tabular-nums}.kg-stats span{color:#88a8bf;font-size:11px}.kg-stats i{width:1px;height:26px;background:#36516b}
.kg-controls{display:flex;align-items:center;gap:16px;padding:15px 22px;border-bottom:1px solid rgba(133,194,229,.12);background:#0b1d32}.kg-search{display:flex;align-items:center;gap:9px;flex:0 0 280px;max-width:100%;height:37px;padding:0 12px;border:1px solid #2b4c69;border-radius:9px;background:#0c253e}.kg-search:focus-within{border-color:#69c5ed;box-shadow:0 0 0 3px #49a7dc24}.kg-search>span{color:#77b9db;font-size:21px}.kg-search input{width:100%;border:0;outline:0;background:transparent;color:#eff8ff;font-size:12px}.kg-search input::placeholder{color:#7795aa}.kg-search button{border:0;background:none;color:#a9c5d9;cursor:pointer;font-size:18px}.kg-filters{display:flex;gap:6px;overflow:auto;scrollbar-width:thin}.kg-filters button{display:flex;align-items:center;gap:6px;flex:none;padding:8px 10px;border:1px solid transparent;border-radius:8px;background:transparent;color:#89a8be;cursor:pointer;font-size:11px;white-space:nowrap}.kg-filters button:hover,.kg-filters button.active{border-color:#3c7195;background:#123955;color:#d7f4ff}.kg-filters small{color:#6c98b3;font-size:10px}.kg-filter-dot{display:inline-block;flex:none;width:7px;height:7px;border-radius:50%;box-shadow:0 0 8px currentColor}
.kg-body{display:grid;grid-template-columns:minmax(0,1fr) 315px;min-height:620px}.kg-visual{position:relative;min-width:0;background:#09182b}.kg-visual svg{display:block;width:100%;height:auto;min-height:530px}.kg-canvas-caption{position:absolute;top:17px;left:22px;z-index:1;color:#73b7d7;font-size:10px;font-weight:700;letter-spacing:.15em}.kg-caption-divider{margin:0 8px;color:#427390}.kg-live-dot{display:inline-block;width:6px;height:6px;margin-right:8px;border-radius:50%;background:#5be4d5;box-shadow:0 0 9px #5be4d5}.kg-canvas-footer{position:absolute;right:18px;bottom:15px;left:18px;display:flex;justify-content:space-between;align-items:center;gap:12px;color:#7797ae;font-size:10px}.kg-canvas-footer>div{display:flex;align-items:center;gap:8px}.kg-canvas-footer button{width:25px;height:25px;border:1px solid #32546d;border-radius:6px;background:#12304a;color:#ceeaff;cursor:pointer;font-size:18px;line-height:1}.kg-canvas-footer button:disabled{opacity:.35;cursor:default}
.kg-edge{cursor:pointer}.kg-edge-line{stroke:#75b6d5;stroke-width:1.4;stroke-opacity:.28;transition:stroke-opacity .2s,stroke-width .2s}.kg-edge-hit{stroke:transparent;stroke-width:17}.kg-edge.active .kg-edge-line{stroke:#83ddff;stroke-opacity:.9;stroke-width:3;filter:drop-shadow(0 0 5px #54c4ff)}.kg-edge.dim .kg-edge-line{stroke-opacity:.075}.kg-edge-label{fill:#c5efff;font-size:11px;font-weight:700;paint-order:stroke;stroke:#10253e;stroke-width:5px;pointer-events:none}.kg-core-number{fill:#edfbff;font-size:32px;font-weight:800}.kg-core-title{fill:#c7eaff;font-size:15px;font-weight:700}.kg-core-subtitle{fill:#5e9ec2;font-size:7px;letter-spacing:.15em}.kg-node{cursor:pointer;outline:none;transition:opacity .2s}.kg-node.dim{opacity:.35}.kg-node-halo{fill:var(--node-color);fill-opacity:.08;stroke:var(--node-color);stroke-opacity:.26;stroke-width:1}.kg-node-disc{fill:#102842;stroke:var(--node-color);stroke-width:2;filter:drop-shadow(0 0 6px var(--node-color))}.kg-node:hover .kg-node-disc,.kg-node.active .kg-node-disc,.kg-node:focus .kg-node-disc{fill:var(--node-color);stroke:#efffff;stroke-width:2.5}.kg-node-initial{fill:var(--node-color);font-size:15px;font-weight:800;pointer-events:none}.kg-node:hover .kg-node-initial,.kg-node.active .kg-node-initial{fill:#102033}.kg-node-name{fill:#d6eaf6;font-size:12px;font-weight:700;paint-order:stroke;stroke:#0a1a2e;stroke-width:4px;pointer-events:none}.kg-node:hover .kg-node-name,.kg-node.active .kg-node-name{fill:#fff}.kg-empty-title{fill:#d3eafb;font-size:18px;font-weight:700}.kg-empty-subtitle{fill:#6b95b4;font-size:12px}
.kg-inspector{min-width:0;padding:26px 23px;border-left:1px solid rgba(133,194,229,.14);background:linear-gradient(180deg,#0d233b,#0a1b2f);max-height:710px;overflow:auto;scrollbar-color:#335776 transparent}.kg-inspector h3{margin:17px 0 8px;font-size:18px;line-height:1.4;overflow-wrap:anywhere}.kg-entity-pair{display:grid;gap:7px;margin-top:27px}.kg-entity-pair span{font-size:17px;font-weight:700;overflow-wrap:anywhere}.kg-entity-pair b{color:#65c8ec;font-size:18px}.kg-relation-tag{display:inline-block;margin-top:19px;padding:7px 12px;border:1px solid #397aa0;border-radius:7px;background:#164366;color:#b9edff;font-size:12px;font-weight:700}.kg-section-caption{display:flex;justify-content:space-between;align-items:center;margin:30px 0 12px;padding-bottom:10px;border-bottom:1px solid #24405a;color:#8eb7cd;font-size:11px;font-weight:700;letter-spacing:.08em}.kg-section-caption span{color:#6fd5f3}.kg-evidence{display:flex;gap:12px;padding:15px 0;border-bottom:1px solid #1e3851}.kg-evidence-number{color:#4ebbdc;font-size:11px;font-weight:800}.kg-evidence strong{display:block;color:#e6f3ff;font-size:12px;line-height:1.5;overflow-wrap:anywhere}.kg-evidence small{display:block;margin:5px 0 9px;color:#78a3bb;font-size:10px}.kg-evidence p{margin:0;padding:10px 11px;border-left:2px solid #3ea7ce;border-radius:0 6px 6px 0;background:#11304b;color:#accfe2;font-size:11px;line-height:1.7;overflow-wrap:anywhere}.kg-profile-icon{display:grid;place-items:center;width:55px;height:55px;margin-top:26px;border:1px solid var(--node-color);border-radius:15px;background:#143652;color:var(--node-color);font-size:22px;font-weight:800;box-shadow:0 0 20px #2ca4c72b}.kg-profile-meta{display:flex;gap:8px}.kg-profile-meta span{padding:5px 9px;border-radius:5px;background:#173650;color:#8fcce8;font-size:10px}.kg-relation-row{display:grid;grid-template-columns:1fr auto 1fr auto;align-items:center;gap:7px;width:100%;padding:12px 0;border:0;border-bottom:1px solid #1f3b54;background:none;color:#c0ddec;text-align:left;cursor:pointer;font-size:11px}.kg-relation-row:hover{color:#fff}.kg-relation-row span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.kg-relation-row b{color:#69cee9;font-weight:600}.kg-relation-row em{color:#69cee9;font-style:normal}.kg-guide-icon{display:grid;place-items:center;width:62px;height:62px;margin-top:36px;border:1px solid #3a779b;border-radius:17px;background:#123c5a;color:#82d8f4;font-size:30px}.kg-guide-text{color:#94b4c7;font-size:12px;line-height:1.8}.kg-legend{display:grid;gap:12px}.kg-legend>div{display:flex;align-items:center;gap:9px;color:#a9c8d9;font-size:11px}.kg-legend strong{margin-left:auto;color:#dcefff;font-variant-numeric:tabular-nums}
@media(max-width:1120px){.kg-body{grid-template-columns:minmax(0,1fr) 275px}.kg-controls{align-items:flex-start;flex-direction:column}.kg-filters{max-width:100%}}@media(max-width:850px){.kg-header{align-items:flex-start;flex-direction:column}.kg-body{grid-template-columns:1fr}.kg-inspector{max-height:none;border-top:1px solid rgba(133,194,229,.14);border-left:0}.kg-visual svg{min-height:0}.kg-canvas-footer{position:static;padding:0 15px 14px}.kg-canvas-caption{font-size:9px}}@media(max-width:540px){.kg-header{padding:19px}.kg-stats{gap:12px}.kg-stats strong{font-size:19px}.kg-search{flex-basis:auto;width:100%}.kg-controls{padding:14px}.kg-canvas-footer>span:last-child{display:none}}
</style>
