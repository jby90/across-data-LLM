<script setup lang="ts">
import { ElNotification } from 'element-plus'
import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../auth'

// 论述类文件（DPIA/LIA/TIA）在后台生成，用户可能已经离开文件页：仅对当前打开会话期间新完成的后台任务弹出简洁提示
interface Done { project_id: string; title: string; code: string; name: string; state: 'done' | 'failed'; error?: string; at: string }
const auth = useAuth()
const router = useRouter()
let timer: number | undefined
let cursor = ''
let seeded = false

async function check(silent = false) {
  if (!api.token() || !auth.user) return
  if (!cursor) cursor = new Date().toISOString()
  let items: Done[] = []
  try {
    const query = seeded ? `?since=${encodeURIComponent(cursor)}` : ''
    items = await api.get<Done[]>(`/api/analysis/doc-projects/papers/recent${query}`)
  } catch {
    return
  }
  let latest = cursor
  for (const it of items) {
    if (it.at > latest) latest = it.at
  }
  if (silent || !seeded) {
    cursor = latest
    seeded = true
    return
  }
  const fresh = items.filter((it) => it.at > cursor)
  cursor = latest
  if (!fresh.length) return

  const grouped = new Map<string, Done[]>()
  for (const it of fresh) {
    const k = `${it.project_id}:${it.state}`
    const arr = grouped.get(k)
    if (arr) arr.push(it)
    else grouped.set(k, [it])
  }

  for (const list of grouped.values()) {
    const first = list[0]
    const names = Array.from(new Set(list.map((x) => x.name)))
    const summaryName = names.length === 1 ? names[0] : `${names.slice(0, 2).join('、')}等 ${names.length} 份合规文件`
    let handle: { close: () => void } | undefined
    handle = ElNotification({
      title: first.state === 'done' ? '合规文件已生成' : '合规文件未生成成功',
      message:
        first.state === 'done'
          ? `${first.title}：${summaryName}已生成，点击查看项目`
          : `${first.title}：${summaryName}生成未完成（${first.error || '请稍后重试'}），点击查看项目`,
      type: first.state === 'done' ? 'success' : 'warning',
      duration: 5000,
      offset: 64,
      onClick: () => {
        handle?.close()
        void router.push({ path: '/documents', query: { project: first.project_id } })
      },
    })
  }
}

onMounted(() => {
  cursor = new Date().toISOString()
  seeded = false
  void check(true)
  timer = window.setInterval(() => void check(false), 20000)
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<template><span hidden /></template>
