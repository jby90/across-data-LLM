<script setup lang="ts">
// 合规文件第 5 类《海外属地市场准入材料清单》旁的去办理：进入海外属地准入，按这个项目的国家和业务列出要办的事项
import { useRouter } from 'vue-router'

const props = defineProps<{ taskId: string; disabled?: boolean; label?: string }>()
const router = useRouter()
</script>

<template>
  <el-button size="small" :disabled="disabled" data-testid="btn-admission" @click="router.push({ path: '/admission', query: { task: props.taskId } })">{{ label || '去办理' }}</el-button>
</template>
