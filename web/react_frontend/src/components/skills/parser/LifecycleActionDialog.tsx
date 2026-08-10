import React, { useEffect, useState } from 'react'
import { Alert, Input, Modal, Space, Typography } from 'antd'

interface LifecycleActionDialogProps {
  open: boolean
  title: string
  actionLabel: string
  resourceName: string
  impact: string
  blockers?: string[]
  danger?: boolean
  loading?: boolean
  reasonRequired?: boolean
  onCancel: () => void
  onConfirm: (reason: string) => void
}

const LifecycleActionDialog: React.FC<LifecycleActionDialogProps> = ({
  open,
  title,
  actionLabel,
  resourceName,
  impact,
  blockers = [],
  danger = false,
  loading = false,
  reasonRequired = true,
  onCancel,
  onConfirm,
}) => {
  const [reason, setReason] = useState('')

  useEffect(() => {
    if (!open) setReason('')
  }, [open])

  const normalizedReason = reason.trim()
  const blocked = blockers.length > 0

  return (
    <Modal
      open={open}
      title={title}
      okText={actionLabel}
      cancelText="取消"
      okButtonProps={{
        danger,
        disabled: blocked || (reasonRequired && normalizedReason.length < 5),
      }}
      confirmLoading={loading}
      onCancel={onCancel}
      onOk={() => onConfirm(normalizedReason)}
      destroyOnHidden
    >
      <Space direction="vertical" size={14} style={{ width: '100%' }}>
        <Alert
          type={danger ? 'warning' : 'info'}
          showIcon
          message={resourceName}
          description={impact}
        />
        {blocked && (
          <Alert
            type="error"
            showIcon
            message="当前操作被质量门禁阻断"
            description={blockers.join('；')}
          />
        )}
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <Typography.Text strong>{reasonRequired ? '操作理由' : '操作说明（可选）'}</Typography.Text>
          <Input.TextArea
            autoFocus
            rows={4}
            maxLength={500}
            showCount
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={reasonRequired
              ? '说明变更目的、依据或回退原因（至少 5 个字符）'
              : '可选；操作人、时间、对象和结果会自动写入审计记录'}
          />
          <Typography.Text type="secondary">
            操作者由当前登录会话确定，操作对象和结果会写入审计记录。
          </Typography.Text>
        </Space>
      </Space>
    </Modal>
  )
}

export default LifecycleActionDialog
