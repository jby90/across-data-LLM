<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  closeTermPopover,
  sanitizeTermView,
  termPopoverState,
  updateTermPopoverPosition,
} from '../termPopover'

const route = useRoute()
const popEl = ref<HTMLElement | null>(null)

const view = computed(() => (termPopoverState.term ? sanitizeTermView(termPopoverState.term) : null))

const popStyle = computed(() => {
  const base: Record<string, string> = {
    left: `${termPopoverState.left}px`,
    top: `${termPopoverState.top}px`,
  }
  if (termPopoverState.placement === 'top') {
    base.transform = 'translateY(-100%)'
  }
  return base
})

function onDocPointerDown(ev: MouseEvent) {
  if (!termPopoverState.visible) return
  const t = ev.target as HTMLElement | null
  if (!t) return
  if (popEl.value?.contains(t)) return
  if (t.closest?.('.term-mark')) return
  closeTermPopover()
}

function onKeydown(ev: KeyboardEvent) {
  if (ev.key === 'Escape' && termPopoverState.visible) {
    closeTermPopover()
  }
}

function onScrollOrResize() {
  if (termPopoverState.visible) {
    updateTermPopoverPosition()
  }
}

watch(
  () => route.fullPath,
  () => closeTermPopover(),
)

onMounted(() => {
  document.addEventListener('mousedown', onDocPointerDown)
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('scroll', onScrollOrResize, true)
  window.addEventListener('resize', onScrollOrResize)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', onDocPointerDown)
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('scroll', onScrollOrResize, true)
  window.removeEventListener('resize', onScrollOrResize)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="termPopoverState.visible && view"
      ref="popEl"
      class="cy-term-popover"
      role="dialog"
      aria-label="法律术语释义"
      data-testid="term-popover"
      :style="popStyle"
    >
      <div class="tp-head">
        <div class="tp-title">
          <strong>{{ view.headingMain }}</strong>
          <span v-if="view.headingSub" class="tp-sub" dir="auto">{{ view.headingSub }}</span>
        </div>
        <button
          type="button"
          class="tp-close"
          aria-label="关闭术语释义"
          @click="closeTermPopover"
        >
          ×
        </button>
      </div>

      <div class="tp-body">
        <template v-if="view.isMultiple">
          <p class="tp-multi-note">该术语在不同条款中有以下说明：</p>
          <section v-for="entry in view.entries" :key="entry.id" class="tp-entry">
            <p v-if="entry.zh" class="tp-zh">{{ entry.zh }}</p>
            <p v-if="entry.orig" class="tp-orig" dir="auto">{{ entry.orig }}</p>
            <small v-if="entry.ref" class="tp-ref">{{ entry.ref }}</small>
          </section>
        </template>
        <template v-else-if="view.entries[0]">
          <p v-if="view.entries[0].zh" class="tp-zh">{{ view.entries[0].zh }}</p>
          <p v-if="view.entries[0].orig" class="tp-orig" dir="auto">{{ view.entries[0].orig }}</p>
          <p v-if="view.genericFallback" class="tp-zh">{{ view.genericFallback }}</p>
          <div class="tp-foot">
            <small v-if="view.entries[0].ref" class="tp-ref">{{ view.entries[0].ref }}</small>
            <a
              v-if="view.sourceUrl"
              :href="view.sourceUrl"
              target="_blank"
              rel="noopener noreferrer"
              class="tp-link"
            >查看来源 ↗</a>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.cy-term-popover {
  position: fixed;
  z-index: 2600;
  width: min(396px, calc(100vw - 24px));
  background: #ffffff;
  border: 1px solid #dbe4ee;
  border-radius: 12px;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.14);
  padding: 14px 16px;
  font-size: 14px;
  line-height: 1.7;
  color: #1e293b;
}
.tp-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid #eef2f6;
}
.tp-title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.tp-title strong {
  font-size: 15px;
  color: #0f172a;
}
.tp-sub {
  font-size: 13px;
  color: #64748b;
}
.tp-close {
  border: 0;
  background: transparent;
  color: #64748b;
  font-size: 18px;
  line-height: 1;
  padding: 2px 6px;
  border-radius: 6px;
  cursor: pointer;
  flex-shrink: 0;
}
.tp-close:hover {
  background: #f1f5f9;
  color: #0f172a;
}
.tp-body {
  max-height: 340px;
  overflow-y: auto;
  padding-top: 8px;
}
.tp-multi-note {
  margin: 0 0 6px;
  font-size: 13px;
  color: #64748b;
}
.tp-entry + .tp-entry {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e2e8f0;
}
.tp-zh {
  margin: 0 0 6px;
  color: #1e293b;
}
.tp-orig {
  margin: 0 0 6px;
  font-size: 13px;
  color: #475569;
  background: #f8fafc;
  padding: 6px 10px;
  border-radius: 6px;
}
.tp-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 6px;
}
.tp-ref {
  font-size: 13px;
  color: #64748b;
}
.tp-link {
  font-size: 13px;
  color: #215c9b;
  white-space: nowrap;
}
.tp-link:hover {
  text-decoration: underline;
}
</style>
