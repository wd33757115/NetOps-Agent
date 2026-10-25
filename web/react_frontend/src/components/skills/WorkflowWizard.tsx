// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Form, Input, Modal, Radio, Select, Steps, Tabs, Typography, message } from 'antd'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { skillApi, workflowApi } from '../../services/api'
import { useAuth } from '../../context/AuthContext'
import SkillStepConfigPanel from './SkillStepConfigPanel'
import WorkflowTriggerStep from './WorkflowTriggerStep'
import WorkflowDryRunPanel from './WorkflowDryRunPanel'
import WorkflowCanvas from '../workflow-builder/WorkflowCanvas'
import WorkflowRunMonitor from './WorkflowRunMonitor'
import WorkflowWizardMetaBar from './WorkflowWizardMetaBar'
import { GrokToolBtn } from '../ui/GrokUi'
import {
  normalizeWorkflowYaml,
  parseWorkflowName,
  syncChatIntentWorkflow,
} from './workflowYamlBuilder'
import {
  createEmptyGraph,
  dslToGraph,
  graphToDslSteps,
  graphToWorkflowDsl,
  type WorkflowGraph,
} from '../../types/workflowGraph'
import {
  FIREWALL_POLICY_ACTION,
  buildDslFromWizard,
  mergeChatYamlIntoDsl,
  type WorkflowDSL,
  type WizardFormValues,
  type ItsmWebhookDSL,
} from '../../types/workflowDsl'

const { TextArea } = Input
const { Text } = Typography

export interface WorkflowWizardInitial {
  pluginName?: string
  category?: string
  description?: string
  workflowYaml?: string
  chatIntentYaml?: string
  templateId?: string
  /** 协同模板 / 预填 Skill 链 */
  step1_skill?: string
  step2_skill?: string
  /** 直接传入 DSL（协同模板 preview 后） */
  initialDsl?: WorkflowDSL
}

interface WorkflowWizardProps {
  open: boolean
  onClose: () => void
  initial?: WorkflowWizardInitial | null
  onSaved?: () => void
  /** 打开时跳转到指定步骤（协同模板直达「配置参数」） */
  startStep?: number
}

interface WizardNavProps {
  onCancel: () => void
  onBack?: () => void
  backLabel?: string
  children?: React.ReactNode
  align?: 'split' | 'right'
}

const WizardNav: React.FC<WizardNavProps> = ({
  onCancel,
  onBack,
  backLabel = '上一步',
  children,
  align = 'split',
}) => {
  if (align === 'right') {
    return (
      <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        <GrokToolBtn onClick={onCancel}>取消</GrokToolBtn>
        {children}
      </div>
    )
  }
  return (
    <div style={{ marginTop: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <div style={{ display: 'flex', gap: 8 }}>
        <GrokToolBtn onClick={onCancel}>取消</GrokToolBtn>
        {onBack && <GrokToolBtn onClick={onBack}>{backLabel}</GrokToolBtn>}
      </div>
      {children && <div style={{ display: 'flex', gap: 8 }}>{children}</div>}
    </div>
  )
}

const DEFAULT_CHAT = `workflow: my-workflow
priority: 50
description: 聊天触发示例

match:
  require_any:
    - 关键词
  require_any_secondary:
    - 变更

response_template: |
  [OK] 已启动 Workflow
  - 流程 ID: {run_id}
  - 工单: {ticket_id}
  - 步骤: {workflow_description}
`

const WorkflowWizard: React.FC<WorkflowWizardProps> = ({ open, onClose, initial, onSaved, startStep = 0 }) => {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [step, setStep] = useState(0)
  const [form] = Form.useForm()
  const [workflowYaml, setWorkflowYaml] = useState('')
  const [chatYaml, setChatYaml] = useState(DEFAULT_CHAT)
  const [workflowDsl, setWorkflowDsl] = useState<WorkflowDSL | null>(null)
  const [previewPath, setPreviewPath] = useState('')
  const [yamlTab, setYamlTab] = useState('WORKFLOW.yaml')
  const [validation, setValidation] = useState<{ valid: boolean; errors: string[]; warnings: string[] } | null>(null)
  const [testRunId, setTestRunId] = useState<string | null>(null)
  const [published, setPublished] = useState(false)
  const [workflowGraph, setWorkflowGraph] = useState<WorkflowGraph>(createEmptyGraph())
  const [chainMode, setChainMode] = useState<'canvas' | 'list'>('canvas')
  const [saveMode, setSaveMode] = useState<'draft' | 'review' | 'publish'>('draft')
  const [changeSummary, setChangeSummary] = useState('')
  const [webhookConfig, setWebhookConfig] = useState<ItsmWebhookDSL>({
    enabled: false,
    route_key: '',
    accepted_message: '已受理，正在处理',
  })
  const [webhookYaml, setWebhookYaml] = useState('')
  const [canvasEpoch, setCanvasEpoch] = useState(0)

  const isEditMode = !!initial?.initialDsl
  const watchedPluginName = Form.useWatch('plugin_name', form) as string | undefined

  const { data: skills = [] } = useQuery('skills', skillApi.list, { enabled: open })
  const { data: templates = [] } = useQuery('workflow-plugins', workflowApi.listPlugins, { enabled: open })
  const { data: categories = ['itsm', 'custom'] } = useQuery('workflow-categories', workflowApi.listCategories, {
    enabled: open,
  })

  const categoryOptions = useMemo(
    () => categories.map((c) => ({ value: c, label: c })),
    [categories],
  )

  const initialRef = useRef(initial)
  initialRef.current = initial

  const resetWizard = useCallback(() => {
    const boot = initialRef.current
    setValidation(null)
    setTestRunId(null)
    setPreviewPath('')
    setYamlTab('WORKFLOW.yaml')
    setWebhookYaml('')
    setPublished(!!boot?.workflowYaml)
    setCanvasEpoch((n) => n + 1)

    if (boot?.initialDsl) {
      setWorkflowDsl(boot.initialDsl)
      setWorkflowGraph(dslToGraph(boot.initialDsl))
      setChainMode('canvas')
      setWorkflowYaml(boot.workflowYaml || '')
      setChatYaml(
        syncChatIntentWorkflow(boot.chatIntentYaml || DEFAULT_CHAT, boot.initialDsl.meta.name),
      )
      const wh = boot.initialDsl.triggers?.webhook
      setWebhookConfig(
        wh
          ? { enabled: true, route_key: wh.route_key || '', accepted_message: wh.accepted_message, legacy_paths: wh.legacy_paths, context_mapping: wh.context_mapping }
          : { enabled: false, route_key: '', accepted_message: '已受理，正在处理', legacy_paths: [], context_mapping: {} },
      )
      form.setFieldsValue({
        plugin_name: boot.initialDsl.meta.name,
        category: boot.initialDsl.meta.category || 'itsm',
        description: boot.initialDsl.meta.description || '自定义 Workflow',
        step1_skill: boot.initialDsl.steps[0]?.skill,
        step2_skill: boot.initialDsl.steps[1]?.skill,
      })
      return
    }

    if (boot?.workflowYaml) {
      const pluginName = boot.pluginName || parseWorkflowName(boot.workflowYaml) || 'my-workflow'
      const wf = normalizeWorkflowYaml(boot.workflowYaml, pluginName)
      setWorkflowYaml(wf)
      setChatYaml(syncChatIntentWorkflow(boot.chatIntentYaml || DEFAULT_CHAT, pluginName))
      setWorkflowDsl(null)
      form.setFieldsValue({
        plugin_name: pluginName,
        category: boot.category || 'itsm',
        description: boot.description || '自定义 Workflow',
        step1_skill: boot.step1_skill,
        step2_skill: boot.step2_skill,
      })
    } else {
      setWorkflowYaml('')
      setChatYaml(DEFAULT_CHAT)
      setWorkflowDsl(null)
      setWorkflowGraph(createEmptyGraph())
      setChainMode('canvas')
      form.resetFields()
      form.setFieldsValue({
        category: boot?.category || 'itsm',
        description: boot?.description || '自定义 Workflow',
        plugin_name: boot?.pluginName,
        step1_skill: boot?.step1_skill,
        step2_skill: boot?.step2_skill,
      })
    }
  }, [form])

  const wizardBootKeyRef = useRef<string | null>(null)

  useEffect(() => {
    if (!open) {
      wizardBootKeyRef.current = null
      return
    }
    const boot = initialRef.current
    const bootKey = [
      startStep,
      boot?.initialDsl?.meta?.name,
      boot?.pluginName,
      boot?.templateId,
      boot?.workflowYaml ? 'yaml' : '',
    ].join('|')
    if (wizardBootKeyRef.current === bootKey) return
    wizardBootKeyRef.current = bootKey
    resetWizard()
    setStep(startStep)
  }, [open, startStep, initial, resetWizard])

  const handleFinish = useCallback(() => {
    if (!published) {
      Modal.confirm({
        title: '尚未保存',
        content: '您还未点击「生成并保存」，确定要关闭向导吗？未保存的修改将丢失。',
        okText: '仍要关闭',
        cancelText: '继续编辑',
        onOk: onClose,
      })
      return
    }
    onClose()
  }, [published, onClose])

  useEffect(() => {
    if (!open) return undefined
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        handleFinish()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, handleFinish])

  const handleGraphChange = useCallback((next: WorkflowGraph) => {
    setWorkflowGraph((prev) => {
      if (JSON.stringify(prev) === JSON.stringify(next)) return prev
      return next
    })
  }, [])

  const getFormPluginName = (): string =>
    (watchedPluginName || (form.getFieldValue('plugin_name') as string) || '').trim()

  const getResolvedPluginName = (): string => {
    const fromForm = getFormPluginName()
    return workflowDsl?.meta.name || parseWorkflowName(workflowYaml) || fromForm || 'my-workflow'
  }

  const applyPreviewFiles = (files: Record<string, string>) => {
    if (files['WORKFLOW.yaml']) setWorkflowYaml(files['WORKFLOW.yaml'])
    if (files['CHAT.intent.yaml']) setChatYaml(files['CHAT.intent.yaml'])
    if (files['ITSM.webhook.yaml']) setWebhookYaml(files['ITSM.webhook.yaml'])
    else if (!webhookConfig.enabled) setWebhookYaml('')
  }

  const ensurePluginName = async (): Promise<string | false> => {
    let name = getFormPluginName()
    if (!name) {
      try {
        const values = await form.validateFields(['plugin_name'])
        name = (values.plugin_name as string)?.trim()
      } catch {
        message.warning('请先填写插件名（目录名），见顶部提示或返回「基础信息」')
        return false
      }
    }
    if (!/^[a-z0-9-]+$/.test(name)) {
      message.warning('插件名仅支持小写字母、数字、连字符')
      setStep(0)
      return false
    }
    return name
  }

  const applyTriggersToDsl = (dsl: WorkflowDSL): WorkflowDSL => {
    let next = dsl
    if (chatYaml.trim()) {
      next = mergeChatYamlIntoDsl(next, chatYaml)
    }
    if (webhookConfig.enabled && webhookConfig.route_key) {
      next = {
        ...next,
        triggers: {
          ...next.triggers,
          webhook: { ...webhookConfig, enabled: true },
        },
      }
    }
    return next
  }

  const buildCurrentDsl = (): WorkflowDSL | null => {
    if (workflowDsl) {
      return applyTriggersToDsl(workflowDsl)
    }
    const values = form.getFieldsValue() as WizardFormValues
    if (!values.plugin_name || !values.step1_skill) return null
    return applyTriggersToDsl(buildDslFromWizard(values))
  }

  const previewMutation = useMutation(
    async (dsl: WorkflowDSL) => {
      return workflowApi.preview({ dsl, options: { persist: false, auto_map_inputs: true } })
    },
    {
      onSuccess: (res) => {
        setValidation(res.validation)
        setPreviewPath(res.plugin_path)
        applyPreviewFiles(res.files)
        if (!res.validation.valid) {
          message.warning(res.validation.errors.join('；') || '预览校验有错误')
        }
      },
      onError: (err: unknown) => {
        message.error(err instanceof Error ? err.message : '预览生成失败')
      },
    },
  )

  const applySkillChainPreview = async (): Promise<boolean> => {
    const pluginName = await ensurePluginName()
    if (!pluginName) return false

    const values = { ...(form.getFieldsValue() as WizardFormValues), plugin_name: pluginName }

    if (chainMode === 'canvas') {
      const templateDsl = buildDslFromWizard({
        ...values,
        step1_skill: values.step1_skill || FIREWALL_POLICY_ACTION,
      })
      const { dsl, errors } = graphToWorkflowDsl(workflowGraph, {
        meta: templateDsl.meta,
        triggers: templateDsl.triggers,
        on_complete: templateDsl.on_complete,
      })
      if (!dsl || dsl.steps.length === 0) {
        message.error(errors[0] || '画布至少需要一个 Skill 节点')
        return false
      }
      if (errors.length > 0) {
        message.warning(errors.join('；'))
      }
      setWorkflowDsl(dsl)
      return true
    }

    if (!values.step1_skill) {
      message.warning('请选择 Step 1 Skill')
      return false
    }
    const dsl = buildDslFromWizard(values)
    setWorkflowDsl(dsl)
    return true
  }

  const runPreviewFromDsl = async (dsl: WorkflowDSL): Promise<boolean> => {
    try {
      const res = await previewMutation.mutateAsync(dsl)
      return res.validation.valid
    } catch {
      return false
    }
  }

  const validateMutation = useMutation(
    async () => {
      const dsl = buildCurrentDsl()
      if (dsl) {
        const res = await workflowApi.preview({ dsl, options: { persist: false, auto_map_inputs: false } })
        return {
          validation: res.validation,
          files: res.files,
          plugin_path: res.plugin_path,
        }
      }
      const pluginName = getResolvedPluginName()
      const wf = normalizeWorkflowYaml(workflowYaml, pluginName)
      const chat = syncChatIntentWorkflow(chatYaml, pluginName)
      const validation = await workflowApi.validate({ workflow_yaml: wf, chat_intent_yaml: chat })
      return { validation, files: null, plugin_path: null }
    },
    {
      onSuccess: ({ validation: res, files, plugin_path }) => {
        if (files) {
          applyPreviewFiles(files)
          if (plugin_path) setPreviewPath(plugin_path)
        }
        setValidation(res)
        if (res.valid) message.success('校验通过')
        else message.error(res.errors.join('；') || '校验未通过')
      },
      onError: (err: unknown) => {
        message.error(err instanceof Error ? err.message : '校验请求失败')
      },
    },
  )

  const saveMutation = useMutation(
    async () => {
      const values = (await form.validateFields(['plugin_name', 'category'])) as WizardFormValues
      let dsl = buildCurrentDsl()
      if (!dsl) {
        dsl = applyTriggersToDsl(
          buildDslFromWizard({ ...values, step1_skill: values.step1_skill || FIREWALL_POLICY_ACTION }),
        )
      }

      const result = await workflowApi.generate({
        dsl,
        options: {
          persist: true,
          overwrite: true,
          reload: true,
          auto_map_inputs: true,
          submit_review: saveMode === 'review',
          publish: saveMode === 'publish' && isAdmin,
          change_summary: changeSummary || undefined,
        },
      })

      if (!result.success) {
        setValidation(result.validation)
        throw new Error(result.validation?.errors?.join('；') || result.message || '生成失败')
      }

      if (result.files['WORKFLOW.yaml']) setWorkflowYaml(result.files['WORKFLOW.yaml'])
      if (result.files['CHAT.intent.yaml']) setChatYaml(result.files['CHAT.intent.yaml'])
      if (result.files['ITSM.webhook.yaml']) setWebhookYaml(result.files['ITSM.webhook.yaml'])
      setPreviewPath(result.plugin_path)
    },
    {
      onSuccess: () => {
        const msg =
          saveMode === 'publish' && isAdmin
            ? 'Workflow 已生成并发布'
            : saveMode === 'review'
              ? 'Workflow 已保存并提交审核'
              : 'Workflow 已生成并保存为草稿'
        message.success(msg)
        setPublished(true)
        queryClient.invalidateQueries('workflow-plugins')
        queryClient.invalidateQueries('workflow-templates')
        onSaved?.()
      },
      onError: (err: unknown) => {
        message.error(err instanceof Error ? err.message : '保存失败')
      },
    },
  )

  const testRunMutation = useMutation(
    async () => {
      const templateName = getResolvedPluginName()
      const exists = templates.some((t) => t.name === templateName)
      if (!published && !exists) {
        throw new Error('请先「生成并保存」，或从已有模板复制后再试跑')
      }
      const values = form.getFieldsValue()
      const ctx = { ticket_id: values.test_ticket_id || 'REQ2025001' }
      return workflowApi.testRun({ template_name: templateName, context: ctx })
    },
    {
      onSuccess: (res) => {
        setTestRunId(res.run_id)
        message.success('试跑已启动')
      },
      onError: (err: unknown) => {
        message.error(err instanceof Error ? err.message : '试跑失败（需 admin 权限且 Celery 运行中）')
      },
    }
  )

  const handleGenerateFromTemplate = async (templateName: string) => {
    try {
      const detail = await workflowApi.getTemplate(templateName)
      const copyName = `${templateName}-copy`
      const wf = normalizeWorkflowYaml(detail.files['WORKFLOW.yaml'] || '', copyName)
      setWorkflowYaml(wf)
      setChatYaml(syncChatIntentWorkflow(detail.files['CHAT.intent.yaml'] || DEFAULT_CHAT, copyName))
      form.setFieldsValue({ plugin_name: copyName })
      setWorkflowDsl(null)
      message.success(`已复制模板 ${templateName}`)
    } catch {
      message.error('加载模板失败')
    }
  }

  const goNextFromStep0 = async () => {
    try {
      await form.validateFields(['plugin_name', 'category'])
      setStep(1)
    } catch {
      message.warning('请填写必填项')
    }
  }

  const goNextFromStep1 = async () => {
    const ok = await applySkillChainPreview()
    if (ok) setStep(2)
  }

  const goNextFromStep2 = () => {
    if (!workflowDsl) {
      message.warning('请先配置 Skill 链')
      return
    }
    setStep(3)
  }

  const goNextFromStep3 = async () => {
    setChatYaml((prev) => syncChatIntentWorkflow(prev, getResolvedPluginName()))
    const dsl = buildCurrentDsl()
    if (!dsl) {
      message.warning('请先配置 Skill 链')
      return
    }
    const ok = await runPreviewFromDsl(dsl)
    if (ok) setStep(4)
  }

  const goNextFromStep4 = async () => {
    if (!workflowYaml.trim()) {
      message.warning('WORKFLOW.yaml 不能为空，请返回上一步重新生成')
      return
    }
    validateMutation.mutate(undefined, {
      onSuccess: ({ validation: res }) => {
        if (res.valid) setStep(5)
      },
    })
  }

  const handleChainModeChange = (mode: 'canvas' | 'list') => {
    if (mode === 'list' && chainMode === 'canvas') {
      const { steps } = graphToDslSteps(workflowGraph)
      const skills = steps.map((s) => s.skill).filter(Boolean)
      form.setFieldsValue({
        step1_skill: skills[0],
        step2_skill: skills[1],
      })
    }
    setChainMode(mode)
  }

  const refreshPreview = () => {
    const dsl = buildCurrentDsl()
    if (dsl) previewMutation.mutate(dsl)
    else message.warning('请先配置 Skill 链')
  }

  const excludeTemplateName = useMemo(
    () => (watchedPluginName || '').trim() || undefined,
    [watchedPluginName],
  )

  const previewFiles: Record<string, string> = useMemo(() => {
    const files: Record<string, string> = {
      'WORKFLOW.yaml': workflowYaml,
      'CHAT.intent.yaml': chatYaml,
    }
    if (webhookYaml.trim() || webhookConfig.enabled) {
      files['ITSM.webhook.yaml'] = webhookYaml
    }
    return files
  }, [workflowYaml, chatYaml, webhookYaml, webhookConfig.enabled])

  const panelClassName = [
    'grok-wizard-panel',
    step === 1 && chainMode === 'canvas' ? 'grok-wizard-panel-canvas' : '',
    step === 2 ? 'grok-wizard-panel-config' : '',
    step === 4 ? 'grok-wizard-panel-yaml' : '',
  ]
    .filter(Boolean)
    .join(' ')

  if (!open) return null

  return (
    <div className={panelClassName}>
      <Steps
        current={step}
        size="small"
        style={{ marginBottom: 24 }}
        items={[
          { title: '基础信息' },
          { title: '流程编排' },
          { title: '配置参数' },
          { title: '触发器' },
          { title: '预览 YAML' },
          { title: '生成发布' },
        ]}
      />

      <Form form={form} layout="vertical" preserve>
        <WorkflowWizardMetaBar
          step={step}
          pluginName={watchedPluginName}
          isEditMode={isEditMode}
          onGoBasic={() => setStep(0)}
        />
        <div style={{ display: step === 0 ? 'block' : 'none' }}>
          <Form.Item
            name="plugin_name"
            label="插件名（目录名）"
            extra="唯一标识，用于插件目录与 Workflow 模板名，仅小写字母、数字、连字符"
            rules={[{ required: true, pattern: /^[a-z0-9-]+$/, message: '小写字母、数字、连字符' }]}
          >
            <Input
              placeholder="itsm-my-workflow"
              disabled={isEditMode}
              onBlur={() => {
                const n = form.getFieldValue('plugin_name')
                if (n) setChatYaml((prev) => syncChatIntentWorkflow(prev, n))
              }}
            />
          </Form.Item>
        </div>
        {step === 0 && (
          <>
            <Form.Item name="category" label="分类目录" rules={[{ required: true }]}>
              <Select options={categoryOptions} />
            </Form.Item>
            <Form.Item name="description" label="描述">
              <Input placeholder="Workflow 说明" />
            </Form.Item>
            <Form.Item label="从已有模板复制">
              <Select
                placeholder="选择模板…"
                allowClear
                options={templates.map((t) => ({ value: t.name, label: `${t.name} — ${t.description}` }))}
                onChange={(v) => v && handleGenerateFromTemplate(v)}
              />
            </Form.Item>
            <Text type="secondary">后端 WorkflowGenerator 将根据 DSL 自动生成标准插件文件。</Text>
            <WizardNav onCancel={handleFinish} align="right">
              <GrokToolBtn primary onClick={goNextFromStep0}>下一步</GrokToolBtn>
            </WizardNav>
          </>
        )}

        {step === 1 && (
          <>
            <Tabs
              activeKey={chainMode}
              onChange={(k) => handleChainModeChange(k as 'canvas' | 'list')}
              size="small"
              style={{ marginBottom: 12 }}
              items={[
                { key: 'canvas', label: '可视化画布' },
                { key: 'list', label: '列表模式' },
              ]}
            />
            {chainMode === 'canvas' ? (
              <div className="wf-wizard-canvas-wrap">
                <WorkflowCanvas
                  graph={workflowGraph}
                  onChange={handleGraphChange}
                  skills={skills}
                  remountKey={canvasEpoch}
                  excludeTemplateName={excludeTemplateName}
                />
              </div>
            ) : (
              <>
                <Text type="secondary">列表模式：快速配置线性 Skill 链。</Text>
                <Form.Item label="Step 1 Skill" name="step1_skill" rules={[{ required: true, message: '请选择 Step 1 Skill' }]}>
                  <Select
                    showSearch
                    placeholder="选择 Skill"
                    options={skills.map((s) => ({ value: s.name, label: s.name }))}
                  />
                </Form.Item>
                <Form.Item label="Step 2 Skill" name="step2_skill">
                  <Select
                    showSearch
                    allowClear
                    placeholder="可选"
                    options={skills.map((s) => ({ value: s.name, label: s.name }))}
                  />
                </Form.Item>
              </>
            )}
            <Alert
              type="info"
              showIcon
              message="编排说明"
              description="画布支持顺序连接与条件分支（when 配置在连线上）。引擎按顺序执行，when 为 false 的步骤跳过。"
              style={{ marginBottom: 16, marginTop: 12 }}
            />
            <WizardNav onCancel={handleFinish} onBack={() => setStep(0)}>
              <GrokToolBtn primary onClick={goNextFromStep1}>
                下一步：配置参数
              </GrokToolBtn>
            </WizardNav>
          </>
        )}

        {step === 2 && workflowDsl && (
          <>
            <SkillStepConfigPanel
              dsl={workflowDsl}
              onChange={(next) => setWorkflowDsl(next)}
            />
            <WizardNav onCancel={handleFinish} onBack={() => setStep(1)}>
              <GrokToolBtn primary onClick={goNextFromStep2}>
                下一步：配置触发器
              </GrokToolBtn>
            </WizardNav>
          </>
        )}

        {step === 2 && !workflowDsl && (
          <Alert
            type="warning"
            message="请先完成 Skill 链配置"
            action={<GrokToolBtn onClick={() => setStep(1)}>返回 Skill 链</GrokToolBtn>}
          />
        )}

        {step === 3 && (
          <>
            <WorkflowTriggerStep
              chatYaml={chatYaml}
              onChatYamlChange={setChatYaml}
              webhook={webhookConfig}
              onWebhookChange={setWebhookConfig}
              workflowName={getResolvedPluginName()}
            />
            <WizardNav onCancel={handleFinish} onBack={() => setStep(2)}>
              <GrokToolBtn primary loading={previewMutation.isLoading} onClick={goNextFromStep3}>
                生成 YAML 预览
              </GrokToolBtn>
            </WizardNav>
          </>
        )}

        {step === 4 && (
          <>
            <div className="wf-wizard-yaml-preview" style={{ display: 'flex', gap: 16, minHeight: 360 }}>
              <div style={{ flex: 1 }}>
                <Text strong>即将生成的插件路径</Text>
                <div style={{ margin: '8px 0', fontFamily: 'monospace', fontSize: 12 }}>{previewPath || '—'}</div>
                <Tabs
                  activeKey={yamlTab}
                  onChange={setYamlTab}
                  size="small"
                  items={Object.keys(previewFiles).map((key) => ({
                    key,
                    label: key,
                    children: (
                      <TextArea
                        value={previewFiles[key]}
                        onChange={(e) => {
                          if (key === 'WORKFLOW.yaml') setWorkflowYaml(e.target.value)
                          else if (key === 'CHAT.intent.yaml') setChatYaml(e.target.value)
                          else if (key === 'ITSM.webhook.yaml') setWebhookYaml(e.target.value)
                        }}
                        rows={16}
                        style={{ fontFamily: 'monospace', fontSize: 12 }}
                      />
                    ),
                  }))}
                />
              </div>
            </div>
            {validation && (
              <div style={{ marginTop: 8 }}>
                {validation.errors.map((e) => (
                  <Alert key={e} type="error" message={e} style={{ marginBottom: 4 }} />
                ))}
                {validation.warnings.map((w) => (
                  <Alert key={w} type="warning" message={w} style={{ marginBottom: 4 }} />
                ))}
              </div>
            )}
            <WizardNav onCancel={handleFinish} onBack={() => setStep(3)}>
              <GrokToolBtn disabled={previewMutation.isLoading} onClick={refreshPreview}>
                重新预览
              </GrokToolBtn>
              <GrokToolBtn disabled={validateMutation.isLoading} onClick={() => validateMutation.mutate()}>
                {validateMutation.isLoading ? '校验中…' : '校验 YAML'}
              </GrokToolBtn>
              <GrokToolBtn primary disabled={validateMutation.isLoading} onClick={goNextFromStep4}>
                下一步
              </GrokToolBtn>
            </WizardNav>
            <WorkflowDryRunPanel buildDsl={buildCurrentDsl} />
          </>
        )}

        {step === 5 && (
          <>
            {previewPath && (
              <Alert
                type="success"
                showIcon
                message="生成目标"
                description={<code>{previewPath}</code>}
                style={{ marginBottom: 16 }}
              />
            )}
            <Form.Item label="保存方式">
              <Radio.Group value={saveMode} onChange={(e) => setSaveMode(e.target.value)}>
                <Radio value="draft">保存为草稿</Radio>
                <Radio value="review">保存并提交审核</Radio>
                {isAdmin && <Radio value="publish">保存并立即发布</Radio>}
              </Radio.Group>
            </Form.Item>
            {(saveMode === 'publish' || saveMode === 'review') && (
              <Form.Item label="变更说明">
                <Input
                  placeholder="可选：版本/审核说明"
                  value={changeSummary}
                  onChange={(e) => setChangeSummary(e.target.value)}
                />
              </Form.Item>
            )}
            <Form.Item name="test_ticket_id" label="试跑工单号" initialValue="REQ2025001">
              <Input placeholder="REQ2025001" />
            </Form.Item>
            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
              <GrokToolBtn primary disabled={saveMutation.isLoading} onClick={() => saveMutation.mutate()}>
                {saveMutation.isLoading ? '生成中…' : '生成并保存'}
              </GrokToolBtn>
              <GrokToolBtn disabled={testRunMutation.isLoading || saveMutation.isLoading} onClick={() => testRunMutation.mutate()}>
                {testRunMutation.isLoading ? '试跑中…' : '试跑 Workflow'}
              </GrokToolBtn>
            </div>
            {testRunId && (
              <WorkflowRunMonitor runId={testRunId} embedded onClose={() => setTestRunId(null)} />
            )}
            <WorkflowDryRunPanel buildDsl={buildCurrentDsl} />
            <WizardNav onCancel={handleFinish} onBack={() => setStep(4)}>
              <GrokToolBtn primary onClick={handleFinish}>完成</GrokToolBtn>
            </WizardNav>
          </>
        )}
      </Form>
    </div>
  )
}

export default WorkflowWizard
