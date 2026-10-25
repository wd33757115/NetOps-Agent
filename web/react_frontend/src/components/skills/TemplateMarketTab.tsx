// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react'
import { Card, Empty, Input, Select, Spin, Tag, Typography, Upload, message } from 'antd'
import { CloudDownloadOutlined, ImportOutlined, RocketOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from 'react-query'
import { GrokChip, GrokToolBtn } from '../ui/GrokUi'
import { workflowApi, MarketTemplateSummary, WorkflowImportBundle } from '../../services/api'
import WorkflowWizard, { WorkflowWizardInitial } from './WorkflowWizard'
import WorkflowWizardShell from './WorkflowWizardShell'

const { Paragraph, Text } = Typography

const TemplateMarketTab: React.FC = () => {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<string | undefined>()
  const [wizardOpen, setWizardOpen] = useState(false)
  const [wizardInitial, setWizardInitial] = useState<WorkflowWizardInitial | null>(null)
  const [usingId, setUsingId] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)

  const { data: templates = [], isLoading, refetch } = useQuery(
    ['market-templates', category],
    () => workflowApi.listMarketTemplates(category ? { category } : undefined),
    { refetchOnWindowFocus: false },
  )

  const categories = Array.from(new Set(templates.map((t) => t.category))).sort()

  const filtered = templates.filter((t: MarketTemplateSummary) => {
    const q = search.trim().toLowerCase()
    if (!q) return true
    return (
      t.title.toLowerCase().includes(q) ||
      t.description.toLowerCase().includes(q) ||
      t.id.includes(q)
    )
  })

  const handleUseTemplate = async (tpl: MarketTemplateSummary) => {
    setUsingId(tpl.id)
    try {
      const detail = await workflowApi.getMarketTemplate(tpl.id)
      const defaultName = detail.source_plugin_name || `from-${tpl.id.replace(/^market-/, '')}`

      setWizardInitial({
        pluginName: `${defaultName}-copy`,
        category: detail.category,
        description: detail.description,
        workflowYaml: detail.files['WORKFLOW.yaml'],
        chatIntentYaml: detail.files['CHAT.intent.yaml'],
      })
      setWizardOpen(true)
      message.success('已加载市场模板，请调整参数后保存')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载市场模板失败')
    } finally {
      setUsingId(null)
    }
  }

  const handleImportJson = async (file: File) => {
    setImporting(true)
    try {
      const text = await file.text()
      const bundle = JSON.parse(text) as WorkflowImportBundle
      if (!bundle.name || !bundle.files?.['WORKFLOW.yaml']) {
        message.error('无效的导入包')
        return false
      }
      const result = await workflowApi.importPlugin({ bundle, overwrite: false })
      message.success(result.message || '导入成功')
      queryClient.invalidateQueries('workflow-plugins')
      refetch()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '导入失败')
    } finally {
      setImporting(false)
    }
    return false
  }

  return (
    <>
      <Paragraph type="secondary">
        浏览内置与用户发布的 Workflow 模板，一键导入为草稿或直接基于模板创建插件。
      </Paragraph>

      <div className="grok-page-toolbar grok-page-toolbar-inline" style={{ marginBottom: 16 }}>
        <Input
          className="grok-search-input"
          placeholder="搜索模板…"
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Select
          allowClear
          placeholder="分类"
          style={{ width: 140 }}
          value={category}
          onChange={setCategory}
          options={categories.map((c) => ({ value: c, label: c }))}
        />
        <Upload accept=".json" showUploadList={false} beforeUpload={handleImportJson}>
          <GrokToolBtn icon={<ImportOutlined />} disabled={importing}>
            {importing ? '导入中…' : '导入 JSON 包'}
          </GrokToolBtn>
        </Upload>
        <GrokToolBtn icon={<CloudDownloadOutlined />} onClick={() => refetch()}>
          刷新
        </GrokToolBtn>
      </div>

      {isLoading ? (
        <div className="grok-page-loading"><Spin size="large" /></div>
      ) : filtered.length === 0 ? (
        <Empty description="暂无市场模板" className="grok-empty" />
      ) : (
        <div className="grok-skill-grid">
          {filtered.map((tpl) => (
            <Card key={tpl.id} className="grok-skill-card" bordered={false}>
              <div className="grok-skill-card-inner">
                <div className="grok-skill-card-main">
                  <div className="grok-skill-card-title">{tpl.title}</div>
                  <Paragraph type="secondary" className="grok-skill-card-desc" ellipsis={{ rows: 2 }}>
                    {tpl.description || '—'}
                  </Paragraph>
                  <div className="grok-chip-row grok-skill-card-tags">
                    <GrokChip>{tpl.category}</GrokChip>
                    {tpl.featured && <Tag color="gold">精选</Tag>}
                    {tpl.tags?.map((tag) => (
                      <Tag key={tag}>{tag}</Tag>
                    ))}
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      使用 {tpl.use_count} 次
                    </Text>
                  </div>
                </div>
                <div className="grok-skill-card-footer">
                  <div className="grok-skill-card-btns">
                    <GrokToolBtn
                      primary
                      icon={<RocketOutlined />}
                      disabled={usingId === tpl.id}
                      onClick={() => handleUseTemplate(tpl)}
                    >
                      {usingId === tpl.id ? '加载中…' : '使用模板'}
                    </GrokToolBtn>
                  </div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <WorkflowWizardShell
        title="基于市场模板创建 Workflow"
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
      >
        <WorkflowWizard
          open={wizardOpen}
          onClose={() => setWizardOpen(false)}
          initial={wizardInitial}
          startStep={0}
          onSaved={() => {
            setWizardOpen(false)
            queryClient.invalidateQueries('workflow-plugins')
          }}
        />
      </WorkflowWizardShell>
    </>
  )
}

export default TemplateMarketTab
