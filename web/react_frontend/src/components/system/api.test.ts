import { describe, expect, it } from 'vitest'
import { accessSources, type AccessUser } from './api'

const user: AccessUser = { id: 10, username: 'engineer', is_active: true, role: 'operator', group_ids: [2, 3] }
const groups = [{ id: 2, name: '核心网', protected: false }, { id: 3, name: '值班组', protected: false }]
describe('effective application access preview', () => {
  it('explains retained group access after direct access is removed', () => {
    expect(accessSources(user, [10], [2, 3], groups)).toEqual(['直接授权', '用户组：核心网', '用户组：值班组'])
    expect(accessSources(user, [], [2], groups)).toEqual(['用户组：核心网'])
    expect(accessSources(user, [], [], groups)).toEqual([])
  })
  it('does not imply group access for a nonmember', () => {
    expect(accessSources({ ...user, group_ids: [] }, [], [2], groups)).toEqual([])
  })
  it('keeps admin default access distinct from ordinary grants', () => {
    expect(accessSources({ ...user, role: 'admin' }, [], [], groups)).toEqual(['平台管理员默认权限'])
  })
})
