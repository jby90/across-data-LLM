<script setup lang="ts">
import { computed } from 'vue'
import { parseBold } from '../richText'

// 文字里的 **重点** 渲染成粗体。不使用 v-html：每个片段都作为文本节点输出，
// 模型文字里即使混入 HTML 也只会原样显示，不会被执行。
const props = defineProps<{ text?: string | null }>()
const segments = computed(() => parseBold(props.text ?? ''))
</script>

<template>
  <span class="rich"
    ><template v-for="(s, i) in segments" :key="i"
      ><strong v-if="s.bold">{{ s.text }}</strong
      ><template v-else>{{ s.text }}</template></template
    ></span
  >
</template>

<style scoped>
.rich strong { font-weight: 600; color: #111827 }
</style>
