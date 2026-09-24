import React, { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Input, Space, Typography, message } from 'antd'
import { useMutation, useQuery } from 'react-query'
import { parserApi, type ParserRuleAssetBundle } from '@netops/services/api'

interface Props { bundle: ParserRuleAssetBundle | null; open: boolean; onClose: () => void; onChanged: () => void }
const RuleAssetEngineeringReviewDrawer: React.FC<Props> = ({ bundle, open, onClose, onChanged }) => {
  const [reason, setReason] = useState('')
  useEffect(() => setReason(''), [bundle?.id])
  const draft = useQuery(['rule-asset-engineering-draft', bundle?.id],
    () => parserApi.ruleAssetEngineeringDraft(bundle!.id), { enabled: open && Boolean(bundle?.id) })
  const review = useMutation((decision: 'accepted' | 'revision_requested') =>
    parserApi.reviewRuleAssetBundle(bundle!.id, { decision, reason: reason.trim() }), {
      onSuccess: () => { message.success('复核意见已保存'); onChanged(); onClose() },
      onError: (error: Error) => message.error(error.message || '保存失败'),
    })
  return <Drawer title="规则候选复核" open={open} onClose={onClose} width={880} destroyOnClose>
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Alert type="info" showIcon message="规则语义由检查项维护" description="需要调整 Value 或判断语义时，请在检查项目录修订后重新制作规则。" />
      {draft.isError && <Alert type="error" message="候选加载失败" action={<Button onClick={() => draft.refetch()}>重试</Button>} />}
      {draft.isLoading ? <Typography.Text>正在加载候选…</Typography.Text> : <>
        <Typography.Title level={5}>变化策略</Typography.Title>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(draft.data?.change_policies || [], null, 2)}</pre>
        <Typography.Title level={5}>状态与事件规则</Typography.Title>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(draft.data?.event_rules || [], null, 2)}</pre>
      </>}
      <Input.TextArea aria-label="复核意见" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="填写复核依据或修订要求（至少 5 个字）" rows={4} />
      <Space>
        <Button disabled={reason.trim().length < 5 || review.isLoading || !draft.data} onClick={() => review.mutate('revision_requested')}>要求修订</Button>
        <Button type="primary" disabled={reason.trim().length < 5 || review.isLoading || !draft.data} onClick={() => review.mutate('accepted')}>通过复核</Button>
      </Space>
    </Space>
  </Drawer>
}
export default RuleAssetEngineeringReviewDrawer
