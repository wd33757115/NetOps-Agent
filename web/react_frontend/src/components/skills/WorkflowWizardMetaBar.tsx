// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { Alert } from 'antd'
import { GrokToolBtn } from '../ui/GrokUi'

interface WorkflowWizardMetaBarProps {
  step: number
  pluginName?: string
  isEditMode: boolean
  onGoBasic: () => void
}

/** 步骤 1+ 顶部展示插件名；未填写时提示返回基础信息（避免重复 Form.Item 引发循环更新） */
const WorkflowWizardMetaBar: React.FC<WorkflowWizardMetaBarProps> = ({
  step,
  pluginName,
  isEditMode,
  onGoBasic,
}) => {
  if (step === 0) return null

  const normalized = pluginName?.trim()

  if (normalized) {
    return (
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={`插件名：${normalized}`}
        description={isEditMode ? '编辑模式下插件名不可修改' : '如需修改请返回「基础信息」'}
        action={
          !isEditMode ? (
            <GrokToolBtn onClick={onGoBasic}>修改</GrokToolBtn>
          ) : undefined
        }
      />
    )
  }

  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 16 }}
      message="尚未填写插件名（目录名）"
      description="插件名将作为目录名与 Workflow 标识。请返回「基础信息」步骤填写。"
      action={<GrokToolBtn onClick={onGoBasic}>前往基础信息</GrokToolBtn>}
    />
  )
}

export default WorkflowWizardMetaBar
