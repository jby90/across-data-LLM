import assert from 'node:assert/strict'
import test from 'node:test'
import { alignBilingualParagraphs, reflow } from '../packages/shared/src/reflow.ts'

test('roman sub-items (i)/(ii) never collide with numeric subsections (1)/(2) on lettered sections like 48J', () => {
  const orig = [
    '48J.—(1) Subject to subsection (2), the Commission may, if it is satisfied that —',
    '(a) an organisation has contravened Part 3; or',
    '(b) a person has contravened —',
    '(i) any provision of Part 9; or',
    '(ii) section 48B(1),',
    'require the organisation to pay a financial penalty.',
    '(2) Subsection (1) does not apply to an offence.',
    '(4A) A financial penalty must not exceed $200,000.',
    '(5) Different maximum amounts may be prescribed.',
    '(5A) Annual turnover is ascertained from audited accounts.',
  ].join('\n')
  const zh = [
    '第48J条 （1）在符合第(2)款的前提下，委员会如确信以下事项，可要求支付财务处罚——',
    '（a）某组织违反第3部分；或',
    '（b）某人违反——',
    '（i）第9部分的任何规定；或',
    '（ii）第48B条第(1)款，',
    '（2）第(1)款不适用于犯罪。',
    '（4A）财务处罚不得超过$200,000。',
    '第5款 可规定不同的最高数额。',
    '第5A款 年营业额按经审计账目查明。',
  ].join('\n')
  const pairs = alignBilingualParagraphs(orig, zh, 'Financial penalties')
  const sub2 = pairs.find(([l]) => l.startsWith('(2)'))
  const sub4A = pairs.find(([l]) => l.startsWith('(4A)'))
  const sub5 = pairs.find(([l]) => l.startsWith('(5)'))
  const sub5A = pairs.find(([l]) => l.startsWith('(5A)'))
  assert.ok(sub2 && sub2[1].startsWith('（2）'))
  assert.ok(sub4A && sub4A[1].startsWith('（4A）'))
  assert.ok(sub5 && sub5[1].startsWith('第5款'))
  assert.ok(sub5A && sub5A[1].startsWith('第5A款'))
})

test('subsection-1 barrier prevents multi-section amendment clauses from cross-jumping across inserted sections', () => {
  const orig = [
    '20. In the principal Act, after section 37, insert —',
    '“Civil penalty',
    '37A.—(1) Whenever it appears to the Commissioner that any person contravened a provision.',
    '(2) The court may make an order:',
    '(a) first tier;',
    '(b) second tier.',
    '(3) Despite subsection (2), the court may make an agreed order in',
    'Part 3, 3A or 3B.',
    '25 (4) Nothing prevents an agreement.',
    '“Action not to commence',
    '37B.—(1) An action under section 37A must not be commenced late.',
    '(2) An action must not be commenced after conviction.',
  ].join('\n')
  const zh = [
    '20. 在主体法令中，在第37条之后插入——“民事罚款第37A条——（1）凡专员认为任何人违反规定。',
    '（2）法院可作出命令：',
    '（a）第一档；及（b）第二档。',
    '（3）尽管有第(2)款的规定，法院可就第3编、3A或3B作出约定命令。',
    '(4) 本条不阻止订立协议。',
    '“不得启动诉讼',
    '第37B条——第(1)款 第37A条项下诉讼不得逾期启动。',
    '(2) 定罪后不得启动诉讼。',
  ].join('\n')
  const pairs = alignBilingualParagraphs(orig, zh)
  const row37A4 = pairs.find(([l]) => l.startsWith('(4)'))
  const row37B2 = pairs.find(([l]) => l.startsWith('(2) An action'))
  assert.ok(row37A4 && row37A4[1].startsWith('(4)'))
  assert.ok(row37B2 && row37B2[1].startsWith('(2) 定罪'))
})

test('French lowercase numbered items stay separate while inline French number-unit wraps reflow cleanly', () => {
  const frList = 'Les services doivent :\n(1) être fournis par un prestataire ;\n(2) garantir l’identification.'
  const zhList = '服务必须：\n（1）由服务商提供；\n（2）保证身份识别。'
  const pairs = alignBilingualParagraphs(frList, zhList)
  assert.equal(pairs.length, 3)
  assert.ok(pairs[2][0].startsWith('(2)') && pairs[2][1].startsWith('（2）'))

  const frUnit = 'Véhicule articulé à trois\n(3) essieux : 11 tonnes.'
  assert.equal(reflow(frUnit), 'Véhicule articulé à trois (3) essieux : 11 tonnes.')
})
