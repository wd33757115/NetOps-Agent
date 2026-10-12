import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Modal, Typography, message } from 'antd'
import type { CheckItemRollbackPreview, ParserCheckItem } from '@netops/services/api'
import { parserApi } from '@netops/services/api'
import CheckItemReleasePreview from './CheckItemReleasePreview'
import { queryErrorLabel } from './parserUi'

const key = (scope: string) => `${scope}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

interface CheckItemRollbackDialogProps {
  checkItem?: ParserCheckItem
  /** The version whose content comes back; shown before the server has answered. */
  restoresVersion?: number | null
  onClose: () => void
  onDone: () => void | Promise<void>
}

/**
 * Going back to the previous release, shown before anything switches.
 *
 * Opening the dialog drafts the earlier content as a new version so that the
 * server can try the release; leaving without confirming withdraws that draft.
 */
const CheckItemRollbackDialog: React.FC<CheckItemRollbackDialogProps> = ({ checkItem, restoresVersion, onClose, onDone }) => {
  const checkItemId = checkItem?.check_item_id
  const name = checkItem?.display_name || checkItem?.name || ''
  const [rollback, setRollback] = useState<CheckItemRollbackPreview>()
  const [error, setError] = useState<string>()
  const [working, setWorking] = useState(false)
  // The draft this dialog made and has neither published nor withdrawn.
  const pending = useRef<string>()

  useEffect(() => {
    setRollback(undefined)
    setError(undefined)
    if (!checkItemId) return undefined
    let current = true
    parserApi.previewCheckItemRollback(checkItemId).then((result) => {
      if (!current) {
        // Closed before the answer came: the draft has no dialog left to confirm it.
        void parserApi.discardCatalogDraft(result.change_id).catch(() => undefined)
        return
      }
      pending.current = result.change_id
      setRollback(result)
    }).catch((reason) => {
      if (current) setError(queryErrorLabel(reason))
    })
    return () => { current = false }
  }, [checkItemId])

  const leave = async () => {
    const changeId = pending.current
    pending.current = undefined
    onClose()
    if (changeId) await parserApi.discardCatalogDraft(changeId).catch(() => undefined)
    await onDone()
  }

  const confirm = async () => {
    if (!rollback) return
    setWorking(true)
    try {
      const body = { reason: `回退到 v${rollback.restores_version}`, expected_impact_digest: rollback.impact_digest }
      await parserApi.submitCatalogChange(rollback.change_id, body, key('catalog-rollback-submit'))
      await parserApi.decideCatalogChange(rollback.change_id, 'approve', body, key('catalog-rollback-approve'))
      pending.current = undefined
      message.success(`“${name}”已回退到 v${rollback.restores_version} 的内容，规则和运行包已一起切换`)
      onClose()
      await onDone()
    } catch (reason) {
      message.error(queryErrorLabel(reason))
    } finally {
      setWorking(false)
    }
  }

  const target = rollback?.restores_version ?? restoresVersion
  const releasable = Boolean(rollback?.preview.applicable && rollback.preview.releasable)

  return (
    <Modal
      open={Boolean(checkItem)}
      width={880}
      centered
      maskClosable={false}
      title={`把“${name}”回退到 v${target ?? ''}`}
      onCancel={() => void leave()}
      footer={[
        <Button key="cancel" disabled={working} onClick={() => void leave()}>取消</Button>,
        <Button key="rollback" type="primary" disabled={!releasable} loading={working} onClick={() => void confirm()}>
          回退到 v{target ?? ''}
        </Button>,
      ]}
    >
      {!rollback && !error && (
        <Typography.Text type="secondary">正在核对回退会切换哪些规则、适配器和运行包…</Typography.Text>
      )}
      {error && <Alert type="error" showIcon message="无法准备回退" description={error} />}
      {rollback && (
        <div className="check-item-rollback">
          <Typography.Paragraph>
            回退会发布一个新版本 v{rollback.new_version}，内容与 v{rollback.restores_version} 相同，规则、适配器和运行包随它一起切换。
            现在的 v{rollback.current_version} 留在历史里，之后还能再回退回来。确认前不会有任何改动。
          </Typography.Paragraph>
          {!rollback.restores_wording && (
            <Typography.Paragraph type="secondary">
              v{rollback.restores_version} 发布时没有留下名称和说明，这两项保持现在的内容。
            </Typography.Paragraph>
          )}
          <CheckItemReleasePreview preview={rollback.preview} />
        </div>
      )}
    </Modal>
  )
}

export default CheckItemRollbackDialog
