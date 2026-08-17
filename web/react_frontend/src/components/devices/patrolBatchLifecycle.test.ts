import { describe, expect, it } from 'vitest'

import { canCancelPatrolBatch } from './patrolBatchLifecycle'

describe('canCancelPatrolBatch', () => {
  it('allows cancellation while a batch is waiting for dispatch without workflow runs', () => {
    expect(canCancelPatrolBatch({ dispatchStatus: 'pending' })).toBe(true)
    expect(canCancelPatrolBatch({ dispatchStatus: 'dispatching' })).toBe(true)
  })

  it('allows cancellation while device runs are active', () => {
    expect(canCancelPatrolBatch({ dispatchStatus: 'completed', runningCount: 1 })).toBe(true)
    expect(canCancelPatrolBatch({ dispatchStatus: 'completed', pendingCount: 2 })).toBe(true)
  })

  it('rejects terminal and archived batches', () => {
    expect(canCancelPatrolBatch({ dispatchStatus: 'cancelled' })).toBe(false)
    expect(canCancelPatrolBatch({ dispatchStatus: 'completed' })).toBe(false)
    expect(canCancelPatrolBatch({ archived: true, dispatchStatus: 'pending' })).toBe(false)
  })
})
