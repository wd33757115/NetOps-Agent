// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useCallback, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Panel,
  addEdge,
  useNodesState,
  useEdgesState,
  ReactFlowProvider,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Alert, Input, Select, Typography, message } from 'antd'
import type { SkillItem } from '../../services/api'
import {
  type WorkflowGraph,
  appendSkillNode,
  appendSubworkflowNode,
  graphToDslSteps,
  insertConditionNode,
  nextNodeId,
  START_ID,
} from '../../types/workflowGraph'
import { builderNodeTypes } from './BuilderNodes'
import SkillPalette from './SkillPalette'
import { GrokToolBtn } from '../ui/GrokUi'
import { workflowApi } from '../../services/api'
import { useQuery } from 'react-query'

const { Text } = Typography

interface WorkflowCanvasProps {
  graph: WorkflowGraph
  onChange: (graph: WorkflowGraph) => void
  skills: SkillItem[]
  /** 外部重置画布时递增，避免 props↔state 双向同步死循环 */
  remountKey?: number
  /** 禁止引用自身，避免子流程无限递归 */
  excludeTemplateName?: string
}

function toFlowNodes(graph: WorkflowGraph): Node[] {
  return graph.nodes.map((n) => ({
    id: n.id,
    type: n.type,
    position: n.position,
    data: { ...n.data },
    draggable: n.type !== 'start' && n.type !== 'end',
    deletable: n.type === 'skill' || n.type === 'condition' || n.type === 'subworkflow',
  }))
}

function toFlowEdges(graph: WorkflowGraph): Edge[] {
  return graph.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.data?.when ? (e.data.label || 'when') : undefined,
    data: { ...e.data },
    animated: !!e.data?.when,
    style: e.data?.when ? { stroke: '#fa8c16' } : undefined,
  }))
}

function fromFlow(stateNodes: Node[], stateEdges: Edge[]): WorkflowGraph {
  return {
    nodes: stateNodes.map((n) => ({
      id: n.id,
      type: n.type as WorkflowGraph['nodes'][0]['type'],
      position: n.position,
      data: n.data as WorkflowGraph['nodes'][0]['data'],
    })),
    edges: stateEdges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: e.data?.when ? 'conditional' : 'default',
      data: e.data as WorkflowGraph['edges'][0]['data'],
    })),
  }
}

interface WorkflowCanvasInnerProps {
  graph: WorkflowGraph
  onChange: (graph: WorkflowGraph) => void
  skills: SkillItem[]
  excludeTemplateName?: string
}

function graphSignature(graph: WorkflowGraph): string {
  return JSON.stringify(graph)
}

const WorkflowCanvasInner: React.FC<WorkflowCanvasInnerProps> = ({
  graph,
  onChange,
  skills,
  excludeTemplateName,
}) => {
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const lastEmittedRef = useRef(graphSignature(graph))

  const { data: workflowTemplates = [] } = useQuery('workflow-templates-canvas', workflowApi.listTemplates, {
    staleTime: 60_000,
  })
  const availableTemplates = useMemo(
    () => workflowTemplates.filter((t) => t.name !== excludeTemplateName),
    [workflowTemplates, excludeTemplateName],
  )
  const [nodes, setNodes, onNodesChange] = useNodesState(toFlowNodes(graph))
  const [edges, setEdges, onEdgesChange] = useEdgesState(toFlowEdges(graph))
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)

  const notifyChange = useCallback((next: WorkflowGraph) => {
    const sig = graphSignature(next)
    if (sig === lastEmittedRef.current) return
    lastEmittedRef.current = sig
    onChangeRef.current(next)
  }, [])

  const applyGraph = useCallback(
    (next: WorkflowGraph) => {
      setNodes(toFlowNodes(next))
      setEdges(toFlowEdges(next))
      notifyChange(next)
    },
    [notifyChange, setNodes, setEdges],
  )

  const emitChange = useCallback(
    (n: Node[], e: Edge[]) => {
      notifyChange(fromFlow(n, e))
    },
    [notifyChange],
  )

  const onConnect = useCallback(
    (conn: Connection) => {
      if (!conn.source || !conn.target) return
      const targetNode = nodes.find((n) => n.id === conn.target)
      const sourceNode = nodes.find((n) => n.id === conn.source)
      if (targetNode?.type === 'start') {
        message.warning('不能连接到开始节点')
        return
      }
      if (sourceNode?.type === 'end') {
        message.warning('结束节点不能连出')
        return
      }
      const newEdge: Edge = {
        ...conn,
        id: nextNodeId('e'),
        data:
          sourceNode?.type === 'condition'
            ? { when: "${context.priority} == 'high'", label: '分支' }
            : {},
      }
      setEdges((eds) => {
        const next = addEdge(newEdge, eds)
        emitChange(nodes, next)
        return next
      })
    },
    [nodes, setEdges, emitChange],
  )

  const onNodeDragStop = useCallback(
    (_: React.MouseEvent, node: Node) => {
      setNodes((nds) => {
        const next = nds.map((n) => (n.id === node.id ? { ...n, position: node.position } : n))
        emitChange(next, edges)
        return next
      })
    },
    [edges, emitChange, setNodes],
  )

  const conversion = useMemo(() => graphToDslSteps(fromFlow(nodes, edges)), [nodes, edges])

  const selectedNode = nodes.find((n) => n.id === selectedNodeId)
  const selectedEdge = edges.find((e) => e.id === selectedEdgeId)

  const updateNodeData = (nodeId: string, patch: Record<string, unknown>) => {
    setNodes((nds) => {
      const next = nds.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n,
      )
      emitChange(next, edges)
      return next
    })
  }

  const updateEdgeData = (edgeId: string, patch: Record<string, unknown>) => {
    setEdges((eds) => {
      const next = eds.map((e) =>
        e.id === edgeId
          ? {
              ...e,
              data: { ...e.data, ...patch },
              animated: !!(patch.when ?? e.data?.when),
              label: (patch.label as string) || (patch.when ? 'when' : e.label),
            }
          : e,
      )
      emitChange(nodes, next)
      return next
    })
  }

  const handleAddSkill = (skillName: string) => {
    const next = appendSkillNode(fromFlow(nodes, edges), skillName)
    applyGraph(next)
    message.success(`已添加 ${skillName}`)
  }

  const handleAddSubworkflow = (templateName: string) => {
    if (templateName === excludeTemplateName) {
      message.warning('不能引用当前正在编辑的 Workflow 作为子流程')
      return
    }
    const next = appendSubworkflowNode(fromFlow(nodes, edges), templateName)
    applyGraph(next)
    message.success(`已添加子流程 ${templateName}`)
  }

  const handleAddCondition = () => {
    const skillNodes = nodes.filter((n) => n.type === 'skill')
    const afterId = skillNodes.length > 0 ? skillNodes[skillNodes.length - 1].id : START_ID
    const next = insertConditionNode(fromFlow(nodes, edges), afterId)
    applyGraph(next)
  }

  const handleDeleteSelected = () => {
    if (!selectedNodeId) return
    const n = nodes.find((x) => x.id === selectedNodeId)
    if (n?.type === 'start' || n?.type === 'end') return
    const nextNodes = nodes.filter((x) => x.id !== selectedNodeId)
    const nextEdges = edges.filter((e) => e.source !== selectedNodeId && e.target !== selectedNodeId)
    setNodes(nextNodes)
    setEdges(nextEdges)
    emitChange(nextNodes, nextEdges)
    setSelectedNodeId(null)
  }

  return (
    <div className="wf-canvas-layout">
      <SkillPalette
        skills={skills}
        onAddSkill={handleAddSkill}
        onAddCondition={handleAddCondition}
        workflowTemplates={availableTemplates.map((t) => t.name)}
        onAddSubworkflow={handleAddSubworkflow}
      />
      <div className="wf-canvas-main">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeDragStop={onNodeDragStop}
          nodeTypes={builderNodeTypes}
          fitView={false}
          onNodeClick={(_, node) => {
            setSelectedNodeId(node.id)
            setSelectedEdgeId(null)
          }}
          onEdgeClick={(_, edge) => {
            setSelectedEdgeId(edge.id)
            setSelectedNodeId(null)
          }}
          onPaneClick={() => {
            setSelectedNodeId(null)
            setSelectedEdgeId(null)
          }}
          deleteKeyCode={['Backspace', 'Delete']}
          onNodesDelete={(deleted) => {
            const ids = new Set(deleted.map((d) => d.id))
            const nextNodes = nodes.filter((n) => !ids.has(n.id))
            const nextEdges = edges.filter((e) => !ids.has(e.source) && !ids.has(e.target))
            setNodes(nextNodes)
            setEdges(nextEdges)
            emitChange(nextNodes, nextEdges)
          }}
        >
          <Background gap={16} size={1} color="#e8e8e8" />
          <Controls showInteractive={false} />
          <MiniMap zoomable pannable nodeStrokeWidth={2} />
          <Panel position="top-right" className="wf-canvas-panel">
            <Text type="secondary">{conversion.steps.length} 步</Text>
            {conversion.errors.length > 0 && (
              <Alert type="warning" message={conversion.errors[0]} style={{ marginTop: 8, maxWidth: 260 }} />
            )}
          </Panel>
        </ReactFlow>
      </div>
      <div className="wf-canvas-inspector">
        <Text strong>属性</Text>
        {!selectedNode && !selectedEdge && (
          <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
            选择节点或连线以编辑 when / Skill
          </Text>
        )}
        {selectedNode && selectedNode.type === 'subworkflow' && (
          <div className="wf-inspector-form">
            <label>子 Workflow 模板</label>
            <Select
              showSearch
              value={selectedNode.data.subworkflow as string}
              options={availableTemplates.map((t) => ({ value: t.name, label: t.name }))}
              onChange={(v) => {
                if (v === excludeTemplateName) {
                  message.warning('不能引用当前正在编辑的 Workflow 作为子流程')
                  return
                }
                updateNodeData(selectedNode.id, {
                  subworkflow: v,
                  label: `子流程: ${v}`,
                })
              }}
              style={{ width: '100%' }}
            />
            <label>步骤名</label>
            <Input
              value={selectedNode.data.stepName as string}
              onChange={(e) => updateNodeData(selectedNode.id, { stepName: e.target.value })}
            />
            <label>展示标签</label>
            <Input
              value={selectedNode.data.label as string}
              onChange={(e) => updateNodeData(selectedNode.id, { label: e.target.value })}
            />
            <label>when（可选）</label>
            <Input
              value={selectedNode.data.when as string}
              onChange={(e) => updateNodeData(selectedNode.id, { when: e.target.value })}
            />
            <GrokToolBtn className="wf-danger-btn" onClick={handleDeleteSelected} style={{ marginTop: 8 }}>
              删除节点
            </GrokToolBtn>
          </div>
        )}
        {selectedNode && selectedNode.type === 'skill' && (
          <div className="wf-inspector-form">
            <label>Skill</label>
            <Select
              showSearch
              value={selectedNode.data.skill as string}
              options={skills.map((s) => ({ value: s.name, label: s.name }))}
              onChange={(v) => updateNodeData(selectedNode.id, { skill: v, label: v })}
              style={{ width: '100%' }}
            />
            <label>步骤名 (WORKFLOW name)</label>
            <Input
              value={selectedNode.data.stepName as string}
              onChange={(e) => updateNodeData(selectedNode.id, { stepName: e.target.value })}
            />
            <label>展示标签</label>
            <Input
              value={selectedNode.data.label as string}
              onChange={(e) => updateNodeData(selectedNode.id, { label: e.target.value })}
            />
            <label>when（可选）</label>
            <Input
              value={selectedNode.data.when as string}
              placeholder="${context.priority} == 'high'"
              onChange={(e) => updateNodeData(selectedNode.id, { when: e.target.value })}
            />
            <label>并行组 ID（同组并发执行）</label>
            <Input
              value={(selectedNode.data.parallelGroup as string) || ''}
              placeholder="batch-1"
              onChange={(e) => updateNodeData(selectedNode.id, { parallelGroup: e.target.value || undefined })}
            />
            <GrokToolBtn className="wf-danger-btn" onClick={handleDeleteSelected} style={{ marginTop: 8 }}>
              删除节点
            </GrokToolBtn>
          </div>
        )}
        {selectedEdge && (
          <div className="wf-inspector-form">
            <label>分支 when 表达式</label>
            <Input.TextArea
              rows={3}
              value={(selectedEdge.data?.when as string) || ''}
              placeholder="${context.priority} == 'high'"
              onChange={(e) => updateEdgeData(selectedEdge.id, { when: e.target.value })}
            />
            <label>标签</label>
            <Input
              value={(selectedEdge.data?.label as string) || ''}
              onChange={(e) => updateEdgeData(selectedEdge.id, { label: e.target.value })}
            />
          </div>
        )}
      </div>
    </div>
  )
}

const WorkflowCanvas: React.FC<WorkflowCanvasProps> = ({
  graph,
  onChange,
  skills,
  remountKey = 0,
  excludeTemplateName,
}) => (
  <ReactFlowProvider key={remountKey}>
    <WorkflowCanvasInner
      graph={graph}
      onChange={onChange}
      skills={skills}
      excludeTemplateName={excludeTemplateName}
    />
  </ReactFlowProvider>
)

export default WorkflowCanvas
