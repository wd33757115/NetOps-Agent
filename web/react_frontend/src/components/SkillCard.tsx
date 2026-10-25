// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { Card, Switch, Typography } from 'antd'
import { EyeOutlined, ReloadOutlined, EditOutlined, SettingOutlined } from '@ant-design/icons'
import { SkillItem, SkillRolloutStatus } from '../services/api'
import { GrokChip, GrokToolBtn } from './ui/GrokUi'

const { Text, Paragraph } = Typography

const ROLLOUT_TONE: Record<SkillRolloutStatus, 'default' | 'warn' | 'ok'> = {
  draft: 'default',
  canary: 'warn',
  stable: 'ok',
  deprecated: 'warn',
}

const ROLLOUT_LABEL: Record<SkillRolloutStatus, string> = {
  draft: 'draft',
  canary: 'canary',
  stable: 'stable',
  deprecated: 'deprecated',
}

interface SkillCardProps {
  skill: SkillItem
  onToggle: (name: string, enabled: boolean) => void
  onReload: (name: string) => void
  onView: (name: string) => void
  onEdit: (name: string) => void
  onRollout?: (skill: SkillItem) => void
  loading?: boolean
}

const SkillCard: React.FC<SkillCardProps> = ({
  skill,
  onToggle,
  onReload,
  onView,
  onEdit,
  onRollout,
  loading,
}) => {
  const rollout = (skill.rollout_status || 'stable') as SkillRolloutStatus
  const ratio = skill.enabled_ratio ?? 100
  const rolloutLabel =
    rollout === 'canary' ? `canary ${ratio}%` : ROLLOUT_LABEL[rollout] || rollout

  return (
    <Card
      size="small"
      className="grok-skill-card"
      title={
        <span className="grok-skill-card-title is-inline">
          <span>{skill.name}</span>
          <GrokChip tone={skill.enabled ? 'ok' : 'default'}>
            {skill.enabled ? 'active' : 'inactive'}
          </GrokChip>
          <GrokChip tone={ROLLOUT_TONE[rollout] || 'default'}>{rolloutLabel}</GrokChip>
        </span>
      }
      extra={<GrokChip>{skill.category}</GrokChip>}
    >
      <Paragraph type="secondary" className="grok-skill-card-desc" ellipsis={{ rows: 2 }}>
        {skill.description || '暂无描述'}
      </Paragraph>
      <div className="grok-chip-row" style={{ marginBottom: 12 }}>
        {(skill.tags || []).slice(0, 4).map((tag) => (
          <GrokChip key={tag}>{tag}</GrokChip>
        ))}
      </div>
      <div className="grok-skill-card-actions">
        <Text type="secondary" style={{ fontSize: 12 }}>
          v{skill.version || '1.0.0'}
          {skill.domain && skill.domain !== skill.category ? ` · ${skill.domain}` : ''}
        </Text>
        <span className="grok-skill-card-btns">
          <Switch
            size="small"
            checked={skill.enabled}
            loading={loading}
            onChange={(checked) => onToggle(skill.name, checked)}
          />
          {onRollout && (
            <GrokToolBtn icon={<SettingOutlined />} onClick={() => onRollout(skill)}>
              灰度
            </GrokToolBtn>
          )}
          <GrokToolBtn icon={<EyeOutlined />} onClick={() => onView(skill.name)}>
            查看
          </GrokToolBtn>
          <GrokToolBtn icon={<EditOutlined />} onClick={() => onEdit(skill.name)}>
            编辑
          </GrokToolBtn>
          <GrokToolBtn icon={<ReloadOutlined />} onClick={() => onReload(skill.name)}>
            重载
          </GrokToolBtn>
        </span>
      </div>
    </Card>
  )
}

export default SkillCard
