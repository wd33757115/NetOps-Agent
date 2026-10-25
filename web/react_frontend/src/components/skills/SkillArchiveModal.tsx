// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect } from 'react'
import { Alert, Form, InputNumber, Modal, Typography, message } from 'antd'
import { useMutation } from 'react-query'
import { skillCatalogApi, SkillArchiveResult } from '../../services/api'

const { Paragraph, Text } = Typography

const DEFAULT_BEFORE_DAYS = 90
const DEFAULT_BATCH_SIZE = 500

interface SkillArchiveModalProps {
  open: boolean
  onClose: () => void
}

interface ArchiveFormValues {
  before_days: number
  batch_size: number
}

const SkillArchiveModal: React.FC<SkillArchiveModalProps> = ({ open, onClose }) => {
  const [form] = Form.useForm<ArchiveFormValues>()

  useEffect(() => {
    if (!open) return
    form.setFieldsValue({
      before_days: DEFAULT_BEFORE_DAYS,
      batch_size: DEFAULT_BATCH_SIZE,
    })
  }, [open, form])

  const archiveMutation = useMutation(
    (values: ArchiveFormValues) =>
      skillCatalogApi.archiveExecutions(values.before_days, values.batch_size),
    {
      onSuccess: (res: SkillArchiveResult) => {
        if (res.error) {
          message.error(res.error)
          return
        }
        if (res.skipped) {
          message.warning(res.reason || '归档功能未启用')
          onClose()
          return
        }
        if ((res.archived ?? 0) === 0) {
          message.info(`无早于 cutoff 的记录需归档（${formatCutoff(res.cutoff)}）`)
          onClose()
          return
        }
        message.success(`已归档 ${res.archived} 条执行记录`)
        Modal.info({
          title: '归档完成',
          width: 520,
          content: (
            <div style={{ marginTop: 8 }}>
              <Paragraph style={{ marginBottom: 8 }}>
                共归档 <Text strong>{res.archived}</Text> 条记录。
              </Paragraph>
              {res.cutoff && (
                <Paragraph type="secondary" style={{ marginBottom: 4, fontSize: 13 }}>
                  截止时间：{formatCutoff(res.cutoff)}
                </Paragraph>
              )}
              {res.object_key && (
                <Paragraph
                  copyable={{ text: res.object_key }}
                  style={{ marginBottom: 0, fontSize: 13, wordBreak: 'break-all' }}
                >
                  MinIO: {res.object_key}
                </Paragraph>
              )}
            </div>
          ),
        })
        onClose()
      },
      onError: (err: unknown) => {
        message.error(err instanceof Error ? err.message : '归档失败')
      },
    }
  )

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      Modal.confirm({
        title: '确认归档执行记录？',
        content: (
          <Paragraph style={{ marginBottom: 0 }}>
            将导出并删除早于 <Text strong>{values.before_days}</Text> 天的 Skill 执行记录至 MinIO，
            此操作不可撤销。
          </Paragraph>
        ),
        okText: '确认归档',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: () => archiveMutation.mutateAsync(values),
      })
    } catch {
      /* 表单校验失败 */
    }
  }

  return (
    <Modal
      title="归档 Skill 执行记录"
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      okText="开始归档"
      okButtonProps={{ danger: true, loading: archiveMutation.isLoading }}
      cancelButtonProps={{ disabled: archiveMutation.isLoading }}
      destroyOnClose
    >
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="归档会将 PostgreSQL 中的历史执行记录导出为 JSONL.gz 并上传 MinIO，随后从数据库删除。"
      />
      <Form form={form} layout="vertical">
        <Form.Item
          name="before_days"
          label="归档天数"
          tooltip="早于该天数的记录将被归档（相对当前时间）"
          rules={[
            { required: true, message: '请输入归档天数' },
            { type: 'number', min: 1, max: 3650, message: '范围 1–3650 天' },
          ]}
        >
          <InputNumber min={1} max={3650} style={{ width: '100%' }} addonAfter="天" />
        </Form.Item>
        <Form.Item
          name="batch_size"
          label="批次大小"
          tooltip="每批从数据库读取并处理的记录数"
          rules={[
            { required: true, message: '请输入批次大小' },
            { type: 'number', min: 1, max: 5000, message: '范围 1–5000' },
          ]}
        >
          <InputNumber min={1} max={5000} style={{ width: '100%' }} addonAfter="条/批" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

function formatCutoff(iso?: string): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN')
  } catch {
    return iso
  }
}

export default SkillArchiveModal
