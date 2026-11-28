<script setup lang="ts">
import { computed } from 'vue'
import { useAuth } from '../auth'
import Cockpit from './Cockpit.vue'

const auth = useAuth()
const isSuper = computed(() => auth.user?.role === 'platform_admin')
</script>

<template>
  <Cockpit :ops-mode="!isSuper" />
</template>

