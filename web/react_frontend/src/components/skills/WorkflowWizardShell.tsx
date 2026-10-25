// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { Modal } from 'antd'

interface WorkflowWizardShellProps {
  title: string
  open: boolean
  onClose: () => void
  children: React.ReactNode
  className?: string
}

/** Workflow 向导容器：与 Skill 向导一致使用 Modal，禁止点击遮罩关闭 */
const WorkflowWizardShell: React.FC<WorkflowWizardShellProps> = ({
  title,
  open,
  onClose,
  children,
  className = 'grok-wizard-card',
}) => (
  <Modal
    title={title}
    open={open}
    onCancel={onClose}
    footer={null}
    destroyOnClose
    maskClosable={false}
    className={`grok-workflow-wizard-modal ${className}`}
    wrapClassName="grok-workflow-wizard-wrap"
    styles={{ body: { maxHeight: 'calc(92vh - 110px)', overflowY: 'auto' } }}
  >
    {children}
  </Modal>
)

export default WorkflowWizardShell
