<script setup lang="ts">
import { computed } from 'vue'
import { splitLegalText, type LegalTerm } from '@cyshuhe/shared/src/legalText'
import { reflow } from '@cyshuhe/shared/src/reflow'
import { handleTermDelegation, registerTerm } from '../termPopover'

const props = defineProps<{
  text: string
  terms?: LegalTerm[]
  clauseId?: string
  sectionPath?: string | null
}>()

const parts = computed(() => {
  const cleaned = reflow(props.text)
  const scoped = (props.terms || []).filter(
    (t) =>
      !t.source_scope ||
      t.source_scope === 'document' ||
      (t.source_scope === 'clause' && t.clause_id === props.clauseId) ||
      (t.source_scope === 'chapter' &&
        !!t.scope_path &&
        props.sectionPath?.includes(t.scope_path)),
  )
  return splitLegalText(cleaned, scoped).map((p) =>
    p.term ? { text: p.text, termKey: registerTerm(p.term) } : { text: p.text, termKey: '' },
  )
})
</script>

<template>
  <span class="legal-text" @click="handleTermDelegation" @keydown="handleTermDelegation"
    ><template v-for="(part, index) in parts" :key="index"
      ><span
        v-if="part.termKey"
        class="term term-mark"
        role="button"
        tabindex="0"
        :data-term-key="part.termKey"
      >{{ part.text }}</span
      ><template v-else>{{ part.text }}</template></template
    ></span
  >
</template>

<style scoped>
.legal-text {
  white-space: pre-wrap;
  line-height: 1.9;
  overflow-wrap: anywhere;
}
.term-mark {
  font: inherit;
  color: #215c9b;
  background: transparent;
  border: 0;
  border-bottom: 1px dotted #88a9c8;
  padding: 0;
  cursor: pointer;
  text-align: inherit;
}
.term-mark:hover {
  color: #1d4ed8;
  border-bottom-color: #2563eb;
  background: #eff6ff;
}
.term-mark:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
}
</style>
