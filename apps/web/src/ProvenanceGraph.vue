<script setup lang="ts">
import { computed } from 'vue'
import type { Provenance, ProvenanceChunk } from './api'

const props = defineProps<{ graph: Provenance; selectedChunkId?: string }>()
const emit = defineEmits<{ selectChunk: [chunk: ProvenanceChunk, versionNo: number] }>()

type Line = { key: string; d: string }
type VersionNode = { versionNo: number; status: string; parser: string | null; pageCount: number; chunkCount: number; y: number }
type ChunkNode = { chunk: ProvenanceChunk; versionNo: number; y: number }

const layout = computed(() => {
  const versions: VersionNode[] = []
  const chunks: ChunkNode[] = []
  const lines: Line[] = []
  let cursor = 60
  for (const version of props.graph.versions) {
    const firstY = cursor
    for (const chunk of version.chunks) {
      chunks.push({ chunk, versionNo: version.version_no, y: cursor })
      cursor += 82
    }
    const versionY = version.chunks.length ? (firstY + cursor - 82) / 2 : cursor
    versions.push({ versionNo: version.version_no, status: version.status, parser: version.parser_name, pageCount: version.page_count, chunkCount: version.chunk_count, y: versionY })
    for (const chunk of chunks.filter(item => item.versionNo === version.version_no)) {
      lines.push({ key: `chunk-${chunk.chunk.id}`, d: `M 530 ${versionY} C 575 ${versionY}, 575 ${chunk.y}, 620 ${chunk.y}` })
    }
    if (!version.chunks.length) cursor += 82
    cursor += 28
  }
  const documentY = versions.length ? (versions[0].y + versions[versions.length - 1].y) / 2 : 100
  for (const version of versions) {
    lines.push({ key: `version-${version.versionNo}`, d: `M 250 ${documentY} C 295 ${documentY}, 295 ${version.y}, 340 ${version.y}` })
  }
  return { versions, chunks, lines, documentY, height: Math.max(250, cursor + 40) }
})
</script>

<template>
  <div class="graph-scroll">
    <div class="graph-canvas" :style="{ height: `${layout.height}px` }">
      <svg class="graph-lines" width="940" :height="layout.height" aria-hidden="true"><path v-for="line in layout.lines" :key="line.key" :d="line.d" /></svg>
      <div class="graph-node graph-document" :style="{ top: `${layout.documentY}px` }"><span>文档</span><strong :title="graph.title">{{ graph.title }}</strong><small>当前版本 v{{ graph.current_version }}</small></div>
      <div v-for="version in layout.versions" :key="version.versionNo" class="graph-node graph-version" :style="{ top: `${version.y}px` }">
        <span>版本 v{{ version.versionNo }}{{ version.versionNo === graph.current_version ? ' · 当前' : '' }}</span>
        <strong>{{ version.status === 'PARSED' ? '已解析' : version.status === 'FAILED' ? '解析失败' : '待解析' }}</strong>
        <small>{{ version.parser || '尚无解析结果' }} · {{ version.pageCount }} 页 · {{ version.chunkCount }} 片段</small>
      </div>
      <button v-for="item in layout.chunks" :key="item.chunk.id" class="graph-node graph-chunk" :class="{ active: selectedChunkId === item.chunk.id }" :style="{ top: `${item.y}px` }" @click="emit('selectChunk', item.chunk, item.versionNo)">
        <span>解析片段 {{ item.chunk.ordinal + 1 }} <template v-if="item.chunk.page_no">· 第 {{ item.chunk.page_no }} 页</template></span>
        <strong :title="item.chunk.excerpt">{{ item.chunk.excerpt }}</strong>
      </button>
    </div>
  </div>
</template>

<style scoped>
.graph-scroll { max-height: 590px; overflow: auto; border: 1px solid #e3edf1; border-radius: 12px; background: radial-gradient(circle at 25% 15%, #f2faf9, transparent 35%), #fbfdfe; }
.graph-canvas { position: relative; width: 940px; min-width: 940px; }
.graph-lines { position: absolute; inset: 0; fill: none; stroke: #accbd0; stroke-width: 2; }
.graph-node { position: absolute; z-index: 1; display: grid; gap: 5px; transform: translateY(-50%); padding: 12px 14px; border: 1px solid #dbe9ee; border-radius: 10px; background: #fff; box-shadow: 0 7px 22px rgba(22, 63, 79, .08); color: #344c5b; text-align: left; }
.graph-node span { color: #168c91; font-size: 10px; font-weight: 800; }
.graph-node strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.graph-node small { overflow: hidden; color: #8094a0; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; }
.graph-document { left: 30px; width: 220px; border-color: #7bc7c7; background: #eaf8f6; }
.graph-version { left: 340px; width: 190px; }
.graph-chunk { left: 620px; width: 300px; min-height: 66px; cursor: pointer; }
.graph-chunk:hover, .graph-chunk.active { border-color: #43aaa9; background: #f0fbf8; }
</style>
