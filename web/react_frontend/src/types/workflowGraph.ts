// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Workflow 可视化画布图模型 ↔ WorkflowDSL 转换
 */
import type { WorkflowDSL, WorkflowStepDSL } from './workflowDsl'

export type WorkflowNodeType = 'start' | 'end' | 'skill' | 'condition' | 'subworkflow'

export interface WorkflowGraphNodeData {
  label?: string
  skill?: string
  subworkflow?: string
  stepName?: string
  when?: string
  parallelGroup?: string
  dependsOn?: string[]
  inputs?: Record<string, string>
  [key: string]: unknown
}

export interface WorkflowGraphNode {
  id: string
  type: WorkflowNodeType
  position: { x: number; y: number }
  data: WorkflowGraphNodeData
}

export interface WorkflowGraphEdge {
  id: string
  source: string
  target: string
  type?: 'default' | 'conditional'
  data?: {
    when?: string
    label?: string
  }
}

export interface WorkflowGraph {
  nodes: WorkflowGraphNode[]
  edges: WorkflowGraphEdge[]
}

export interface GraphConversionResult {
  steps: WorkflowStepDSL[]
  errors: string[]
}

const START_ID = 'wf-start'
const END_ID = 'wf-end'

let _seq = 0
export function nextNodeId(prefix: string): string {
  _seq += 1
  return `${prefix}-${Date.now()}-${_seq}`
}

export function createEmptyGraph(): WorkflowGraph {
  return {
    nodes: [
      { id: START_ID, type: 'start', position: { x: 220, y: 0 }, data: { label: '开始' } },
      { id: END_ID, type: 'end', position: { x: 220, y: 400 }, data: { label: '结束' } },
    ],
    edges: [],
  }
}

function deriveStepName(skill: string, index: number): string {
  const known: Record<string, string> = {
    'firewall-policy-generator': 'policy_generation',
    'itsm-change-ticket-writer': 'change_ticket',
  }
  if (known[skill]) return known[skill]
  return `step_${index + 1}`
}

function outEdges(edges: WorkflowGraphEdge[], nodeId: string): WorkflowGraphEdge[] {
  return edges.filter((e) => e.source === nodeId)
}

function inEdges(edges: WorkflowGraphEdge[], nodeId: string): WorkflowGraphEdge[] {
  return edges.filter((e) => e.target === nodeId)
}

/** 画布图 → 线性 WorkflowStepDSL（支持顺序 + when 分支） */
export function graphToDslSteps(graph: WorkflowGraph): GraphConversionResult {
  const errors: string[] = []
  const { nodes, edges } = graph
  const nodeById = new Map(nodes.map((n) => [n.id, n]))
  const start = nodes.find((n) => n.type === 'start')

  if (!start) {
    return { steps: [], errors: ['缺少开始节点'] }
  }

  const resolveWhen = (nodeId: string): string | undefined => {
    for (const e of inEdges(edges, nodeId)) {
      if (e.data?.when?.trim()) return e.data.when.trim()
    }
    const node = nodeById.get(nodeId)
    return node?.data.when?.trim() || undefined
  }

  const orderedSkills: WorkflowGraphNode[] = []
  const enqueued = new Set<string>()
  const addedSkills = new Set<string>()
  const queue: string[] = [start.id]

  while (queue.length > 0) {
    const currentId = queue.shift()!
    if (enqueued.has(currentId)) continue
    enqueued.add(currentId)

    const current = nodeById.get(currentId)
    if (!current || current.type === 'end') continue

    for (const edge of outEdges(edges, currentId)) {
      const target = nodeById.get(edge.target)
      if (!target) continue

      if ((target.type === 'skill' || target.type === 'subworkflow') && !addedSkills.has(target.id)) {
        addedSkills.add(target.id)
        const when = edge.data?.when?.trim() || target.data.when?.trim() || resolveWhen(target.id)
        orderedSkills.push({
          ...target,
          data: { ...target.data, when },
        })
      }

      if (target.type !== 'end' && !enqueued.has(target.id)) {
        queue.push(target.id)
      }
    }
  }

  if (orderedSkills.length === 0) {
    errors.push('请至少添加一个 Skill 节点并连接到开始节点')
  }

  const steps: WorkflowStepDSL[] = orderedSkills.map((node, idx) => {
    if (node.type === 'subworkflow') {
      const sub = node.data.subworkflow
      if (!sub) {
        errors.push(`子流程节点 ${node.data.label || node.id} 未选择 Workflow`)
      }
      return {
        id: node.id,
        name: node.data.stepName?.trim() || `subflow_${idx + 1}`,
        label: node.data.label || sub || `子流程 ${idx + 1}`,
        skill: '',
        subworkflow: sub || '',
        when: node.data.when,
        parallel_group: node.data.parallelGroup,
        depends_on: node.data.dependsOn || [],
        inputs: node.data.inputs || {},
      }
    }
    const skill = node.data.skill
    if (!skill) {
      errors.push(`节点 ${node.data.label || node.id} 未选择 Skill`)
    }
    return {
      id: node.id,
      name: node.data.stepName?.trim() || deriveStepName(skill || 'step', idx),
      label: node.data.label || skill || `步骤 ${idx + 1}`,
      skill: skill || '',
      when: node.data.when,
      parallel_group: node.data.parallelGroup,
      depends_on: node.data.dependsOn || [],
      inputs: node.data.inputs || {},
    }
  })

  const end = nodes.find((n) => n.type === 'end')
  if (end && orderedSkills.length > 0) {
    const lastSkill = orderedSkills[orderedSkills.length - 1]
    const reachesEnd = outEdges(edges, lastSkill.id).some((e) => e.target === END_ID)
    if (!reachesEnd) {
      const hasPathToEnd = enqueued.has(END_ID) || queue.includes(END_ID)
      if (!hasPathToEnd) {
        errors.push('请将最后一个 Skill 节点连接到「结束」节点')
      }
    }
  }

  return { steps, errors }
}

/** WorkflowDSL → 画布图（线性布局） */
export function dslToGraph(dsl: WorkflowDSL): WorkflowGraph {
  const graph = createEmptyGraph()
  const nodes: WorkflowGraphNode[] = [...graph.nodes]
  const edges: WorkflowGraphEdge[] = []

  let prevId = START_ID
  const x = 220
  dsl.steps.forEach((step, idx) => {
    const nodeId = step.id || nextNodeId('skill')
    const y = 80 + idx * 120
    const isSub = !!step.subworkflow
    nodes.push({
      id: nodeId,
      type: isSub ? 'subworkflow' : 'skill',
      position: { x, y },
      data: {
        label: step.label,
        skill: step.skill,
        subworkflow: step.subworkflow,
        stepName: step.name,
        when: step.when,
        parallelGroup: step.parallel_group,
        dependsOn: step.depends_on,
        inputs: (step.inputs as Record<string, string>) || {},
      },
    })
    edges.push({
      id: `e-${prevId}-${nodeId}`,
      source: prevId,
      target: nodeId,
      type: step.when ? 'conditional' : 'default',
      data: step.when ? { when: step.when, label: 'when' } : undefined,
    })
    prevId = nodeId
  })

  if (dsl.steps.length > 0) {
    edges.push({
      id: `e-${prevId}-${END_ID}`,
      source: prevId,
      target: END_ID,
    })
    const endNode = nodes.find((n) => n.id === END_ID)
    if (endNode) {
      endNode.position.y = 80 + dsl.steps.length * 120
    }
  }

  return { nodes, edges }
}

/** 合并 DSL meta/triggers 与画布 steps */
export function graphToWorkflowDsl(
  graph: WorkflowGraph,
  base: Pick<WorkflowDSL, 'meta' | 'triggers' | 'on_complete'>,
): { dsl: WorkflowDSL | null; errors: string[] } {
  const { steps, errors } = graphToDslSteps(graph)
  if (errors.length > 0 && steps.length === 0) {
    return { dsl: null, errors }
  }
  return {
    dsl: {
      ...base,
      steps,
    },
    errors,
  }
}

/** 在画布末尾追加 Skill 节点（自动连线） */
export function appendSkillNode(
  graph: WorkflowGraph,
  skill: string,
  label?: string,
): WorkflowGraph {
  const nodeId = nextNodeId('skill')
  const skillNodes = graph.nodes.filter((n) => n.type === 'skill')
  const y = 80 + skillNodes.length * 120

  const newNode: WorkflowGraphNode = {
    id: nodeId,
    type: 'skill',
    position: { x: 220, y },
    data: {
      skill,
      label: label || skill,
      stepName: deriveStepName(skill, skillNodes.length),
    },
  }

  let connectFrom = START_ID
  const skillOnly = graph.nodes.filter((n) => n.type === 'skill')
  if (skillOnly.length > 0) {
    connectFrom = skillOnly[skillOnly.length - 1].id
  } else {
    const startOut = graph.edges.find((e) => e.source === START_ID)
    if (startOut && graph.nodes.find((n) => n.id === startOut.target)?.type === 'condition') {
      connectFrom = startOut.target
    }
  }

  const newEdges = graph.edges.filter(
    (e) => !(e.source === connectFrom && graph.nodes.find((n) => n.id === e.target)?.type === END_ID),
  )

  newEdges.push({
    id: nextNodeId('e'),
    source: connectFrom,
    target: nodeId,
  })

  newEdges.push({
    id: nextNodeId('e'),
    source: nodeId,
    target: END_ID,
  })

  const nodes = graph.nodes.map((n) =>
    n.id === END_ID ? { ...n, position: { ...n.position, y: y + 120 } } : n,
  )
  nodes.push(newNode)

  return { nodes, edges: newEdges }
}

/** 在画布末尾追加子 Workflow 节点 */
export function appendSubworkflowNode(
  graph: WorkflowGraph,
  templateName: string,
  label?: string,
): WorkflowGraph {
  const nodeId = nextNodeId('subwf')
  const flowNodes = graph.nodes.filter((n) => n.type === 'skill' || n.type === 'subworkflow')
  const y = 80 + flowNodes.length * 120

  const newNode: WorkflowGraphNode = {
    id: nodeId,
    type: 'subworkflow',
    position: { x: 220, y },
    data: {
      subworkflow: templateName,
      label: label || `子流程: ${templateName}`,
      stepName: `subflow_${flowNodes.length + 1}`,
    },
  }

  let connectFrom = START_ID
  if (flowNodes.length > 0) {
    connectFrom = flowNodes[flowNodes.length - 1].id
  }

  const newEdges = graph.edges.filter(
    (e) => !(e.source === connectFrom && graph.nodes.find((n) => n.id === e.target)?.type === END_ID),
  )
  newEdges.push({ id: nextNodeId('e'), source: connectFrom, target: nodeId })
  newEdges.push({ id: nextNodeId('e'), source: nodeId, target: END_ID })

  const nodes = graph.nodes.map((n) =>
    n.id === END_ID ? { ...n, position: { ...n.position, y: y + 120 } } : n,
  )
  nodes.push(newNode)
  return { nodes, edges: newEdges }
}

/** 插入条件分支节点 */
export function insertConditionNode(graph: WorkflowGraph, afterNodeId: string): WorkflowGraph {
  const condId = nextNodeId('cond')
  const after = graph.nodes.find((n) => n.id === afterNodeId)
  if (!after) return graph

  const condNode: WorkflowGraphNode = {
    id: condId,
    type: 'condition',
    position: { x: after.position.x, y: after.position.y + 80 },
    data: { label: '条件分支' },
  }

  const out = graph.edges.filter((e) => e.source === afterNodeId)
  const newEdges = graph.edges.filter((e) => e.source !== afterNodeId)

  newEdges.push({
    id: nextNodeId('e'),
    source: afterNodeId,
    target: condId,
  })

  if (out.length === 0) {
    newEdges.push({
      id: nextNodeId('e'),
      source: condId,
      target: END_ID,
      type: 'conditional',
      data: { when: "${context.priority} == 'high'", label: '是' },
    })
  } else {
    out.forEach((e, i) => {
      newEdges.push({
        id: nextNodeId('e'),
        source: condId,
        target: e.target,
        type: 'conditional',
        data: {
          when: e.data?.when || (i === 0 ? "${context.priority} == 'high'" : "${context.priority} != 'high'"),
          label: i === 0 ? '是' : '否',
        },
      })
    })
  }

  const nodes = [...graph.nodes, condNode].map((n) =>
    n.position.y > after.position.y && n.id !== condId
      ? { ...n, position: { ...n.position, y: n.position.y + 60 } }
      : n,
  )

  return { nodes, edges: newEdges }
}

export { START_ID, END_ID }
