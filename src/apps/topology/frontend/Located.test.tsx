import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import Located, { highlightIds, type LocateResult } from './Located'

afterEach(cleanup)

const port = (name: string, address: string | null, collected = true) => ({ node_id: `dev:${name}`, name, address, collected, port: 'GigabitEthernet1/0', tp_id: 'tp', basis: 'arp' as const, vlan: null, evidence_id: collected ? `obs-${name}` : undefined, observed_at: '2026-09-28T08:32:00+00:00' })
const confirmed: LocateResult = {
  query: '192.168.1.2', kind: 'ip', status: 'confirmed',
  gateways: [{ node_id: 'dev:vsr-1', name: 'vsr-1', address: '10.10.1.1', collected: true, interface: 'Vlan-interface200', gateway_ip: '192.168.1.1', subnet: '192.168.1.0/24', vrf: 'default' }],
  locations: [{ status: 'confirmed', mac: '00:50:79:66:68:01', ips: ['192.168.1.2'], host_node: 'host:pc1', link_id: 'l2:1', inferred: false, evidence: ['10.10.1.1 mac 00:50:79:66:68:01 vlan 200 GigabitEthernet3/0'],
    ports: [{ ...port('vsr-1', '10.10.1.1'), port: 'GigabitEthernet3/0', basis: 'mac', vlan: '200' }] }],
}

it('shows the access port, VLAN, gateway and the basis of the answer', () => {
  const evidence = vi.fn()
  render(<Located result={confirmed} onEvidence={evidence} onClear={vi.fn()} />)
  expect(screen.getByText('已确认')).toBeInTheDocument()
  expect(screen.getByText(/vsr-1 · 10\.10\.1\.1 GigabitEthernet3\/0 · VLAN 200/)).toBeInTheDocument()
  expect(screen.getByText(/依据 MAC 表/)).toBeInTheDocument()
  expect(screen.getByText(/Vlan-interface200 192\.168\.1\.1/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /原始证据/ }))
  expect(evidence).toHaveBeenCalledWith('obs-vsr-1')
})

it('marks ARP inference and uncollected devices instead of claiming a confirmed port', () => {
  const result: LocateResult = { query: '10.10.1.254', kind: 'ip', status: 'behind_uncollected', gateways: [],
    locations: [{ status: 'behind_uncollected', mac: '00:15:5d:01:03:14', host_node: 'host:pc', link_id: 'l2:hub', ports: [port('vsr-2', '10.10.1.2'), port('vsr-1', null, false)] }] }
  render(<Located result={result} onEvidence={vi.fn()} onClear={vi.fn()} />)
  expect(screen.getByText('经过未采集的设备，位置未确定')).toBeInTheDocument()
  expect(screen.getAllByText(/依据 ARP 推断/)).toHaveLength(2)
  expect(screen.getByText(/vsr-1（未采集）/)).toBeInTheDocument()
  expect(highlightIds(result)).toEqual(new Set(['host:pc', 'dev:vsr-2', 'dev:vsr-1', 'l2:hub']))
})

it('a single access port does not highlight a segment node', () => {
  expect(highlightIds(confirmed)).toEqual(new Set(['host:pc1', 'dev:vsr-1']))
  expect(highlightIds(undefined).size).toBe(0)
})

it('lists every MAC of a conflicting IP and explains an empty answer', () => {
  const conflict: LocateResult = { query: '10.0.0.9', kind: 'ip', status: 'conflict', gateways: [],
    locations: ['00:00:00:00:00:09', '00:00:00:00:00:0a'].map(mac => ({ status: 'confirmed', mac, ports: [port('s', 's')] })) }
  render(<Located result={conflict} onEvidence={vi.fn()} onClear={vi.fn()} />)
  expect(screen.getByText('同一 IP 对应多个 MAC')).toBeInTheDocument()
  expect(screen.getByText('MAC 00:00:00:00:00:0a')).toBeInTheDocument()
  cleanup()
  render(<Located result={{ query: '10.0.0.77', kind: 'ip', status: 'not_found', gateways: [], locations: [] }} onEvidence={vi.fn()} onClear={vi.fn()} />)
  expect(screen.getByText(/没有它/)).toBeInTheDocument()
})
