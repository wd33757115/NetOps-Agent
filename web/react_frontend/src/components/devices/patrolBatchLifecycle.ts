export interface PatrolBatchCancellationState {
  archived?: boolean
  dispatchStatus?: string | null
  runningCount?: number
  pendingCount?: number
}

const CANCELLABLE_DISPATCH_STATUSES = new Set(['pending', 'dispatching'])

export const canCancelPatrolBatch = ({
  archived,
  dispatchStatus,
  runningCount = 0,
  pendingCount = 0,
}: PatrolBatchCancellationState): boolean => (
  !archived
  && (
    runningCount + pendingCount > 0
    || CANCELLABLE_DISPATCH_STATUSES.has(String(dispatchStatus || '').toLowerCase())
  )
)
