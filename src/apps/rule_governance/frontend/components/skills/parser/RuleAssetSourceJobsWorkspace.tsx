import ReusePatrolSamples from './ReusePatrolSamples'
import React from 'react'
import { Alert, Space, Typography } from 'antd'
import { useSearchParams } from 'react-router-dom'

import { RuleAssetSourceWorkbench } from '../../RuleAssetSourceWorkbench'

/** Parser control owns these evidence jobs; they never execute a patrol task. */
const RuleAssetSourceJobsWorkspace: React.FC = () => {
  const [searchParams] = useSearchParams()
  const action = searchParams.get('action')
  const isAssetGapSource = action === 'asset-gap-source'
  const focusedCommand = [
    'supplement-samples',
    'rule-gap-source',
    'asset-gap-source',
  ].includes(action || '')
    ? searchParams.get('command')?.trim()
    : ''
  const observationType = action === 'rule-gap-source'
    ? searchParams.get('observation_type')?.trim()
    : ''
  const assetGapId = isAssetGapSource
    ? searchParams.get('asset_gap_id')?.trim()
    : ''
  const checkItemId = isAssetGapSource
    ? searchParams.get('check_item_id')?.trim()
    : ''
  const implementationVersionId = isAssetGapSource
    ? searchParams.get('implementation_version_id')?.trim()
    : ''
  const initialJobName = focusedCommand
    ? `${checkItemId || focusedCommand}${observationType ? ` · ${observationType}` : ''} CLI 样本`
    : undefined

  const patrolSourceId = searchParams.get('patrol_source_job_id')
  if (patrolSourceId) return <ReusePatrolSamples patrolId={patrolSourceId} />

  return (
    <Space className="parser-source-jobs-workspace" direction="vertical" size={16} style={{ width: '100%' }}>
      {isAssetGapSource && (
        <Alert
          showIcon
          type="info"
          message="从生产资产缺口导入 CLI 样本"
          description={(
            <Space size={[8, 4]} wrap>
              <Typography.Text>缺口</Typography.Text>
              <Typography.Text code>{assetGapId || '未指定'}</Typography.Text>
              <Typography.Text>检查项</Typography.Text>
              <Typography.Text code>{checkItemId || '未指定'}</Typography.Text>
              <Typography.Text>实现版本</Typography.Text>
              <Typography.Text code>{implementationVersionId || '未指定'}</Typography.Text>
              <Typography.Text>命令</Typography.Text>
              <Typography.Text code>{focusedCommand || '未指定'}</Typography.Text>
              <Typography.Text type="secondary">
                此入口只预填证据范围；导入完成后，命令会进入检查项目录的待归类分组。
              </Typography.Text>
            </Space>
          )}
        />
      )}
      <RuleAssetSourceWorkbench
       
        initialJobType={focusedCommand ? 'parser_validation' : undefined}
        initialJobName={initialJobName}
      />
    </Space>
  )
}

export default RuleAssetSourceJobsWorkspace
