// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Tag } from 'antd'

export type BuilderNodeData = {
  label?: string
  skill?: string
  subworkflow?: string
  stepName?: string
  when?: string
  parallelGroup?: string
  inputs?: Record<string, string>
}

const handleStyle = { width: 8, height: 8, background: '#1677ff' }

export const StartNode = memo(({ data }: NodeProps) => (
  <div className="wf-node wf-node-start">
    <div className="wf-node-title">{String(data.label || '开始')}</div>
    <Handle type="source" position={Position.Bottom} style={handleStyle} />
  </div>
))
StartNode.displayName = 'StartNode'

export const EndNode = memo(({ data }: NodeProps) => (
  <div className="wf-node wf-node-end">
    <Handle type="target" position={Position.Top} style={handleStyle} />
    <div className="wf-node-title">{String(data.label || '结束')}</div>
  </div>
))
EndNode.displayName = 'EndNode'

export const SkillNode = memo(({ data, selected }: NodeProps) => {
  const d = data as BuilderNodeData
  return (
    <div className={`wf-node wf-node-skill${selected ? ' wf-node-selected' : ''}`}>
      <Handle type="target" position={Position.Top} style={handleStyle} />
      <div className="wf-node-skill-head">
        <Tag color="blue">Skill</Tag>
        {d.when && <Tag color="orange">when</Tag>}
        {d.parallelGroup && <Tag color="purple">并行</Tag>}
      </div>
      <div className="wf-node-title">{d.label || d.skill || '未选择 Skill'}</div>
      <div className="wf-node-sub">{d.skill}</div>
      {d.when && <div className="wf-node-when">{d.when}</div>}
      <Handle type="source" position={Position.Bottom} style={handleStyle} />
    </div>
  )
})
SkillNode.displayName = 'SkillNode'

export const ConditionNode = memo(({ data, selected }: NodeProps) => (
  <div className={`wf-node wf-node-condition${selected ? ' wf-node-selected' : ''}`}>
    <Handle type="target" position={Position.Top} style={handleStyle} />
    <div className="wf-node-title">{String(data.label || '条件分支')}</div>
    <div className="wf-node-sub">when 表达式配置在出边上</div>
    <Handle
      type="source"
      position={Position.Bottom}
      id="yes"
      style={{ ...handleStyle, left: '35%' }}
    />
    <Handle
      type="source"
      position={Position.Bottom}
      id="no"
      style={{ ...handleStyle, left: '65%' }}
    />
  </div>
))
ConditionNode.displayName = 'ConditionNode'

export const SubworkflowNode = memo(({ data, selected }: NodeProps) => {
  const d = data as BuilderNodeData
  return (
    <div className={`wf-node wf-node-subworkflow${selected ? ' wf-node-selected' : ''}`}>
      <Handle type="target" position={Position.Top} style={handleStyle} />
      <div className="wf-node-skill-head">
        <Tag color="cyan">子流程</Tag>
        {d.when && <Tag color="orange">when</Tag>}
      </div>
      <div className="wf-node-title">{d.label || d.subworkflow || '未选择子 Workflow'}</div>
      <div className="wf-node-sub">{d.subworkflow}</div>
      <Handle type="source" position={Position.Bottom} style={handleStyle} />
    </div>
  )
})
SubworkflowNode.displayName = 'SubworkflowNode'

export const builderNodeTypes = {
  start: StartNode,
  end: EndNode,
  skill: SkillNode,
  condition: ConditionNode,
  subworkflow: SubworkflowNode,
}
