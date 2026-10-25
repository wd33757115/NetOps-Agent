// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useMemo, useState } from 'react'
import { Input, Tag, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import type { SkillItem } from '../../services/api'
import { GrokToolBtn } from '../ui/GrokUi'

const { Text } = Typography

interface SkillPaletteProps {
  skills: SkillItem[]
  onAddSkill: (skillName: string) => void
  onAddCondition: () => void
  workflowTemplates?: string[]
  onAddSubworkflow?: (templateName: string) => void
}

const SkillPalette: React.FC<SkillPaletteProps> = ({
  skills,
  onAddSkill,
  onAddCondition,
  workflowTemplates = [],
  onAddSubworkflow,
}) => {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return skills
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.category || '').toLowerCase().includes(q) ||
        (s.description || '').toLowerCase().includes(q),
    )
  }, [skills, search])

  const byCategory = useMemo(() => {
    const map = new Map<string, SkillItem[]>()
    for (const s of filtered) {
      const cat = s.category || 'general'
      if (!map.has(cat)) map.set(cat, [])
      map.get(cat)!.push(s)
    }
    return map
  }, [filtered])

  return (
    <div className="wf-skill-palette">
      <Text strong>Skill 面板</Text>
      <Input
        size="small"
        placeholder="搜索 Skill…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{ margin: '8px 0' }}
        allowClear
      />
      <GrokToolBtn icon={<PlusOutlined />} onClick={onAddCondition} style={{ width: '100%', marginBottom: 8 }}>
        插入条件分支
      </GrokToolBtn>
      {workflowTemplates.length > 0 && onAddSubworkflow && (
        <div className="wf-skill-palette-group" style={{ marginBottom: 8 }}>
          <Tag color="cyan">子 Workflow</Tag>
          {workflowTemplates.slice(0, 12).map((name) => (
            <div
              key={name}
              className="wf-skill-palette-item"
              onClick={() => onAddSubworkflow(name)}
              title={`嵌套执行 ${name}`}
            >
              <Text>{name}</Text>
            </div>
          ))}
        </div>
      )}
      <div className="wf-skill-palette-list">
        {Array.from(byCategory.entries()).map(([cat, items]) => (
          <div key={cat} className="wf-skill-palette-group">
            <Tag>{cat}</Tag>
            {items.map((s) => (
              <div
                key={s.name}
                className="wf-skill-palette-item"
                onClick={() => onAddSkill(s.name)}
                title={s.description}
              >
                <Text>{s.name}</Text>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

export default SkillPalette
