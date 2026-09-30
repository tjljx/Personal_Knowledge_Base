<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, apiPage, apiStream, download, openPdf, setToken, type ChatMessage, type ChatSession, type CitationSource, type CloudBudgetSummary, type Document, type DocumentParseIssue, type DocumentParseOverview, type DocumentSearchResult, type DocumentVersion, type KnowledgeBase, type ParsedDocument, type KnowledgeGraphData, type KnowledgeGraphProgress, type User } from './api'
import KnowledgeGraph from './GraphExplorer.vue'
import MarkdownContent from './MarkdownContent.vue'
import { clearStoredSession, readSession, saveSession, SESSION_IDLE_MS } from './session'

const username = ref('')
const password = ref('')
const currentUser = ref<User | null>(null)
const restoringSession = ref(true)
const knowledgeBases = ref<KnowledgeBase[]>([])
const selected = ref<KnowledgeBase | null>(null)
const activeSection = ref<'overview' | 'documents' | 'ask' | 'graph' | 'team'>('overview')
const selectedId = computed({
  get: () => selected.value?.id ?? '',
  set: (id: string) => { selected.value = knowledgeBases.value.find(kb => kb.id === id) ?? null },
})
const canGrant = computed(() => !!selected.value && !!currentUser.value && (currentUser.value.role === 'ADMIN' || selected.value.owner_id === currentUser.value.id))
const newKbName = ref('')
const newKbDescription = ref('')
const createKbDialogVisible = ref(false)
const creatingKb = ref(false)
const passwordDialogVisible = ref(false)
const currentPassword = ref('')
const changedPassword = ref('')
const confirmPassword = ref('')
const changingPassword = ref(false)
const newUsername = ref('')
const newPassword = ref('')
const users = ref<User[]>([])
const cloudBudget = ref<CloudBudgetSummary | null>(null)
const memberId = ref('')
const memberRole = ref<'VIEWER' | 'EDITOR'>('EDITOR')
const busy = ref(false)
const cloudBusy = ref(false)
const documents = ref<Document[]>([])
const selectedDocument = ref<Document | null>(null)
const versions = ref<DocumentVersion[]>([])
const uploadFiles = ref<File[]>([])
const uploading = ref(false)
const documentTotal = ref(0)
const parseOverview = ref<DocumentParseOverview | null>(null)
const parseSubmitting = ref<string | null>(null)
const allDocumentTotal = ref(0)
const documentPage = ref(1)
const showDeleted = ref(false)
const searchText = ref('')
const searchResults = ref<DocumentSearchResult[]>([])
const searchMode = ref(false)
const filterTag = ref('')
const filterSource = ref('')
const filterType = ref('')
const editTitle = ref('')
const editDescription = ref('')
const editSource = ref('')
const editTags = ref('')
const previewContent = ref('')
const previewLabel = ref('')
const parsedResult = ref<ParsedDocument | null>(null)
const parsedPage = ref(1)
const knowledgeGraph = ref<KnowledgeGraphData | null>(null)
const graphProgress = ref<KnowledgeGraphProgress | null>(null)
const graphLoading = ref(false)
const graphProgressPercent = computed(() => graphProgress.value?.total_chunks
  ? Math.round(graphProgress.value.processed_chunks * 100 / graphProgress.value.total_chunks) : 0)
const question = ref('')
const asking = ref(false)
const chatSessions = ref<ChatSession[]>([])
const activeChatSession = ref<ChatSession | null>(null)
const chatMessages = ref<ChatMessage[]>([])
const citationOpen = ref(false)
const selectedCitation = ref<ChatMessage['citations'][number] | null>(null)
const citationSource = ref<CitationSource | null>(null)
const citationLoading = ref(false)
const citationPage = computed(() => citationSource.value ? citationSource.value.page_no : selectedCitation.value?.page_no)
const chatHistoryRef = ref<HTMLElement | null>(null)
let chatAbortController: AbortController | null = null
let parsePoller: number | undefined
let graphProgressPoller: number | undefined
let sessionToken = ''
let lastActivityAt = 0
let lastStoredAt = 0
let lastRenewedAt = 0
let lastRenewAttemptAt = 0
let renewingSession: Promise<void> | null = null

function renewSessionIfNeeded(): Promise<void> {
  const now = Date.now()
  if (!sessionToken || !currentUser.value || now - lastRenewedAt < 4 * 60 * 1000
    || now - lastRenewAttemptAt < 30 * 1000) return Promise.resolve()
  if (renewingSession) return renewingSession
  lastRenewAttemptAt = now
  const previousToken = sessionToken
  renewingSession = api<{ access_token: string }>('/auth/renew', { method: 'POST' })
    .then(result => {
      if (sessionToken !== previousToken || !currentUser.value) return
      sessionToken = result.access_token
      setToken(sessionToken)
      lastRenewedAt = Date.now()
      try { saveSession(sessionStorage, sessionToken, lastActivityAt, lastRenewedAt) } catch { /* Keep this tab usable. */ }
    })
    .catch(() => undefined)
    .finally(() => { renewingSession = null })
  return renewingSession
}

function persistActivity(now: number): void {
  lastActivityAt = now
  if (now - lastStoredAt < 1000) return
  try { saveSession(sessionStorage, sessionToken, now, lastRenewedAt); lastStoredAt = now } catch { /* Keep this tab usable. */ }
}

function handleActivity(event: Event): void {
  if (!currentUser.value) return
  const now = Date.now()
  if (now < lastActivityAt || now - lastActivityAt >= SESSION_IDLE_MS) {
    event.preventDefault()
    event.stopImmediatePropagation()
    void api<void>('/auth/logout', { method: 'POST' }).catch(() => undefined)
    clearSession()
    ElMessage.warning('超过 10 分钟未操作，请重新登录')
    return
  }
  persistActivity(now)
  void renewSessionIfNeeded()
}

function persistBeforeLeaving(): void {
  if (!sessionToken || !lastActivityAt) return
  try { saveSession(sessionStorage, sessionToken, lastActivityAt, lastRenewedAt) } catch { /* Storage may be unavailable. */ }
}

async function restoreSession(): Promise<void> {
  let saved = null
  try { saved = readSession(sessionStorage) } catch { /* Storage may be unavailable. */ }
  if (!saved) { restoringSession.value = false; return }
  sessionToken = saved.token
  lastActivityAt = saved.lastActivityAt
  lastRenewedAt = saved.renewedAt
  setToken(saved.token)
  try {
    currentUser.value = await api<User>('/auth/me')
    persistActivity(Date.now())
    await renewSessionIfNeeded()
    await refresh()
  } catch {
    clearSession()
  } finally {
    restoringSession.value = false
  }
}
const parseable = (filename: string): boolean => /\.(md|html|htm|pdf|doc|docx|pptx|xlsx|png|jpe?g|tiff?|webp)$/i.test(filename)
const uploadOnly = (filename: string): boolean => /\.(ppt|xls)$/i.test(filename)
const parsedPages = computed(() => parsedResult.value?.structure.pages ?? [])
const visibleParsedPage = computed(() => parsedPages.value.find(page => page.number === parsedPage.value))
const statusLabel = (status: string): string => ({ UPLOADED: '已上传', PARSING: '解析中', PARSED: '已解析', FAILED: '解析失败', DELETED: '已删除', CANCELLED: '已取消' })[status as 'UPLOADED' | 'PARSING' | 'PARSED' | 'FAILED' | 'DELETED' | 'CANCELLED'] ?? status

onMounted(() => {
  window.addEventListener('pointerdown', handleActivity, true)
  window.addEventListener('keydown', handleActivity, true)
  window.addEventListener('click', handleActivity, true)
  window.addEventListener('wheel', handleActivity, { capture: true, passive: false })
  window.addEventListener('pagehide', persistBeforeLeaving)
  void restoreSession()
  parsePoller = window.setInterval(() => {
    if (activeSection.value === 'documents' && selected.value) void loadParseOverview(true)
    if (activeSection.value === 'documents' && selectedDocument.value && versions.value.some(version => version.job_status === 'QUEUED' || version.job_status === 'RUNNING')) {
      refreshVersionStates().catch(() => undefined)
    }
  }, 3000)
  graphProgressPoller = window.setInterval(() => {
    if (activeSection.value === 'graph' && selected.value) void loadGraphProgress(true)
  }, 10000)
})
onUnmounted(() => {
  window.removeEventListener('pointerdown', handleActivity, true)
  window.removeEventListener('keydown', handleActivity, true)
  window.removeEventListener('click', handleActivity, true)
  window.removeEventListener('wheel', handleActivity, true)
  window.removeEventListener('pagehide', persistBeforeLeaving)
  if (parsePoller) window.clearInterval(parsePoller)
  if (graphProgressPoller) window.clearInterval(graphProgressPoller)
  chatAbortController?.abort()
})

async function refreshVersionStates(): Promise<void> {
  if (!selected.value || !selectedDocument.value) return
  const base = `/knowledge-bases/${selected.value.id}/documents/${selectedDocument.value.id}`
  const [freshVersions, freshDocument] = await Promise.all([
    api<DocumentVersion[]>(`${base}/versions`), api<Document>(base),
  ])
  versions.value = freshVersions
  selectedDocument.value = freshDocument
  documents.value = documents.value.map(doc => doc.id === freshDocument.id ? freshDocument : doc)
}

async function requestParse(version: DocumentVersion): Promise<void> {
  if (!selected.value || !selectedDocument.value) return
  try {
    if (version.status === 'PARSED') {
      await ElMessageBox.confirm('重新解析期间，这个版本会暂时退出检索；重新生成片段、向量和图谱可能消耗模型额度。确认继续吗？', '重新解析文档', { type: 'warning', confirmButtonText: '重新解析', cancelButtonText: '取消' })
    }
    await api(`/knowledge-bases/${selected.value.id}/documents/${selectedDocument.value.id}/versions/${version.version_no}/parse${version.status === 'PARSED' || version.status === 'FAILED' ? '?force=true' : ''}`, { method: 'POST' })
    await refreshVersionStates()
    await loadParseOverview(true)
    ElMessage.success('解析任务已提交')
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message) }
}

async function openParsed(version: DocumentVersion): Promise<void> {
  if (!selected.value || !selectedDocument.value) return
  try {
    const result = await api<ParsedDocument>(`/knowledge-bases/${selected.value.id}/documents/${selectedDocument.value.id}/versions/${version.version_no}/parsed`)
    previewLabel.value = `v${version.version_no} · 解析内容 · ${result.parser_name}`
    previewContent.value = result.markdown
    parsedResult.value = result
    parsedPage.value = result.structure.pages[0]?.number ?? 1
  } catch (error) { ElMessage.error((error as Error).message) }
}

async function refreshDocuments(append = false): Promise<void> {
  if (!selected.value) {
    documents.value = []
    documentTotal.value = 0
    allDocumentTotal.value = 0
    return
  }
  const page = append ? documentPage.value + 1 : 1
  const normalizedQuery = searchText.value.trim()
  if (showDeleted.value) {
    const params = new URLSearchParams({ deleted: 'true', page: String(page), page_size: '20' })
    if (normalizedQuery) params.set('q', normalizedQuery)
    const result = await apiPage<Document>(`/knowledge-bases/${selected.value.id}/documents?${params}`)
    searchMode.value = false
    documents.value = append ? [...documents.value, ...result.items] : result.items
    documentTotal.value = result.total
    documentPage.value = page
    return
  }
  if (normalizedQuery) {
    const params = new URLSearchParams({ q: normalizedQuery, page: String(page), page_size: '20' })
    if (filterTag.value.trim()) params.set('tag', filterTag.value.trim())
    if (filterSource.value.trim()) params.set('source', filterSource.value.trim())
    if (filterType.value) params.set('file_type', filterType.value)
    const result = await apiPage<DocumentSearchResult>(`/knowledge-bases/${selected.value.id}/search?${params}`)
    searchMode.value = true
    searchResults.value = append ? [...searchResults.value, ...result.items] : result.items
    documents.value = searchResults.value.map(item => item.document)
    documentTotal.value = result.total
    documentPage.value = page
    return
  }
  searchMode.value = false
  searchResults.value = []
  const params = new URLSearchParams({ page: String(page), page_size: '20' })
  if (searchText.value.trim()) params.set('q', searchText.value.trim())
  if (filterTag.value.trim()) params.set('tag', filterTag.value.trim())
  if (filterSource.value.trim()) params.set('source', filterSource.value.trim())
  if (filterType.value) params.set('file_type', filterType.value)
  const result = await apiPage<Document>(`/knowledge-bases/${selected.value.id}/documents?${params}`)
  documents.value = append ? [...documents.value, ...result.items] : result.items
  documentTotal.value = result.total
  if (!searchText.value.trim() && !filterTag.value.trim() && !filterSource.value.trim() && !filterType.value) allDocumentTotal.value = result.total
  documentPage.value = page
}

function resultFor(docId: string): DocumentSearchResult | undefined {
  return searchResults.value.find(item => item.document.id === docId)
}

async function loadChatSessions(): Promise<void> {
  if (!selected.value) return
  chatSessions.value = await api<ChatSession[]>(`/knowledge-bases/${selected.value.id}/chat-sessions`)
}

async function openChatSession(session: ChatSession): Promise<void> {
  if (!selected.value || asking.value) return
  activeChatSession.value = session
  chatMessages.value = await api<ChatMessage[]>(`/knowledge-bases/${selected.value.id}/chat-sessions/${session.id}/messages`)
}

async function newChatSession(preserveMessages = false): Promise<void> {
  if (!selected.value || (asking.value && !preserveMessages)) return
  const session = await api<ChatSession>(`/knowledge-bases/${selected.value.id}/chat-sessions`, { method: 'POST', body: JSON.stringify({ title: '新对话' }) })
  chatSessions.value = [session, ...chatSessions.value]
  activeChatSession.value = session
  if (!preserveMessages) chatMessages.value = []
}

function scrollChatBottom(): void {
  void nextTick(() => { if (chatHistoryRef.value) chatHistoryRef.value.scrollTop = chatHistoryRef.value.scrollHeight })
}

function stopAnswer(): void { chatAbortController?.abort() }

async function showCitation(citation: ChatMessage['citations'][number]): Promise<void> {
  if (!selected.value) return
  selectedCitation.value = citation
  citationSource.value = null
  citationOpen.value = true
  citationLoading.value = true
  try {
    const result = await api<CitationSource>(`/knowledge-bases/${selected.value.id}/citations/${citation.chunk_id}`)
    if (selectedCitation.value?.chunk_id === citation.chunk_id) citationSource.value = result
  } catch {
    // A historical citation can outlive its indexed chunk after a reparse.
  } finally { citationLoading.value = false }
}

async function showCitationDocument(): Promise<void> {
  if (!selected.value || !selectedCitation.value) return
  try {
    const doc = await api<Document>(`/knowledge-bases/${selected.value.id}/documents/${selectedCitation.value.document_id}`)
    activeSection.value = 'documents'
    citationOpen.value = false
    await viewDocument(doc)
  } catch (error) { ElMessage.error((error as Error).message) }
}

async function openCitationOriginal(): Promise<void> {
  if (!selected.value || !selectedCitation.value || !citationSource.value) return
  const source = citationSource.value
  const path = `/knowledge-bases/${selected.value.id}/documents/${source.document_id}/versions/${source.version_no}/download`
  try {
    if (source.file_type === 'pdf') await openPdf(path, citationPage.value ?? null)
    else await download(path, source.original_filename)
  } catch (error) { ElMessage.error((error as Error).message) }
}

async function askKnowledgeBase(): Promise<void> {
  if (!selected.value || !question.value.trim() || asking.value) return
  const submitted = question.value.trim()
  const kbId = selected.value.id
  const now = new Date().toISOString()
  const pendingUser: ChatMessage = { id: `pending-user-${Date.now()}`, session_id: activeChatSession.value?.id ?? '', role: 'user', content: submitted, citations: [], model: null, created_at: now }
  const pendingAnswer = reactive<ChatMessage>({ id: `pending-answer-${Date.now()}`, session_id: activeChatSession.value?.id ?? '', role: 'assistant', content: '', citations: [], model: null, created_at: now })
  chatMessages.value.push(pendingUser, pendingAnswer)
  question.value = ''
  asking.value = true
  scrollChatBottom()
  let receivedAnswer = ''
  let finalAnswer = ''
  let typingQueue: string[] = []
  let typingTimer: ReturnType<typeof setInterval> | null = null
  let resolveDrain: (() => void) | null = null
  const stopTyping = () => {
    if (typingTimer !== null) clearInterval(typingTimer)
    typingTimer = null
    resolveDrain?.()
    resolveDrain = null
  }
  const typeNext = () => {
    if (!typingQueue.length) { stopTyping(); return }
    const count = Math.min(8, Math.max(1, Math.ceil(typingQueue.length / 60)))
    pendingAnswer.content += typingQueue.splice(0, count).join('')
    scrollChatBottom()
    if (!typingQueue.length) stopTyping()
  }
  const queueText = (text: string) => {
    receivedAnswer += text
    typingQueue.push(...Array.from(text))
    if (typingTimer === null) typingTimer = setInterval(typeNext, 18)
  }
  try {
    if (!activeChatSession.value) await newChatSession(true)
    if (!activeChatSession.value) return
    const sessionId = activeChatSession.value.id
    chatAbortController = new AbortController()
    const signal = chatAbortController.signal
    signal.addEventListener('abort', stopTyping, { once: true })
    let completed = false
    await apiStream(`/knowledge-bases/${kbId}/chat-sessions/${sessionId}/messages/stream`, { question: submitted }, (event, payload) => {
      if (event === 'delta') queueText(String(payload.content ?? ''))
      if (event === 'done') {
        finalAnswer = String(payload.answer ?? '')
        pendingAnswer.citations = (payload.citations ?? []) as ChatMessage['citations']
        completed = true
      }
      if (event === 'error') throw new Error(String(payload.message ?? '生成回答失败'))
    }, signal)
    if (!completed) throw new Error('回答流中断，请重试')
    if (finalAnswer !== receivedAnswer.trim()) {
      stopTyping()
      typingQueue = []
    } else if (typingQueue.length) {
      await new Promise<void>(resolve => { resolveDrain = resolve })
    }
    if (signal.aborted) throw new DOMException('已停止生成', 'AbortError')
    pendingAnswer.content = finalAnswer
    scrollChatBottom()
    await loadChatSessions()
    const refreshed = chatSessions.value.find(session => session.id === sessionId)
    if (refreshed) activeChatSession.value = refreshed
    if (selected.value?.id === kbId && activeChatSession.value?.id === sessionId) {
      chatMessages.value = await api<ChatMessage[]>(`/knowledge-bases/${kbId}/chat-sessions/${sessionId}/messages`)
      scrollChatBottom()
    }
    await refreshCloudBudget()
  } catch (error) {
    chatMessages.value = chatMessages.value.filter(message => message !== pendingUser && message !== pendingAnswer)
    if (selected.value?.id === kbId) question.value = submitted
    ElMessage.error((error as Error).name === 'AbortError' ? '已停止生成，可重新发送问题' : (error as Error).message)
  } finally { stopTyping(); asking.value = false; chatAbortController = null }
}

watch(selected, async () => {
  chatAbortController?.abort()
  selectedDocument.value = null
  versions.value = []
  previewContent.value = ''
  previewLabel.value = ''
  parsedResult.value = null
  knowledgeGraph.value = null
  graphProgress.value = null
  parseOverview.value = null
  activeChatSession.value = null
  chatMessages.value = []
  await loadChatSessions()
  searchText.value = ''
  filterTag.value = ''
  filterSource.value = ''
  filterType.value = ''
  try {
    await refreshDocuments()
    if (activeSection.value === 'graph') {
      void loadKnowledgeGraph()
      void loadGraphProgress()
    }
  } catch (error) { ElMessage.error((error as Error).message) }
})

async function loadKnowledgeGraph(): Promise<void> {
  if (!selected.value) { knowledgeGraph.value = null; return }
  const kbId = selected.value.id
  graphLoading.value = true
  try {
    const result = await api<KnowledgeGraphData>(`/knowledge-bases/${kbId}/knowledge-graph`)
    if (selected.value?.id === kbId) knowledgeGraph.value = result
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { if (selected.value?.id === kbId) graphLoading.value = false }
}

async function loadParseOverview(silent = false): Promise<void> {
  if (!selected.value) { parseOverview.value = null; return }
  const kbId = selected.value.id
  try {
    const result = await api<DocumentParseOverview>(`/knowledge-bases/${kbId}/documents/parse-issues`)
    if (selected.value?.id === kbId) {
      const previous = parseOverview.value
      parseOverview.value = result
      if (previous && (previous.parsed_documents !== result.parsed_documents ||
        JSON.stringify(previous.issues.map(issue => [issue.document_id, issue.status, issue.job_status])) !==
        JSON.stringify(result.issues.map(issue => [issue.document_id, issue.status, issue.job_status])))) {
        void refreshDocuments().catch(() => undefined)
      }
    }
  } catch (error) { if (!silent) ElMessage.error((error as Error).message) }
}

async function reparseIssue(issue: DocumentParseIssue): Promise<void> {
  if (!selected.value || parseSubmitting.value || !issue.supported) return
  const largePdf = /\.pdf$/i.test(issue.original_filename) && /页数超过/.test(issue.parse_error ?? '')
  try {
    if (largePdf) await ElMessageBox.confirm('这份 PDF 页数很多，重新解析和后续索引可能需要较长时间。确认提交吗？', '重新解析超大文档', { type: 'warning', confirmButtonText: '提交任务', cancelButtonText: '取消' })
    parseSubmitting.value = issue.document_id
    await api(`/knowledge-bases/${selected.value.id}/documents/${issue.document_id}/versions/${issue.version_no}/parse?force=true`, { method: 'POST' })
    await loadParseOverview()
    await refreshDocuments()
    if (activeSection.value === 'documents') void loadParseOverview()
    if (selectedDocument.value?.id === issue.document_id) await refreshVersionStates()
    ElMessage.success('重新解析任务已提交')
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message) }
  finally { parseSubmitting.value = null }
}

async function loadGraphProgress(silent = false): Promise<void> {
  if (!selected.value) { graphProgress.value = null; return }
  const kbId = selected.value.id
  try {
    const result = await api<KnowledgeGraphProgress>(`/knowledge-bases/${kbId}/knowledge-graph/progress`)
    if (selected.value?.id === kbId) graphProgress.value = result
  } catch (error) { if (!silent) ElMessage.error((error as Error).message) }
}

function refreshGraphPage(): void {
  void loadKnowledgeGraph()
  void loadGraphProgress()
}

function openGraph(): void {
  activeSection.value = 'graph'
  refreshGraphPage()
}

async function viewDocument(doc: Document): Promise<void> {
  selectedDocument.value = doc
  editTitle.value = doc.title
  editDescription.value = doc.description
  editSource.value = doc.source
  editTags.value = doc.tags.join(', ')
  previewContent.value = ''
  previewLabel.value = ''
  parsedResult.value = null
  uploadFiles.value = []
  const input = document.querySelector<HTMLInputElement>('#document-file')
  if (input) input.value = ''
  try {
    versions.value = await api<DocumentVersion[]>(`/knowledge-bases/${doc.knowledge_base_id}/documents/${doc.id}/versions`)
  } catch (error) { ElMessage.error((error as Error).message) }
}

async function switchDocumentBin(deleted: boolean): Promise<void> {
  showDeleted.value = deleted
  selectedDocument.value = null
  versions.value = []
  await refreshDocuments()
}

async function deleteSelectedDocument(): Promise<void> {
  if (!selected.value || !selectedDocument.value) return
  try {
    await ElMessageBox.confirm('文档将移入回收站，并立即从搜索、问答和知识图谱中移除。原文件与历史版本会保留，可随时恢复。', '删除文档', { type: 'warning', confirmButtonText: '移入回收站', cancelButtonText: '取消' })
    await api(`/knowledge-bases/${selected.value.id}/documents/${selectedDocument.value.id}`, { method: 'DELETE' })
    selectedDocument.value = null
    versions.value = []
    await refreshDocuments()
    ElMessage.success('已移入回收站')
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message) }
}

async function restoreSelectedDocument(): Promise<void> {
  if (!selected.value || !selectedDocument.value) return
  try {
    await api<Document>(`/knowledge-bases/${selected.value.id}/documents/${selectedDocument.value.id}/restore`, { method: 'POST' })
    selectedDocument.value = null
    versions.value = []
    await refreshDocuments()
    ElMessage.success('文档已恢复，索引正在同步')
  } catch (error) { ElMessage.error((error as Error).message) }
}

async function rollbackVersion(version: DocumentVersion): Promise<void> {
  if (!selected.value || !selectedDocument.value) return
  try {
    await ElMessageBox.confirm(`将 v${version.version_no} 设为当前版本？原有版本仍会保留。`, '回滚文档版本', { type: 'warning', confirmButtonText: '确认回滚', cancelButtonText: '取消' })
    const updated = await api<Document>(`/knowledge-bases/${selected.value.id}/documents/${selectedDocument.value.id}/versions/${version.version_no}/rollback`, { method: 'POST' })
    await refreshDocuments()
    await viewDocument(updated)
    ElMessage.success('已回滚，索引正在同步')
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message) }
}

async function saveMetadata(): Promise<void> {
  if (!selected.value || !selectedDocument.value) return
  try {
    const updated = await api<Document>(`/knowledge-bases/${selected.value.id}/documents/${selectedDocument.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({ title: editTitle.value, description: editDescription.value, source: editSource.value, tags: editTags.value.split(',').map(tag => tag.trim()).filter(Boolean) }),
})

watch(activeSection, section => {
  if (section === 'documents') void loadParseOverview()
})
    await refreshDocuments()
    await viewDocument(updated)
    ElMessage.success('文档信息已保存')
  } catch (error) { ElMessage.error((error as Error).message) }
}

async function previewVersion(version: DocumentVersion): Promise<void> {
  if (!selected.value || !selectedDocument.value) return
  try {
    const result = await api<{ format: string; content: string }>(`/knowledge-bases/${selected.value.id}/documents/${selectedDocument.value.id}/versions/${version.version_no}/preview`)
    previewLabel.value = `v${version.version_no} · ${version.original_filename}`
    previewContent.value = result.content
    parsedResult.value = null
  } catch (error) { ElMessage.error((error as Error).message) }
}

function chooseFile(event: Event): void {
  uploadFiles.value = Array.from((event.target as HTMLInputElement).files ?? [])
  const savedOnly = uploadFiles.value.filter(file => uploadOnly(file.name))
  if (savedOnly.length) ElMessage.info(`${savedOnly.length} 份旧版 PPT/XLS 文件只保存原件；请转换为 PPTX/XLSX 后上传以参与检索和问答`)
}

async function upload(): Promise<void> {
  if (!selected.value || !uploadFiles.value.length) return
  if (uploadFiles.value.length > 10) { ElMessage.error('每批最多上传 10 个文件'); return }
  const body = new FormData()
  uploading.value = true
  try {
    const base = `/knowledge-bases/${selected.value.id}/documents`
    let partialFailure = false
    if (selectedDocument.value) {
      body.append('file', uploadFiles.value[0])
      await api(`${base}/${selectedDocument.value.id}/versions`, { method: 'POST', body })
    } else if (uploadFiles.value.length > 1) {
      uploadFiles.value.forEach(file => body.append('files', file))
      const result = await api<{ items: Document[]; errors: { filename: string; detail: string }[] }>(`${base}/batch`, { method: 'POST', body })
      partialFailure = result.errors.length > 0
      if (partialFailure) ElMessage.warning(`${result.items.length} 个成功，${result.errors.length} 个失败：${result.errors.map(item => item.filename).join('、')}`)
    } else {
      body.append('file', uploadFiles.value[0])
      await api(base, { method: 'POST', body })
    }
    uploadFiles.value = []
    const input = document.querySelector<HTMLInputElement>('#document-file')
    if (input) input.value = ''
    await refreshDocuments()
    if (searchText.value.trim() || filterTag.value.trim() || filterSource.value.trim() || filterType.value) {
      const total = await apiPage<Document>(`${base}?page=1&page_size=1`)
      allDocumentTotal.value = total.total
    }
    if (selectedDocument.value) {
      const updated = documents.value.find(doc => doc.id === selectedDocument.value?.id)
      if (updated) await viewDocument(updated)
    }
    if (!partialFailure) ElMessage.success('上传处理已完成')
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { uploading.value = false }
}

async function downloadVersion(version: DocumentVersion): Promise<void> {
  if (!selected.value || !selectedDocument.value) return
  try {
    await download(`/knowledge-bases/${selected.value.id}/documents/${selectedDocument.value.id}/versions/${version.version_no}/download`, version.original_filename)
  } catch (error) { ElMessage.error((error as Error).message) }
}

async function refresh(): Promise<void> {
  knowledgeBases.value = await api<KnowledgeBase[]>('/knowledge-bases')
  if (currentUser.value?.role === 'ADMIN') users.value = await api<User[]>('/users')
  await refreshCloudBudget()
  selected.value = knowledgeBases.value.find(kb => kb.id === selected.value?.id) ?? knowledgeBases.value[0] ?? null
}

async function refreshCloudBudget(): Promise<void> {
  if (currentUser.value?.role !== 'ADMIN') return
  try {
    const previous = cloudBudget.value?.alert_level ?? 0
    cloudBudget.value = await api<CloudBudgetSummary>('/model-usage/monthly')
    if (cloudBudget.value.alert_level >= 70 && cloudBudget.value.alert_level > previous) {
      ElMessage.warning(`云模型月度预算已使用 ${cloudBudget.value.alert_level}% 档位，请查看工作台。`)
    }
  } catch (error) { ElMessage.warning(`预算状态读取失败：${(error as Error).message}`) }
}

async function login(): Promise<void> {
  busy.value = true
  try {
    const token = await api<{ access_token: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: username.value, password: password.value }),
    })
    setToken(token.access_token)
    currentUser.value = await api<User>('/auth/me')
    sessionToken = token.access_token
    lastRenewedAt = Date.now()
    persistActivity(Date.now())
    password.value = ''
    await refresh()
  } catch (error) {
    clearSession()
    ElMessage.error((error as Error).message)
  } finally {
    busy.value = false
  }
}

function clearSession(): void {
  try { clearStoredSession(sessionStorage) } catch { /* Storage may be unavailable. */ }
  sessionToken = ''
  lastActivityAt = 0
  lastStoredAt = 0
  lastRenewedAt = 0
  lastRenewAttemptAt = 0
  setToken('')
  chatAbortController?.abort()
  currentUser.value = null
  knowledgeBases.value = []
  selected.value = null
  documents.value = []
  selectedDocument.value = null
  versions.value = []
  users.value = []
  cloudBudget.value = null
  chatSessions.value = []
  activeChatSession.value = null
  chatMessages.value = []
  question.value = ''
  asking.value = false
  activeSection.value = 'overview'
  createKbDialogVisible.value = false
  passwordDialogVisible.value = false
}

async function logout(): Promise<void> {
  try {
    await api<void>('/auth/logout', { method: 'POST' })
  } catch (error) {
    ElMessage.warning((error as Error).message)
  } finally {
    clearSession()
  }
}

function handleAccountCommand(command: string): void {
  if (command === 'password') passwordDialogVisible.value = true
  if (command === 'logout') void logout()
}

async function changePassword(): Promise<void> {
  if (!currentPassword.value || changedPassword.value.length < 12 || changedPassword.value.length > 128) {
    ElMessage.warning('新密码长度需为 12 至 128 位')
    return
  }
  if (changedPassword.value !== confirmPassword.value) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  changingPassword.value = true
  try {
    await api<void>('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword.value, new_password: changedPassword.value }),
    })
    passwordDialogVisible.value = false
    currentPassword.value = ''
    changedPassword.value = ''
    confirmPassword.value = ''
    clearSession()
    ElMessage.success('密码已修改，请用新密码重新登录')
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { changingPassword.value = false }
}

function openCreateKbDialog(): void {
  newKbName.value = ''
  newKbDescription.value = ''
  createKbDialogVisible.value = true
}

async function createKb(): Promise<void> {
  if (!newKbName.value.trim() || creatingKb.value) return
  creatingKb.value = true
  try {
    const created = await api<KnowledgeBase>('/knowledge-bases', {
      method: 'POST',
      body: JSON.stringify({ name: newKbName.value.trim(), description: newKbDescription.value.trim() }),
    })
    newKbName.value = ''
    newKbDescription.value = ''
    createKbDialogVisible.value = false
    await refresh()
    selected.value = created
    activeSection.value = 'documents'
    ElMessage.success('知识库已创建')
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally { creatingKb.value = false }
}

async function createUser(): Promise<void> {
  try {
    await api<User>('/users', {
      method: 'POST',
      body: JSON.stringify({ username: newUsername.value, password: newPassword.value }),
    })
    newUsername.value = ''
    newPassword.value = ''
    await refresh()
    ElMessage.success('成员已创建')
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function grantMember(): Promise<void> {
  if (!selected.value) return
  try {
    await api(`/knowledge-bases/${selected.value.id}/members`, {
      method: 'POST',
      body: JSON.stringify({ user_id: memberId.value, member_role: memberRole.value }),
    })
    memberId.value = ''
    ElMessage.success('已授权成员')
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function toggleCloudAccess(): Promise<void> {
  if (!selected.value || currentUser.value?.role !== 'ADMIN') return
  const enabled = !selected.value.cloud_enabled
  if (enabled) {
    try {
      await ElMessageBox.confirm('启用后，提问时命中的资料片段会发送给已配置的云端大模型。请确认该知识库允许外发。', '启用云端问答', { confirmButtonText: '确认启用', cancelButtonText: '取消', type: 'warning' })
    } catch { return }
  }
  cloudBusy.value = true
  try {
    const updated = await api<KnowledgeBase>(`/knowledge-bases/${selected.value.id}/cloud-access`, {
      method: 'PATCH', body: JSON.stringify({ enabled }),
    })
    selected.value = updated
    knowledgeBases.value = knowledgeBases.value.map(kb => kb.id === updated.id ? updated : kb)
    ElMessage.success(enabled ? '云端问答已启用' : '云端问答已关闭')
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { cloudBusy.value = false }
}
</script>

<template>
  <div v-if="restoringSession" class="session-restoring" role="status">正在恢复工作空间…</div>
  <div v-else-if="!currentUser" class="auth-shell">
    <section class="auth-showcase">
      <div class="auth-brand">
        <span class="brand-mark">K<span class="brand-dot"></span></span>
        <div><strong>KNOWLEDGE SPACE</strong><small>个人知识库管理平台</small></div>
      </div>
      <div class="showcase-copy">
        <span class="showcase-kicker">YOUR KNOWLEDGE, ORGANIZED.</span>
        <h1>让每一份知识<br /><em>都有清晰的归属。</em></h1>
        <p>在统一的工作空间中管理资料、追踪版本、连接团队，让知识沉淀成为日常工作的一部分。</p>
        <div class="showcase-feature">
          <span class="feature-glyph">01</span><div><strong>统一管理</strong><small>知识库、文档和成员集中呈现</small></div>
        </div>
        <div class="showcase-feature">
          <span class="feature-glyph">02</span><div><strong>版本可溯</strong><small>每一次上传都有独立记录</small></div>
        </div>
      </div>
      <div class="showcase-footer"><span class="signal-dot"></span> SECURE KNOWLEDGE WORKSPACE <span>© 2026</span></div>
      <div class="showcase-art" aria-hidden="true"><span></span><span></span><span></span></div>
    </section>
    <section class="auth-form-side">
      <div class="auth-form-wrap">
        <span class="overline">WELCOME BACK</span>
        <h2>登录工作空间</h2>
        <p>使用您的账号访问个人知识库。</p>
        <el-form class="auth-form" label-position="top" @submit.prevent="login">
          <el-form-item label="用户名">
            <el-input v-model="username" size="large" autocomplete="username" placeholder="请输入用户名" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="password" size="large" type="password" autocomplete="current-password" placeholder="请输入密码" show-password />
          </el-form-item>
          <el-button type="primary" size="large" native-type="submit" :loading="busy" class="full-button">登录工作空间 <span class="button-arrow">→</span></el-button>
        </el-form>
        <div class="auth-note"><span class="note-icon">i</span> 账号由管理员创建。如需访问权限，请联系管理员。</div>
      </div>
      <div class="auth-form-footer">KNOWLEDGE SPACE · PERSONAL EDITION</div>
    </section>
  </div>

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <span class="brand-mark">K<span class="brand-dot"></span></span>
        <div><strong>KNOWLEDGE</strong><small>SPACE / 知识空间</small></div>
      </div>
      <el-dropdown class="workspace-switcher" trigger="click" @command="(id: string) => { if (id === '__create__') openCreateKbDialog(); else selectedId = id }">
        <button class="sidebar-workspace" type="button" aria-label="切换知识库">
          <span class="workspace-icon">K</span>
          <span class="workspace-copy"><small>当前知识库</small><strong>{{ selected?.name || '选择知识库' }}</strong></span>
          <svg class="workspace-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 6 4.5 4 4.5-4" /></svg>
        </button>
        <template #dropdown><el-dropdown-menu>
          <el-dropdown-item v-for="kb in knowledgeBases" :key="kb.id" :command="kb.id" :class="{ 'workspace-current': selected?.id === kb.id }">{{ kb.name }}</el-dropdown-item>
          <el-dropdown-item divided command="__create__">＋ 新建知识库</el-dropdown-item>
        </el-dropdown-menu></template>
      </el-dropdown>
      <div class="nav-caption">工作空间</div>
      <nav class="sidebar-nav" aria-label="主导航">
        <button aria-label="工作台" :class="{ active: activeSection === 'overview' }" @click="activeSection = 'overview'"><span class="nav-glyph"><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="2.5" width="6" height="6" rx="1"/><rect x="11.5" y="2.5" width="6" height="6" rx="1"/><rect x="2.5" y="11.5" width="6" height="6" rx="1"/><rect x="11.5" y="11.5" width="6" height="6" rx="1"/></svg></span><span>工作台</span></button>
        <button aria-label="文档中心" :class="{ active: activeSection === 'documents' }" @click="activeSection = 'documents'"><span class="nav-glyph"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 2.5h7l3 3v12H5z"/><path d="M12 2.5v3h3M8 9h5M8 12h5M8 15h4"/></svg></span><span>文档中心</span><span v-if="allDocumentTotal" class="nav-count">{{ allDocumentTotal }}</span></button>
        <button aria-label="资料问答" :class="{ active: activeSection === 'ask' }" @click="activeSection = 'ask'"><span class="nav-glyph"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 4.5h14v10H9l-4 3v-3H3z"/><path d="M6.5 8h7M6.5 11h5"/></svg></span><span>资料问答</span></button>
        <button aria-label="知识图谱" :class="{ active: activeSection === 'graph' }" @click="openGraph()"><span class="nav-glyph"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="4" cy="10" r="2"/><circle cx="15.5" cy="4" r="2"/><circle cx="15.5" cy="16" r="2"/><path d="m6 9 7.5-4M6 11l7.5 4"/></svg></span><span>知识图谱</span></button>
        <button aria-label="用户管理" :class="{ active: activeSection === 'team' }" @click="activeSection = 'team'"><span class="nav-glyph"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="6.5" r="3"/><path d="M3.5 17c0-3.3 2.6-5.3 6.5-5.3s6.5 2 6.5 5.3"/></svg></span><span>用户管理</span></button>
      </nav>
      <div class="sidebar-bottom">
        <div class="sidebar-help"><span class="signal-dot"></span><div><strong>知识工作空间</strong><small>资料管理与问答</small></div></div>
        <button class="sidebar-profile" type="button" :class="{ active: activeSection === 'team' }" title="进入用户管理" aria-label="进入用户管理" @click="activeSection = 'team'">
          <span class="avatar">{{ currentUser.username.slice(0, 1).toUpperCase() }}</span>
          <span class="profile-copy"><strong>{{ currentUser.username }}</strong><small>{{ currentUser.role === 'ADMIN' ? '管理员' : '成员' }}</small></span>
          <span class="profile-arrow" aria-hidden="true"><svg viewBox="0 0 20 20"><path d="m7.5 5 5 5-5 5" /></svg></span>
        </button>
      </div>
    </aside>

    <div class="app-main">
      <header class="app-header">
        <div class="breadcrumb"><span>知识空间</span><span class="crumb-divider">/</span><strong>{{ activeSection === 'overview' ? '工作台' : activeSection === 'documents' ? '文档中心' : activeSection === 'ask' ? '资料问答' : activeSection === 'graph' ? '知识图谱' : '用户管理' }}</strong></div>
        <div class="header-right"><span class="header-status"><span class="signal-dot"></span> 系统运行中</span><span class="header-divider"></span>
          <el-dropdown trigger="click" @command="handleAccountCommand">
            <button class="account-trigger" type="button" aria-label="账号菜单"><span class="header-avatar">{{ currentUser.username.slice(0, 1).toUpperCase() }}</span><span class="account-name">{{ currentUser.username }}</span><svg class="account-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 6 4.5 4 4.5-4" /></svg></button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="password">修改密码</el-dropdown-item><el-dropdown-item command="logout" divided>退出登录</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
        </div>
      </header>

      <main class="content" :class="{ 'content--ask': activeSection === 'ask' }">
        <template v-if="activeSection === 'overview'">
          <div class="page-heading">
            <div><span class="overline">WORKSPACE OVERVIEW</span><h1>工作台</h1><p>在这里查看知识空间的概况，快速进入常用工作。</p></div>
            <div class="page-actions"><el-button @click="activeSection = 'documents'">进入文档中心</el-button><el-button type="primary" @click="openCreateKbDialog">＋ 新建知识库</el-button></div>
          </div>
          <section class="workspace-summary surface">
            <div class="workspace-summary-main"><span class="workspace-summary-kicker">当前工作空间</span><h2>{{ selected?.name || '开始使用知识空间' }}</h2><p>{{ selected?.description || (selected ? '在此管理文档、检索内容并查看知识关系。' : '创建知识库并上传资料，开始建立可检索的知识空间。') }}</p></div>
            <div class="workspace-summary-actions"><el-button type="primary" @click="selected ? activeSection = 'documents' : openCreateKbDialog()">{{ selected ? '管理文档' : '新建知识库' }}</el-button><el-button v-if="selected" @click="activeSection = 'ask'">进入资料问答</el-button></div>
            <div class="workspace-facts"><div><span>可访问知识库</span><strong>{{ knowledgeBases.length }}</strong></div><div><span>当前库文档</span><strong>{{ selected ? allDocumentTotal : '—' }}</strong></div><div><span>访问身份</span><strong>{{ currentUser.role === 'ADMIN' ? '管理员' : '成员' }}</strong></div><div><span>云端问答</span><strong>{{ selected ? (selected.cloud_enabled ? '已启用' : '未启用') : '—' }}</strong></div></div>
          </section>
          <div class="overview-grid">
            <section class="surface">
              <div class="section-heading"><div><span class="overline">LIBRARIES</span><h3>我的知识库</h3><p class="section-description">选择知识库，继续管理资料与关系。</p></div><el-button text type="primary" @click="openCreateKbDialog">＋ 新建</el-button></div>
              <div v-if="knowledgeBases.length" class="library-list">
                <button v-for="kb in knowledgeBases" :key="kb.id" class="library-row" :class="{ selected: selected?.id === kb.id }" @click="selected = kb">
                  <span class="library-icon">KB</span><span class="library-copy"><strong>{{ kb.name }}</strong><small>{{ kb.description || '尚未添加描述' }}</small></span><span class="library-row-state">{{ kb.cloud_enabled ? '可问答' : '仅本地资料' }}</span><span class="library-row-date">{{ new Date(kb.created_at).toLocaleDateString() }}</span><span class="row-arrow">→</span>
                </button>
              </div>
              <div v-else class="library-empty"><el-empty description="还没有知识库" /><el-button type="primary" @click="openCreateKbDialog">创建第一个知识库</el-button></div>
            </section>
            <div class="overview-side">
              <section v-if="currentUser.role === 'ADMIN' && cloudBudget" class="surface budget-card">
                <div class="section-heading"><div><span class="overline">CLOUD MODEL BUDGET</span><h3>云模型月度预算（估算）</h3></div><span class="section-count">{{ cloudBudget.month }}</span></div>
                <div class="budget-amount">¥{{ cloudBudget.charged_cny.toFixed(2) }} <small>/ ¥{{ cloudBudget.budget_cny.toFixed(2) }}</small></div>
                <p class="budget-note">按模型返回的 token 用量和配置单价估算，实际费用以 DeepSeek 账单为准。</p>
                <div class="budget-track"><span :style="{ width: `${Math.min(100, (cloudBudget.charged_cny + cloudBudget.reserved_cny) / cloudBudget.budget_cny * 100)}%` }"></span></div>
                <p v-if="cloudBudget.alert_level >= 100" class="budget-alert">预算已用尽，云端问答已暂停。</p>
                <p v-else-if="cloudBudget.alert_level >= 70" class="budget-alert">已达到 {{ cloudBudget.alert_level }}% 预警档位，剩余 ¥{{ cloudBudget.remaining_cny.toFixed(2) }}。</p>
                <p v-else>剩余 ¥{{ cloudBudget.remaining_cny.toFixed(2) }}，费用按保守单价估算。</p>
              </section>
              <section class="surface selected-library">
                <div class="section-heading"><div><span class="overline">SELECTED SPACE</span><h3>当前知识库</h3></div><span v-if="selected" class="status-pill">使用中</span></div>
                <template v-if="selected">
                  <h4>{{ selected.name }}</h4>
                  <p>{{ selected.description || '为这个知识库添加文档，建立可追溯的资料空间。' }}</p>
                  <div class="selected-library-fields"><div><span>云端问答</span><strong>{{ selected.cloud_enabled ? '已启用' : '未启用' }}</strong></div><div><span>创建时间</span><strong>{{ new Date(selected.created_at).toLocaleDateString() }}</strong></div></div>
                  <div class="selected-library-actions"><el-button text type="primary" @click="activeSection = 'documents'">查看文档 →</el-button><el-button v-if="currentUser.role === 'ADMIN'" text :loading="cloudBusy" @click="toggleCloudAccess">{{ selected.cloud_enabled ? '关闭云端问答' : '启用云端问答' }}</el-button></div>
                </template>
                <p v-else>创建或选择一个知识库开始使用。</p>
              </section>
            </div>
          </div>
        </template>

        <template v-else-if="activeSection === 'ask'">
          <div class="page-heading">
            <div><span class="overline">GROUNDED ANSWERS</span><h1>资料问答</h1><p>回答仅基于当前知识库中已解析、且您有权访问的资料，并附带原文引用。</p></div>
            <div class="heading-select"><span>知识库</span><el-select v-model="selectedId" placeholder="选择知识库"><el-option v-for="kb in knowledgeBases" :key="kb.id" :label="kb.name" :value="kb.id" /></el-select><el-button @click="openCreateKbDialog">＋ 新建</el-button></div>
          </div>
          <div v-if="selected" class="chat-layout">
            <aside class="surface chat-sessions"><div class="section-heading"><div><span class="overline">HISTORY</span><h3>对话</h3></div><el-button link type="primary" :disabled="asking" @click="newChatSession()">新对话</el-button></div><div class="chat-session-list"><button v-for="session in chatSessions" :key="session.id" class="chat-session-row" :class="{ active: activeChatSession?.id === session.id }" :disabled="asking" @click="openChatSession(session)"><strong>{{ session.title }}</strong><small>{{ new Date(session.updated_at).toLocaleString() }}</small></button><el-empty v-if="!chatSessions.length" description="暂无对话" /></div></aside>
            <section class="surface ask-surface"><div class="section-heading"><div><span class="overline">ASK {{ selected.name }}</span><h3>{{ activeChatSession?.title || '新对话' }}</h3></div><span class="status-pill">连续对话 · 引用可追溯</span></div><div ref="chatHistoryRef" class="chat-history"><article v-for="message in chatMessages" :key="message.id" class="chat-message" :class="message.role"><strong>{{ message.role === 'user' ? '你' : '知识助手' }}</strong><MarkdownContent v-if="message.role === 'assistant' && message.content" :content="message.content" :class="{ 'is-streaming': asking && message === chatMessages[chatMessages.length - 1] }" /><p v-else>{{ message.content || (asking ? '正在检索资料并连接模型…' : '') }}</p><span v-if="asking && !message.content && message === chatMessages[chatMessages.length - 1]" class="stream-cursor"></span><div v-if="message.citations.length" class="message-citations"><button v-for="citation in message.citations" :key="citation.chunk_id" type="button" @click="showCitation(citation)"><b>[{{ citation.number }}]</b> {{ citation.document_title }}<template v-if="citation.page_no"> · 第 {{ citation.page_no }} 页</template><span class="citation-open-icon" aria-hidden="true">↗</span></button></div></article><el-empty v-if="!chatMessages.length" description="开始一段有引用的资料对话" /></div><el-input v-model="question" type="textarea" :rows="3" maxlength="1000" show-word-limit :disabled="!selected.cloud_enabled || asking" placeholder="例如：这份资料中对收益目标有什么说明？" @keyup.ctrl.enter="askKnowledgeBase" /><div class="ask-actions"><small>{{ selected.cloud_enabled ? 'Ctrl + Enter 提交；可在当前对话中继续追问。' : '云端问答未启用，请管理员确认该知识库允许外发资料后开启。' }}</small><div><el-button v-if="asking" @click="stopAnswer">停止生成</el-button><el-button type="primary" :disabled="!selected.cloud_enabled || !question.trim() || asking" @click="askKnowledgeBase">发送问题 <span class="button-arrow">→</span></el-button></div></div></section>
          </div>
          <section v-else-if="!selected" class="surface empty-surface"><el-empty description="请先创建或选择知识库" /><el-button type="primary" @click="openCreateKbDialog">新建知识库</el-button></section>
        </template>

        <template v-else-if="activeSection === 'graph'">
          <div class="page-heading">
            <div><span class="overline">KNOWLEDGE GRAPH</span><h1>知识图谱</h1><p>跨文档梳理实体与关系，点击连线核对原文证据。</p></div>
            <div class="heading-select"><span>知识库</span><el-select v-model="selectedId" placeholder="选择知识库"><el-option v-for="kb in knowledgeBases" :key="kb.id" :label="kb.name" :value="kb.id" /></el-select><el-button :loading="graphLoading" @click="refreshGraphPage">刷新图谱</el-button></div>
          </div>
          <section v-if="selected" class="surface provenance-surface">
            <div v-if="graphProgress" class="graph-progress" aria-label="知识图谱抽取进度">
              <div class="graph-progress-summary"><strong>图谱抽取进度</strong><span>{{ graphProgress.processed_chunks.toLocaleString() }} / {{ graphProgress.total_chunks.toLocaleString() }} 个片段</span><b>{{ graphProgressPercent }}%</b></div>
              <div class="graph-progress-track" role="progressbar" :aria-valuenow="graphProgress.processed_chunks" :aria-valuemin="0" :aria-valuemax="graphProgress.total_chunks || 1" aria-label="已完成图谱抽取的片段"><span :style="{ width: `${graphProgressPercent}%` }"></span></div>
              <div class="graph-progress-foot"><span>文档 {{ graphProgress.parsed_documents }}/{{ graphProgress.total_documents }} 已解析</span><span v-if="graphProgress.running_chunks">正在抽取 {{ graphProgress.running_chunks }}</span><span v-if="graphProgress.waiting_budget_chunks">预算等待 {{ graphProgress.waiting_budget_chunks }}</span><span v-if="graphProgress.failed_chunks">失败 {{ graphProgress.failed_chunks }}</span></div>
            </div>
            <KnowledgeGraph v-if="knowledgeGraph" :graph="knowledgeGraph" :processed-chunks="graphProgress?.processed_chunks" />
            <div v-else-if="graphLoading" class="graph-loading">正在读取知识图谱…</div>
          </section>
          <section v-else class="surface empty-surface"><el-empty description="请先选择知识库" /></section>
        </template>

        <template v-else-if="activeSection === 'documents'">
          <div class="page-heading">
            <div><span class="overline">DOCUMENT MANAGEMENT</span><h1>文档中心</h1><p>统一管理资料、版本与文档信息。</p></div>
            <div class="heading-select"><span>知识库</span><el-select v-model="selectedId" placeholder="选择知识库"><el-option v-for="kb in knowledgeBases" :key="kb.id" :label="kb.name" :value="kb.id" /></el-select><el-button @click="openCreateKbDialog">＋ 新建</el-button></div>
          </div>
          <template v-if="selected">
            <section v-if="!showDeleted" class="surface parse-overview-surface">
              <div class="section-heading"><div><span class="overline">PARSING STATUS</span><h3>解析待处理</h3></div><div class="document-section-actions"><span class="section-count">{{ parseOverview?.parsed_documents ?? '—' }}/{{ parseOverview?.total_documents ?? '—' }} 已解析</span><el-button link type="primary" @click="loadParseOverview()">刷新状态</el-button></div></div>
              <p class="parse-overview-note">未解析和失败的当前版本集中显示在这里。点击重新解析后，任务由后台处理，状态会自动更新。</p>
              <div v-if="parseOverview?.issues.length" class="parse-issue-list">
                <div v-for="issue in parseOverview.issues" :key="issue.document_id" class="parse-issue-row">
                  <div class="parse-issue-main"><strong>{{ issue.title }}</strong><small>{{ issue.original_filename }} · v{{ issue.version_no }} · {{ issue.job_status === 'QUEUED' ? '排队中' : issue.job_status === 'RUNNING' ? '解析中' : statusLabel(issue.status) }}</small><span v-if="issue.parse_error" class="parse-issue-error">{{ issue.parse_error }}</span><span v-else-if="!issue.supported" class="parse-issue-error">该文件格式暂不支持解析，请转换后上传新版本</span></div>
                  <el-button v-if="issue.supported" type="primary" plain :loading="parseSubmitting === issue.document_id" :disabled="!!parseSubmitting || issue.job_status === 'QUEUED' || issue.job_status === 'RUNNING'" @click="reparseIssue(issue)">{{ issue.status === 'FAILED' ? '重新解析' : '开始解析' }}</el-button>
                  <span v-else class="parse-issue-unsupported">暂不支持</span>
                </div>
              </div>
              <div v-else class="parse-issue-empty">当前知识库没有待处理文档</div>
            </section>
            <section v-if="!showDeleted" class="surface upload-surface">
              <div class="section-heading"><div><span class="overline">UPLOAD FILES</span><h3>{{ selectedDocument ? '上传新版本' : '上传文档' }}</h3></div><span class="section-count">单批最多 10 份</span></div>
              <div class="upload-controls">
                <label class="file-picker" for="document-file"><span class="upload-glyph">↑</span><span><strong>{{ uploadFiles.length ? uploadFiles.map(file => file.name).join('、') : '点击选择文件' }}</strong><small>PDF、DOC、DOCX、PPTX、XLSX、Markdown、HTML 和图片可解析；PPT、XLS 仅保存原件</small></span></label>
                <input id="document-file" type="file" :multiple="!selectedDocument" accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.md,.html,.htm,.png,.jpg,.jpeg,.tif,.tiff,.webp" @change="chooseFile" />
                <el-button type="primary" :disabled="!uploadFiles.length" :loading="uploading" @click="upload">{{ selectedDocument ? '上传新版本' : '开始上传' }}</el-button>
                <el-button v-if="selectedDocument" @click="selectedDocument = null; versions = []">改为新建文档</el-button>
              </div>
            </section>

            <section class="surface document-surface">
              <div class="section-heading"><div><span class="overline">DOCUMENT LIBRARY</span><h3>{{ showDeleted ? '回收站' : '文档列表' }}</h3></div><div class="document-section-actions"><span class="section-count">共 {{ documentTotal }} 份</span><el-button link type="primary" @click="switchDocumentBin(!showDeleted)">{{ showDeleted ? '返回文档列表' : '查看回收站' }}</el-button></div></div>
              <div class="filter-bar">
                <el-input v-model="searchText" placeholder="搜索标题、文件名或已解析正文" clearable @keyup.enter="refreshDocuments()" />
                <el-input v-if="!showDeleted" v-model="filterTag" placeholder="标签" clearable @keyup.enter="refreshDocuments()" />
                <el-input v-if="!showDeleted" v-model="filterSource" placeholder="来源" clearable @keyup.enter="refreshDocuments()" />
                <el-select v-if="!showDeleted" v-model="filterType" placeholder="文件类型" clearable>
                  <el-option v-for="type in ['pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'md', 'html', 'png', 'jpg', 'jpeg', 'tif', 'tiff', 'webp']" :key="type" :label="type.toUpperCase()" :value="type" />
                </el-select>
                <el-button @click="refreshDocuments()">筛选</el-button>
              </div>
              <div class="document-layout" :class="{ 'has-selection': selectedDocument }">
                <div class="document-list">
                  <div class="list-labels"><span>文档名称</span><span>类型 / 版本</span><span>状态</span></div>
                  <button v-for="doc in documents" :key="doc.id" class="document-row" :class="{ selected: selectedDocument?.id === doc.id }" @click="viewDocument(doc)">
                    <span class="document-name"><span class="file-type-icon">{{ doc.file_type.toUpperCase().slice(0, 3) }}</span><span><strong>{{ doc.title }}</strong><small>{{ searchMode && resultFor(doc.id)?.snippet ? resultFor(doc.id)?.snippet : doc.source || doc.description || '暂无来源与描述' }}</small><small v-if="searchMode && resultFor(doc.id)?.locations.length" class="search-location">命中：{{ resultFor(doc.id)?.locations.map(item => item.page ? `第 ${item.page} 页` : '正文').join('、') }}</small></span></span>
                    <span class="document-version">{{ doc.file_type.toUpperCase() }} · v{{ doc.current_version }}</span>
                    <span class="document-status"><span class="status-dot"></span>{{ searchMode ? (resultFor(doc.id)?.matched_in.join(' / ') || '元数据') : statusLabel(doc.status) }}</span>
                  </button>
                  <el-empty v-if="!documents.length" :description="showDeleted ? '回收站为空' : '暂无符合条件的文档'" />
                  <div v-if="documents.length < documentTotal" class="load-more"><el-button @click="refreshDocuments(true)">加载更多</el-button></div>
                </div>
                <aside v-if="selectedDocument" class="document-detail">
                   <div class="detail-top"><span class="overline">DOCUMENT DETAILS</span><button class="close-detail" aria-label="关闭详情" @click="selectedDocument = null">×</button></div>
                   <h4>{{ selectedDocument.title }}</h4>
                   <p class="detail-subtitle">{{ selectedDocument.file_type.toUpperCase() }} 文件 · 创建于 {{ new Date(selectedDocument.created_at).toLocaleDateString() }}</p>
                   <el-button v-if="!showDeleted" link type="primary" @click="openGraph()">查看知识图谱 →</el-button>
                   <div class="document-lifecycle-actions"><el-button v-if="showDeleted" type="primary" @click="restoreSelectedDocument">恢复文档</el-button><el-button v-else type="danger" plain @click="deleteSelectedDocument">移入回收站</el-button></div>
                  <div class="detail-divider"></div>
                  <h5>历史版本</h5>
                  <div v-for="version in versions" :key="version.id" class="version-row">
                    <span class="version-badge">v{{ version.version_no }}</span><span class="version-info"><strong>{{ version.original_filename }}</strong><small>{{ (version.size_bytes / 1024).toFixed(1) }} KB · {{ uploadOnly(version.original_filename) ? '仅存原件' : version.job_status === 'QUEUED' ? '等待解析' : statusLabel(version.status) }}</small><small v-if="uploadOnly(version.original_filename)" class="parse-note">转换为新版 Office 格式后可解析</small><small v-if="version.parse_error" class="parse-error">{{ version.parse_error }}</small></span>
                    <el-button v-if="!showDeleted && version.status === 'PARSED'" link @click="openParsed(version)">解析内容</el-button>
                    <el-button v-if="!showDeleted && version.status === 'PARSED'" link type="warning" @click="requestParse(version)">重新解析</el-button>
                    <el-button v-else-if="!showDeleted && parseable(version.original_filename) && version.job_status !== 'QUEUED' && version.job_status !== 'RUNNING'" link @click="requestParse(version)">{{ version.status === 'FAILED' ? '重试' : '解析' }}</el-button>
                    <el-button v-if="!showDeleted && /\.(md|html|htm)$/i.test(version.original_filename)" link @click="previewVersion(version)">原文</el-button>
                    <el-button v-if="!showDeleted" link type="primary" @click="downloadVersion(version)">下载</el-button>
                    <el-button v-if="!showDeleted && version.version_no !== selectedDocument.current_version" link type="warning" @click="rollbackVersion(version)">设为当前版本</el-button>
                  </div>
                   <div v-if="parsedResult" class="parse-inspector"><div class="parse-inspector-head"><strong>解析概览</strong><span>{{ parsedResult.parser_name }} · {{ parsedPages.length }} 页 · {{ parsedPages.reduce((sum, page) => sum + page.blocks.length, 0) }} 内容块<span v-if="parsedResult.structure.parser_config?.ocr"> · OCR</span></span></div><div class="parse-pages"><button v-for="page in parsedPages" :key="page.number" :class="{ active: parsedPage === page.number }" @click="parsedPage = page.number">第 {{ page.number }} 页</button></div><div v-if="visibleParsedPage" class="parse-blocks"><div v-for="(block, index) in visibleParsedPage.blocks" :key="index"><small>{{ block.type === 'heading' ? '标题' : block.type === 'paragraph' ? '正文' : block.type }}</small><p>{{ block.text }}</p></div><el-empty v-if="!visibleParsedPage.blocks.length" description="此页没有提取到文字" /></div></div>
                   <div v-if="previewLabel" class="preview-block"><strong>{{ previewLabel }}</strong><pre>{{ previewContent }}</pre></div>
                  <div class="detail-divider"></div>
                  <h5 v-if="!showDeleted">文档信息</h5>
                  <div v-if="!showDeleted" class="detail-fields">
                    <label>标题<el-input v-model="editTitle" maxlength="255" placeholder="文档标题" /></label>
                    <label>描述<el-input v-model="editDescription" type="textarea" :rows="2" maxlength="2000" placeholder="简要描述" /></label>
                    <label>来源<el-input v-model="editSource" maxlength="200" placeholder="资料来源" /></label>
                    <label>标签<el-input v-model="editTags" placeholder="多个标签用逗号分隔" /></label>
                  </div>
                  <el-button v-if="!showDeleted" type="primary" :disabled="!editTitle.trim()" @click="saveMetadata">保存信息</el-button>
                </aside>
              </div>
            </section>
          </template>
          <section v-else class="surface empty-surface"><el-empty description="请先创建知识库，再上传文档" /><el-button type="primary" @click="openCreateKbDialog">新建知识库</el-button></section>
        </template>

        <template v-else>
          <div class="page-heading"><div><span class="overline">ACCESS MANAGEMENT</span><h1>用户管理</h1><p>管理个人账号与知识库访问权限。</p></div></div>
          <div class="team-grid">
            <section class="surface account-panel">
              <div class="section-heading"><div><span class="overline">MY ACCOUNT</span><h3>我的账号</h3></div></div>
              <div class="account-summary"><span class="member-avatar">{{ currentUser.username.slice(0, 1).toUpperCase() }}</span><div><strong>{{ currentUser.username }}</strong><small>{{ currentUser.role === 'ADMIN' ? '管理员' : '普通成员' }}</small></div></div>
              <div class="account-actions"><el-button type="primary" plain @click="passwordDialogVisible = true">修改密码</el-button><el-button @click="logout">退出登录</el-button></div>
            </section>
            <section v-if="currentUser.role === 'ADMIN'" class="surface">
              <div class="section-heading"><div><span class="overline">TEAM MEMBERS</span><h3>成员账号</h3></div><span class="section-count">{{ users.length }} 位</span></div>
              <div class="member-list"><div v-for="user in users" :key="user.id" class="member-row"><span class="member-avatar">{{ user.username.slice(0, 1).toUpperCase() }}</span><span><strong>{{ user.username }}</strong><small>{{ user.role === 'ADMIN' ? '管理员' : '普通成员' }}</small></span><span class="member-status">正常</span></div></div>
              <div class="detail-divider"></div><h4>创建成员账号</h4>
              <div class="team-form"><el-input v-model="newUsername" placeholder="用户名" /><el-input v-model="newPassword" type="password" placeholder="初始密码，至少 12 位" show-password /><el-button type="primary" :disabled="newUsername.length < 3 || newPassword.length < 12" @click="createUser">创建成员</el-button></div>
            </section>
            <section class="surface">
              <div class="section-heading"><div><span class="overline">LIBRARY ACCESS</span><h3>知识库授权</h3></div></div>
              <p class="panel-intro">为成员分配知识库的查看或编辑权限。</p>
              <label class="field-label">选择知识库</label>
              <el-select v-model="selectedId" placeholder="选择知识库" class="full-button"><el-option v-for="kb in knowledgeBases" :key="kb.id" :label="kb.name" :value="kb.id" /></el-select>
              <template v-if="canGrant">
                <label class="field-label">选择成员</label>
                <el-select v-if="users.length" v-model="memberId" placeholder="选择成员" class="full-button"><el-option v-for="user in users" :key="user.id" :label="user.username" :value="user.id" /></el-select>
                <el-input v-else v-model="memberId" placeholder="成员用户 ID" />
                <label class="field-label">访问角色</label>
                <el-select v-model="memberRole" class="full-button"><el-option label="可查看" value="VIEWER" /><el-option label="可编辑" value="EDITOR" /></el-select>
                <el-button type="primary" :disabled="!memberId" @click="grantMember">授权成员</el-button>
              </template>
              <p v-else class="permission-note">仅管理员或知识库创建人可以授权成员。</p>
            </section>
          </div>
        </template>
      </main>
    </div>
  </div>
  <el-drawer v-model="citationOpen" title="引用来源" size="min(520px, 96vw)" append-to-body>
    <div v-if="selectedCitation" class="citation-detail">
      <span class="citation-detail-index">引用 [{{ selectedCitation.number }}]</span>
      <h3>{{ selectedCitation.document_title }}</h3>
      <div class="citation-detail-meta"><span>版本 v{{ selectedCitation.version_no }}</span><span>{{ citationPage ? `第 ${citationPage} 页` : '无可核实页码' }}</span><span v-if="citationSource">{{ citationSource.file_type.toUpperCase() }}</span></div>
      <div class="citation-detail-section"><strong>对应原文</strong><p>{{ citationSource?.content || selectedCitation.quote }}</p></div>
      <div v-if="selectedCitation.graph_evidence?.length" class="citation-detail-section graph-citation-evidence"><strong>图谱关系线索</strong><div v-for="item in selectedCitation.graph_evidence" :key="item.id" class="graph-citation-item"><b>{{ item.source }} → {{ item.relation }} → {{ item.target }}</b><p>{{ item.quote }}</p></div><small>关系由上述原文片段抽取，请以原文为准。</small></div>
      <p v-if="!citationSource && !citationLoading" class="citation-detail-note">这条历史引用的索引片段已更新，仍可根据保存的摘录查看来源文档。</p>
      <div class="citation-detail-actions"><el-button type="primary" @click="showCitationDocument">查看文档</el-button><el-button v-if="citationSource" :loading="citationLoading" @click="openCitationOriginal">{{ citationSource.file_type === 'pdf' ? '打开原文件并定位页码' : '下载原文件' }}</el-button></div>
    </div>
  </el-drawer>
  <el-dialog v-model="createKbDialogVisible" title="新建知识库" width="min(480px, 92vw)" destroy-on-close>
    <p class="dialog-intro">创建后即可上传文档，并在工作空间中统一管理。</p>
    <el-form label-position="top" @submit.prevent="createKb">
      <el-form-item label="知识库名称" required><el-input v-model="newKbName" maxlength="200" show-word-limit placeholder="例如：产品资料库" autofocus /></el-form-item>
      <el-form-item label="描述"><el-input v-model="newKbDescription" type="textarea" :rows="3" maxlength="2000" placeholder="简要说明这个知识库的用途（可选）" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="createKbDialogVisible = false">取消</el-button><el-button type="primary" :loading="creatingKb" :disabled="!newKbName.trim()" @click="createKb">创建并进入文档中心</el-button></template>
  </el-dialog>
  <el-dialog v-model="passwordDialogVisible" title="修改密码" width="min(440px, 92vw)" destroy-on-close @closed="currentPassword = ''; changedPassword = ''; confirmPassword = ''">
    <p class="dialog-intro">修改后所有已登录设备需要使用新密码重新登录。</p>
    <el-form label-position="top" @submit.prevent="changePassword">
      <el-form-item label="当前密码" required><el-input v-model="currentPassword" type="password" show-password autocomplete="current-password" /></el-form-item>
      <el-form-item label="新密码" required><el-input v-model="changedPassword" type="password" show-password autocomplete="new-password" maxlength="128" placeholder="至少 12 位" /></el-form-item>
      <el-form-item label="确认新密码" required><el-input v-model="confirmPassword" type="password" show-password autocomplete="new-password" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="passwordDialogVisible = false">取消</el-button><el-button type="primary" :loading="changingPassword" :disabled="!currentPassword || changedPassword.length < 12 || !confirmPassword" @click="changePassword">确认修改</el-button></template>
  </el-dialog>
</template>
