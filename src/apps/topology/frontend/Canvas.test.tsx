import React from 'react'
import { MemoryRouter } from 'react-router-dom'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import Page, { Canvas } from './Page'

const mocks = vi.hoisted(() => ({ fitView: vi.fn(), update: vi.fn(), api: { get: vi.fn() }, nodes: [] as { id: string; width?: number; height?: number; measured?: { width: number; height: number }; handles?: { id: string }[]; data: { ports: { id: string }[] } }[] }))
vi.mock('@netops/config/api', () => ({ api: mocks.api }))
vi.mock('@netops/context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'operator' } }) }))
vi.mock('@netops/platform/terminal/DeviceTerminal', () => ({ default: () => null, readHostKey: vi.fn(), acceptHostKey: vi.fn() }))
// React Flow's browser measurement/zoom is covered by the live canvas audit.
// This adapter exercises our custom nodes/edges and actual selection callbacks.
vi.mock('@xyflow/react', () => ({
  Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
  MarkerType: { ArrowClosed: 'arrowclosed' },
  useReactFlow: () => ({ fitView: mocks.fitView }),
  useUpdateNodeInternals: () => mocks.update,
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Handle: ({ id }: { id: string }) => <span data-handle-id={id} />,
  Background: () => null,
  Controls: () => null,
  BaseEdge: ({ id, path, style, label }: { id: string; path: string; style: React.CSSProperties; label?: string }) => <g><path data-testid={`path-${id}`} d={path} style={style} /><text data-testid={`label-${id}`}>{label}</text></g>,
  ReactFlow: (props: { nodes: { id: string; type: string; ariaLabel: string; data: object }[]; edges: { id: string; type: string; ariaLabel: string }[]; nodeTypes: Record<string, React.ComponentType>; edgeTypes: Record<string, React.ComponentType>; children: React.ReactNode; onNodeClick: (...args: unknown[]) => void; onNodeMouseEnter: (...args: unknown[]) => void; onNodeMouseLeave: () => void; onEdgeClick: (...args: unknown[]) => void; onEdgeMouseEnter: (...args: unknown[]) => void; onEdgeMouseLeave: () => void }) => { mocks.nodes = props.nodes as typeof mocks.nodes; return <div>
    {props.nodes.map(node => { const View = props.nodeTypes[node.type]; return <button key={node.id} data-testid={`node-${node.id}`} aria-label={node.ariaLabel} onClick={event => props.onNodeClick(event, node)} onMouseEnter={event => props.onNodeMouseEnter(event, node)} onMouseLeave={props.onNodeMouseLeave}><View {...node} /></button> })}
    {props.edges.map(edge => { const View = props.edgeTypes[edge.type]; return <button key={edge.id} data-testid={`edge-${edge.id}`} aria-label={edge.ariaLabel} onClick={event => props.onEdgeClick(event, edge)} onMouseEnter={event => props.onEdgeMouseEnter(event, edge)} onMouseLeave={props.onEdgeMouseLeave}><svg><View {...edge} /></svg></button> })}
    {props.children}
  </div> },
}))

const nodes = ['a', 'b', 'c', 'd'].map(id => ({ id, name: id, identity_status: 'collected', freshness: 'observed', tps: Array.from({ length: 4 }, (_, index) => ({ id: `tp-${index}`, name: `Ethernet0/${index}` })) }))
const edge = (id: string, source: string, target: string, port: number) => ({ id, source, target, source_tp: `tp-${port}`, target_tp: `tp-${port + 1}`, freshness: 'observed', label: `${id} 两端端口`, link_type: 'p2p', direction: 'bi', evidence_id: `obs-${id}` })
const graph = { nodes, links: [edge('one', 'a', 'b', 0), edge('two', 'a', 'b', 1), edge('cross', 'a', 'c', 2), edge('other', 'c', 'd', 0)], model_profile: 'simap' }
const props = () => ({ graph, highlight: new Set<string>(), onNode: vi.fn(), onLink: vi.fn() })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks() })

it('renders and selects each parallel link independently with its original evidence object', () => {
  const handlers = props()
  render(<Canvas {...handlers} />)
  expect(screen.getByTestId('path-one').getAttribute('d')).not.toBe(screen.getByTestId('path-two').getAttribute('d'))
  fireEvent.click(screen.getByTestId('edge-one'))
  fireEvent.click(screen.getByTestId('edge-two'))
  expect(handlers.onLink.mock.calls[0][0]).toBe(graph.links[0])
  expect(handlers.onLink.mock.calls[1][0]).toBe(graph.links[1])
})

it('highlights just the hovered edge, then all neighbors when a node is focused', () => {
  const handlers = props(), view = render(<Canvas {...handlers} />)
  fireEvent.mouseEnter(screen.getByTestId('edge-two'))
  expect(screen.getByTestId('path-two')).toHaveStyle({ strokeWidth: '3', opacity: '1' })
  expect(screen.getByTestId('path-one')).toHaveStyle({ opacity: '0.12' })
  expect(screen.getByTestId('label-two')).toHaveTextContent('two 两端端口')
  expect(screen.getByTestId('label-one')).toBeEmptyDOMElement()
  fireEvent.mouseLeave(screen.getByTestId('edge-two'))
  fireEvent.mouseEnter(screen.getByTestId('node-a'))
  for (const id of ['one', 'two', 'cross']) expect(screen.getByTestId(`path-${id}`)).toHaveStyle({ strokeWidth: '3', opacity: '1' })
  expect(screen.getByTestId('path-other')).toHaveStyle({ opacity: '0.12' })
  for (const id of ['one', 'two', 'cross']) expect(screen.getByTestId(`label-${id}`)).toBeEmptyDOMElement()
  fireEvent.click(screen.getByTestId('node-a'))
  expect(handlers.onNode).toHaveBeenCalledWith(graph.nodes[0])
  fireEvent.mouseLeave(screen.getByTestId('node-a'))
  view.rerender(<Canvas {...handlers} selectedId="a" />)
  expect(screen.getByTestId('path-cross')).toHaveStyle({ strokeWidth: '3' })
})

it('keeps user pan/zoom through polling but fits a changed relationship', () => {
  vi.useFakeTimers()
  const handlers = props(), view = render(<Canvas {...handlers} />)
  act(() => { vi.advanceTimersByTime(100) })
  expect(mocks.fitView).toHaveBeenCalledTimes(1)
  view.rerender(<Canvas {...handlers} graph={structuredClone(graph)} />)
  act(() => { vi.advanceTimersByTime(100) })
  expect(mocks.fitView).toHaveBeenCalledTimes(1)
  view.rerender(<Canvas {...handlers} graph={{ ...graph, links: graph.links.map(link => link.id === 'cross' ? { ...link, target: 'd' } : link) }} />)
  act(() => { vi.advanceTimersByTime(100) })
  expect(mocks.fitView).toHaveBeenCalledTimes(2)
})

it('announces unrenderable relationships and keeps their evidence selectable', () => {
  const handlers = props(), missing = edge('missing', 'a', 'absent', 0)
  render(<Canvas {...handlers} graph={{ ...graph, links: [...graph.links, missing] }} />)
  expect(screen.getByRole('status')).toHaveTextContent('有 1 条关系暂未绘入画布')
  fireEvent.click(screen.getByRole('button', { name: 'missing 两端端口' }))
  expect(handlers.onLink).toHaveBeenCalledWith(missing)
})

it('lists each selected node port relationship and opens the chosen link evidence', async () => {
  const observed = { id: 'run', status: 'completed', network_id: 'net', graph, targets: [], created_at: '2026-10-03T00:00:00Z' }
  mocks.api.get.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/default-login') ? { configured: true } : url.endsWith('/networks') ? [{ id: 'net', name: 'test' }] : url.endsWith('/runs') ? [observed] : url.includes('/evidence/') ? { observation: url } : observed }))
  render(<MemoryRouter><Page /></MemoryRouter>)
  fireEvent.click(await screen.findByTestId('node-a'))
  expect(screen.getByText('相邻链路')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Ethernet0/1 → b Ethernet0/2' }))
  fireEvent.click(screen.getByRole('button', { name: '查看原始证据' }))
  await waitFor(() => expect(mocks.api.get).toHaveBeenCalledWith('/apps/topology/evidence/obs-two'))
})

it('gives every node static size and handles so hover re-renders cannot drop its edges', () => {
  // React Flow 12 resets measured handle bounds for a new node object without them and
  // hides its edges until re-measured; under the pointer that loops as a flicker.
  render(<Canvas {...props()} />)
  fireEvent.mouseEnter(screen.getByTestId('node-a'))
  fireEvent.mouseLeave(screen.getByTestId('node-a'))
  fireEvent.mouseEnter(screen.getByTestId('edge-cross'))
  for (const node of mocks.nodes) {
    expect(node.measured).toEqual({ width: node.width, height: node.height })
    expect(node.handles?.map(handle => handle.id).sort()).toEqual(node.data.ports.map(port => port.id).sort())
  }
  expect(mocks.nodes.flatMap(node => node.handles ?? [])).toHaveLength(8)
})
