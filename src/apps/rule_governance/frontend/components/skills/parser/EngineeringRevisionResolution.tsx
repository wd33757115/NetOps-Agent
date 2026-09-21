import React, { useState } from 'react'
import { Alert, Button, Input, Space, Table, Typography, message } from 'antd'
import { useMutation, useQuery } from 'react-query'
import { parserApi } from '@netops/services/api'

const errorText = (error: any) => {
  const detail = error?.response?.data?.detail
  return typeof detail === 'string' ? detail : detail?.message || error?.message || '核查操作失败，请刷新后重试'
}

export const EngineeringRevisionResolution: React.FC<{ taskId: string; onResolved: () => void }> = ({ taskId, onResolved }) => {
  const [note, setNote] = useState('')
  const inspection = useQuery(['engineering-revision-inspection', taskId], () => parserApi.inspectEngineeringRevision(taskId), {
    retry: false,
    onSuccess: (data) => {
      if (data.state === 'resolved') onResolved()
    },
  })
  const resolve = useMutation(
    () => parserApi.resolveEngineeringRevision(taskId, { evidence_digest: inspection.data!.digest, note: note.trim() }),
    {
      onSuccess: () => {
        message.success('核查已记录，任务已结案；候选资产仍按原流程复核和审批')
        onResolved()
      },
      onError: (error) => {
        message.error(errorText(error))
        void inspection.refetch()
      },
    },
  )
  return (
    <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
      <Typography.Title level={5}>核查中断任务</Typography.Title>
      <Typography.Paragraph>请核对下列候选资产及校验记录，填写处理说明后结案。结案不会批准、删除或重新生成资产。</Typography.Paragraph>
      {inspection.isError && <Alert type="error" showIcon message={errorText(inspection.error)} />}
      {inspection.data?.blocked_reason && <Alert type="warning" showIcon message={inspection.data.blocked_reason} />}
      {inspection.data && (
        <Table size="small" rowKey="id" pagination={false} dataSource={inspection.data.records}
          locale={{ emptyText: '未发现该请求关联的候选资产，可在说明中记录核查结果' }}
          columns={[
            { title: '资产记录', dataIndex: 'id', render: (id: string) => <Typography.Text copyable>{id}</Typography.Text> },
            { title: '类型', dataIndex: 'job_type' },
            { title: '状态', dataIndex: 'status' },
          ]}
          expandable={{ expandedRowRender: (record) => <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(record.validation || {}, null, 2)}</pre> }} />
      )}
      <Input.TextArea aria-label="中断任务核查说明" placeholder="记录已核查的资产、结果及后续处理安排（至少 5 个字符）" value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} />
      <Space>
        <Button onClick={() => void inspection.refetch()} loading={inspection.isFetching}>刷新核查结果</Button>
        <Button type="primary" loading={resolve.isLoading}
          disabled={!inspection.data?.can_resolve || inspection.isError || inspection.isFetching || note.trim().length < 5}
          onClick={() => resolve.mutate()}>记录核查并结案</Button>
      </Space>
    </Space>
  )
}
