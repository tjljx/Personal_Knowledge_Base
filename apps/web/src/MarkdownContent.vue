<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const props = defineProps<{ content: string }>()

const html = computed(() => DOMPurify.sanitize(
  marked.parse(props.content, { async: false, gfm: true, breaks: true }) as string,
))
</script>

<template>
  <div class="markdown-content" v-html="html" />
</template>
