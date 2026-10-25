// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useCallback, useMemo } from 'react'
import {
  Alert,
  Badge,
  Collapse,
  Input,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'
import { useQueries, useQuery } from 'react-query'
import { skillApi, workflowApi } from '../../services/api'
import { GrokToolBtn } from '../ui/GrokUi'
import type { SkillSchema, WorkflowDSL, WorkflowStepDSL } from '../../types/workflowDsl'
import { buildExpressionPresets } from '../../types/workflowCollab'

const { Text } = Typography

export interface MappingSuggestion {
  step_name: string
  skill: string
  suggested_inputs: Record<string, string>
  upstream_step?: string | null
  available_expressions?: Array<{ label: string; expr: string }>
}

interface SkillStepConfigPanelProps {
  dsl: WorkflowDSL
  onChange: (dsl: WorkflowDSL) => void
}

interface StepInputRow {
  key: string
  param: string
  type: string
  required: boolean
  description: string
  suggested?: string
  value: string
}

function updateStepInputs(
  dsl: WorkflowDSL,
  stepId: string,
  param: string,
  value: string,
): WorkflowDSL {
  return {
    ...dsl,
    steps: dsl.steps.map((s) =>
      s.id === stepId
        ? {
            ...s,
            inputs: {
              ...(s.inputs as Record<string, string>),
              [param]: value,
            },
          }
        : s,
    ),
  }
}

function applySuggestionsToDsl(
  dsl: WorkflowDSL,
  suggestions: MappingSuggestion[],
  mergeOnlyEmpty: boolean,
): WorkflowDSL {
  return {
    ...dsl,
    steps: dsl.steps.map((step) => {
      const sug = suggestions.find((s) => s.step_name === step.name)
      if (!sug?.suggested_inputs) return step
      const current = (step.inputs || {}) as Record<string, string>
      const merged = mergeOnlyEmpty
        ? { ...sug.suggested_inputs, ...current }
        : { ...current, ...sug.suggested_inputs }
      return { ...step, inputs: merged }
    }),
  }
}

const StepInputTable: React.FC<{
  step: WorkflowStepDSL
  schema?: SkillSchema
  suggestion?: MappingSuggestion
  onInputChange: (param: string, value: string) => void
}> = ({ step, schema, suggestion, onInputChange }) => {
  const presets = useMemo(() => {
    if (suggestion?.available_expressions?.length) {
      return suggestion.available_expressions.map((e) => ({ label: e.label, value: e.expr }))
    }
    return buildExpressionPresets(suggestion?.upstream_step)
  }, [suggestion])

  const rows: StepInputRow[] = useMemo(() => {
    const schemaInputs = schema?.inputs ?? []
    const allParamNames = new Set<string>([
      ...schemaInputs.map((i) => i.name),
      ...Object.keys(suggestion?.suggested_inputs ?? {}),
      ...Object.keys((step.inputs as Record<string, string>) ?? {}),
    ])

    return Array.from(allParamNames).map((param) => {
      const spec = schemaInputs.find((i) => i.name === param)
      const current = ((step.inputs as Record<string, string>) ?? {})[param] ?? ''
      return {
        key: param,
        param,
        type: spec?.type ?? 'string',
        required: spec?.required ?? false,
        description: spec?.description ?? '',
        suggested: suggestion?.suggested_inputs?.[param],
        value: current,
      }
    })
  }, [schema, step.inputs, suggestion])

  const columns = [
    {
      title: '参数',
      dataIndex: 'param',
      width: 140,
      render: (name: string, row: StepInputRow) => (
        <Space size={4}>
          <Text code>{name}</Text>
          {row.required && <Tag color="red" style={{ margin: 0 }}>必填</Tag>}
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'type',
      width: 72,
      render: (t: string) => <Text type="secondary">{t}</Text>,
    },
    {
      title: '建议映射',
      dataIndex: 'suggested',
      width: 220,
      render: (suggested: string | undefined, row: StepInputRow) =>
        suggested ? (
          <Tooltip title="点击应用到当前值">
            <Text
              code
              style={{ fontSize: 11, cursor: 'pointer' }}
              onClick={() => onInputChange(row.param, suggested)}
            >
              {suggested.length > 36 ? `${suggested.slice(0, 36)}…` : suggested}
            </Text>
          </Tooltip>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: '当前值',
      dataIndex: 'value',
      render: (_: string, row: StepInputRow) => (
        <Space.Compact style={{ width: '100%' }}>
          <Input
            size="small"
            style={{ flex: 1 }}
            value={row.value}
            placeholder={row.suggested || '固定值或 ${...} 表达式'}
            onChange={(e) => onInputChange(row.param, e.target.value)}
          />
          <Select
            size="small"
            placeholder="表达式"
            style={{ width: 108 }}
            popupMatchSelectWidth={300}
            options={presets.map((p) => ({ value: p.value, label: p.label }))}
            onChange={(v) => onInputChange(row.param, v)}
            value={null}
          />
        </Space.Compact>
      ),
    },
  ]

  if (!schema && rows.length === 0) {
    return <Alert type="warning" message="无法加载 Skill schema，请检查 Skill 是否已注册" />
  }

  return (
    <>
      {schema && (
        <div className="wf-step-schema-meta">
          <Text type="secondary">{schema.description}</Text>
          {schema.entry_output && (
            <Tag style={{ marginLeft: 8 }}>输出: {schema.entry_output}</Tag>
          )}
        </div>
      )}
      <Table
        size="small"
        pagination={false}
        dataSource={rows}
        columns={columns}
        scroll={{ x: 720 }}
        locale={{ emptyText: '该 Skill 未声明 input 参数' }}
      />
    </>
  )
}

const SkillStepConfigPanel: React.FC<SkillStepConfigPanelProps> = ({ dsl, onChange }) => {
  const skillNames = useMemo(() => [...new Set(dsl.steps.map((s) => s.skill))], [dsl.steps])

  const { data: mappingData, isLoading: mappingLoading, refetch: refetchMappings } = useQuery(
    ['wf-infer-mappings', dsl.meta.name, skillNames.join(',')],
    () => workflowApi.inferMappings(dsl),
    { enabled: dsl.steps.length > 0, staleTime: 30_000 },
  )

  const schemaQueries = useQueries(
    skillNames.map((name) => ({
      queryKey: ['skill-schema', name],
      queryFn: () => skillApi.getSchema(name),
      staleTime: 60_000,
    })),
  )

  const schemaMap = useMemo(() => {
    const map: Record<string, SkillSchema> = {}
    skillNames.forEach((name, i) => {
      const data = schemaQueries[i]?.data
      if (data) map[name] = data
    })
    return map
  }, [skillNames, schemaQueries])

  const schemasLoading = schemaQueries.some((q) => q.isLoading)

  const handleApplyAll = useCallback(
    (mergeOnlyEmpty: boolean) => {
      if (!mappingData?.suggestions?.length) {
        message.info('暂无映射建议')
        return
      }
      onChange(applySuggestionsToDsl(dsl, mappingData.suggestions, mergeOnlyEmpty))
      message.success(mergeOnlyEmpty ? '已填充空白参数' : '已应用全部映射建议')
    },
    [dsl, mappingData, onChange],
  )

  const collapseItems = dsl.steps.map((step, idx) => {
    const suggestion = mappingData?.suggestions?.find((s) => s.step_name === step.name)
    const configuredCount = Object.keys((step.inputs as Record<string, string>) ?? {}).length
    const suggestedCount = Object.keys(suggestion?.suggested_inputs ?? {}).length

    return {
      key: step.id,
      label: (
        <Space>
          <Badge count={idx + 1} color="#1677ff" />
          <Text strong>{step.label || step.name}</Text>
          <Tag>{step.skill}</Tag>
          {configuredCount > 0 && (
            <Tag color="green">{configuredCount} 已配置</Tag>
          )}
          {suggestedCount > 0 && (
            <Tag color="blue">{suggestedCount} 建议</Tag>
          )}
        </Space>
      ),
      children: (
        <StepInputTable
          step={step}
          schema={schemaMap[step.skill]}
          suggestion={suggestion}
          onInputChange={(param, value) => onChange(updateStepInputs(dsl, step.id, param, value))}
        />
      ),
    }
  })

  return (
    <div className="wf-step-config-panel">
      <Alert
        type="info"
        showIcon
        message="Skill 参数配置"
        description={
          <>
            参数定义来自 <Text code>GET /skills/{'{name}'}/schema</Text>；
            建议映射来自 <Text code>POST /workflows/infer-mappings</Text>。
            支持 <Text code>${'${context.*}'}</Text> 与 <Text code>${'${steps.*}'}</Text> 表达式。
          </>
        }
        style={{ marginBottom: 16 }}
      />

      <div className="wf-step-config-toolbar">
        <GrokToolBtn
          icon={<ThunderboltOutlined />}
          loading={mappingLoading}
          onClick={() => refetchMappings()}
        >
          刷新映射建议
        </GrokToolBtn>
        <GrokToolBtn onClick={() => handleApplyAll(true)} disabled={mappingLoading}>
          填充空白参数
        </GrokToolBtn>
        <GrokToolBtn primary onClick={() => handleApplyAll(false)} disabled={mappingLoading}>
          一键应用全部建议
        </GrokToolBtn>
      </div>

      {(mappingLoading || schemasLoading) && (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin tip="加载 Skill schema 与映射建议…" />
        </div>
      )}

      {!mappingLoading && !schemasLoading && (
        <Collapse
          defaultActiveKey={dsl.steps.map((s) => s.id)}
          items={collapseItems}
          className="wf-step-config-collapse"
        />
      )}
    </div>
  )
}

export default SkillStepConfigPanel
