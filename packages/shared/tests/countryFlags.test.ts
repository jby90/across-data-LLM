import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import { countryFlagShapes } from '../src/countryFlags.ts'

test('countryFlagShapes covers all 46 platform jurisdictions with distinct SVG shapes', () => {
  const codes = [
    'CN', 'SG', 'EG', 'TN', 'MA', 'LB', 'EU', 'DE', 'FR', 'IT', 'ES', 'NL', 'BE', 'SE', 'PL',
    'GB', 'RU', 'TR', 'JP', 'KR', 'TH', 'MY', 'ID', 'VN', 'PH', 'IN', 'PK', 'BD', 'KZ', 'KG',
    'AE', 'SA', 'QA', 'KW', 'OM', 'IL', 'DZ', 'ZA', 'NG', 'US', 'CA', 'MX', 'BR', 'AR', 'CL',
    'CO', 'PE', 'AU',
  ]
  for (const cc of codes) {
    const shapes = countryFlagShapes(cc)
    assert.ok(shapes.length >= 2, `Expected >=2 SVG shapes for ${cc}`)
  }
  assert.deepEqual(countryFlagShapes(''), [])
  assert.ok(countryFlagShapes('ZZ').length >= 2)
})

test('user and admin CountryFlag components stay identical', () => {
  const root = resolve(import.meta.dirname, '../../..')
  const u = readFileSync(resolve(root, 'apps/web-user/src/components/CountryFlag.vue'), 'utf-8')
  const a = readFileSync(resolve(root, 'apps/web-admin/src/components/CountryFlag.vue'), 'utf-8')
  assert.equal(u, a)
})
