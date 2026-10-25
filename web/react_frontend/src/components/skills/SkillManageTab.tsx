// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useMemo, useState } from 'react'
import { Col, Empty, Input, Modal, Row, Spin, Space, Typography, message } from 'antd'
import { DatabaseOutlined, InboxOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import SkillCard from '../SkillCard'
import SkillEditorModal from '../SkillEditorModal'
import SkillCreateWizard from './SkillCreateWizard'
import SkillRolloutDrawer from './SkillRolloutDrawer'
import SkillArchiveModal from './SkillArchiveModal'
import { GrokToolBtn } from '../ui/GrokUi'
import { useAuth } from '../../context/AuthContext'
import { skillApi, skillCatalogApi, SkillItem } from '../../services/api'

const { Text } = Typography

interface SkillManageTabProps {
  onOpenWizard?: () => void
}

const SkillManageTab: React.FC<SkillManageTabProps> = () => {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [viewContent, setViewContent] = useState<string | null>(null)
  const [viewName, setViewName] = useState<string | null>(null)
  const [editName, setEditName] = useState<string | null>(null)
  const [actionSkill, setActionSkill] = useState<string | null>(null)
  const [rolloutSkill, setRolloutSkill] = useState<SkillItem | null>(null)
  const [archiveOpen, setArchiveOpen] = useState(false)

  const { data: skills = [], isLoading, refetch } = useQuery<SkillItem[]>(
    'skills',
    skillApi.list,
    { refetchOnWindowFocus: false }
  )

  const { data: catalogStats } = useQuery(
    'skill-catalog-stats',
    skillCatalogApi.getStats,
    { refetchOnWindowFocus: false }
  )

  const reindexMutation = useMutation(
    (force: boolean) => skillCatalogApi.reindex(force),
    {
      onSuccess: (res) => {
        message.success(`Catalog 索引完成（${res.indexed ?? res.total ?? ''} 条）`)
        queryClient.invalidateQueries('skills')
        queryClient.invalidateQueries('skill-catalog-stats')
      },
      onError: () => message.error('Catalog 重建索引失败'),
    }
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return skills
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.description || '').toLowerCase().includes(q) ||
        (s.tags || []).some((t) => t.toLowerCase().includes(q))
    )
  }, [skills, search])

  const toggleMutation = useMutation(
    ({ name, enabled }: { name: string; enabled: boolean }) => skillApi.toggle(name, enabled),
    { onSuccess: () => queryClient.invalidateQueries('skills') }
  )

  const reloadMutation = useMutation(
    (name: string) => skillApi.reload(name),
    {
      onSuccess: (_, name) => {
        message.success(`已重载 ${name}`)
        queryClient.invalidateQueries('skills')
      },
    }
  )

  const handleView = async (name: string) => {
    try {
      const res = await skillApi.getContent(name)
      setViewName(name)
      setViewContent(res.content)
    } catch {
      message.error('读取 Skill 内容失败')
    }
  }

  return (
    <>
      {catalogStats && (
        <div className="grok-page-toolbar grok-page-toolbar-inline" style={{ marginBottom: 12 }}>
          <Space wrap size="middle">
            <Text type="secondary" style={{ fontSize: 13 }}>
              <DatabaseOutlined style={{ marginRight: 6 }} />
              Catalog: {catalogStats.total} 条 · 启用 {catalogStats.enabled} · 已索引{' '}
              {catalogStats.indexed}
            </Text>
            {isAdmin && (
              <>
                <GrokToolBtn
                  icon={<ReloadOutlined />}
                  loading={reindexMutation.isLoading}
                  onClick={() => reindexMutation.mutate(false)}
                >
                  重建索引
                </GrokToolBtn>
                <GrokToolBtn icon={<InboxOutlined />} onClick={() => setArchiveOpen(true)}>
                  归档执行记录
                </GrokToolBtn>
              </>
            )}
          </Space>
        </div>
      )}

      <div className="grok-page-toolbar grok-page-toolbar-inline">
        <Input
          className="grok-search-input"
          placeholder="搜索 Skill…"
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <GrokToolBtn icon={<ReloadOutlined />} onClick={() => refetch()}>
          刷新
        </GrokToolBtn>
        <GrokToolBtn primary icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建 Skill
        </GrokToolBtn>
      </div>

      {isLoading ? (
        <div className="grok-page-loading">
          <Spin size="large" />
        </div>
      ) : filtered.length === 0 ? (
        <Empty description="暂无 Skill" className="grok-empty" />
      ) : (
        <Row gutter={[16, 16]}>
          {filtered.map((skill) => (
            <Col xs={24} md={12} xl={8} key={skill.name}>
              <SkillCard
                skill={skill}
                loading={actionSkill === skill.name && (toggleMutation.isLoading || reloadMutation.isLoading)}
                onToggle={(name, enabled) => {
                  setActionSkill(name)
                  toggleMutation.mutate({ name, enabled })
                }}
                onReload={(name) => {
                  setActionSkill(name)
                  reloadMutation.mutate(name)
                }}
                onView={handleView}
                onEdit={setEditName}
                onRollout={isAdmin ? setRolloutSkill : undefined}
              />
            </Col>
          ))}
        </Row>
      )}

      <Modal
        title={viewName ? `查看 ${viewName}` : '查看 Skill'}
        open={!!viewContent}
        onCancel={() => { setViewContent(null); setViewName(null) }}
        footer={null}
        width={800}
      >
        <Input.TextArea value={viewContent || ''} readOnly rows={20} style={{ fontFamily: 'monospace' }} />
      </Modal>

      <SkillCreateWizard
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => queryClient.invalidateQueries('skills')}
      />

      <SkillEditorModal
        open={!!editName}
        skillName={editName}
        onClose={() => setEditName(null)}
        onSaved={() => queryClient.invalidateQueries('skills')}
      />

      <SkillRolloutDrawer
        skill={rolloutSkill}
        open={!!rolloutSkill}
        onClose={() => setRolloutSkill(null)}
      />

      <SkillArchiveModal open={archiveOpen} onClose={() => setArchiveOpen(false)} />
    </>
  )
}

export default SkillManageTab
