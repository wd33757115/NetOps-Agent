import React from 'react'
import { Button, Space, Tag } from 'antd'

interface Place { node_id: string; name: string; address?: string | null; collected: boolean; evidence_id?: string; observed_at?: string }
interface Port extends Place { port: string; tp_id: string; basis: 'mac' | 'arp'; vlan?: string | null }
interface RouterInterface extends Place { interface: string; interface_ip: string; subnet: string; vrf: string }
interface Location { status: string; mac?: string; ips?: string[]; host_node?: string; link_id?: string; ports?: Port[]; inferred?: boolean; evidence?: string[]; arp?: { device: string; ip: string; interface: string }[]; device?: Place; interface?: string; vrf?: string }
export interface LocateResult { query: string; kind: 'ip' | 'mac'; status: string; router_interfaces: RouterInterface[]; locations: Location[] }

export const statusLabels: Record<string, [string, string]> = {
  confirmed: ['已定位观测端口，物理直连未确认', 'green'],
  shared_segment: ['共享二层关系，物理结构未确认', 'gold'],
  behind_uncollected: ['经过未采集的设备，位置未确定', 'orange'],
  not_found: ['未找到', 'default'],
  device: ['设备自身地址', 'blue'],
  conflict: ['地址对应多个身份或作用域', 'red'],
}

/** Canvas ids to highlight: the host, its attachment ports' devices and a shared segment. */
export function highlightIds(result?: LocateResult) {
  const ids = new Set<string>()
  for (const location of result?.locations || []) {
    if (location.host_node) ids.add(location.host_node)
    if (location.device) ids.add(location.device.node_id)
    for (const port of location.ports || []) ids.add(port.node_id)
    if (location.link_id && (location.ports?.length || 0) > 1) ids.add(location.link_id)
  }
  return ids
}

const when = (value?: string) => value ? new Date(value).toLocaleString() : '未知'
const place = (item: Place) => `${item.name}${item.address ? ` · ${item.address}` : ''}${item.collected ? '' : '（未采集）'}`

export default function Located({ result, onEvidence, onClear }: { result: LocateResult; onEvidence: (id?: string) => void; onClear: () => void }) {
  const [label, color] = statusLabels[result.status] || [result.status, 'default']
  return <div className="topology-located">
    <Space style={{ display: 'flex', justifyContent: 'space-between' }}><Tag color={color}>{label}</Tag><Button size="small" type="link" onClick={onClear}>清除</Button></Space>
    <dl>
      <dt>查询</dt><dd>{result.query}</dd>
      {result.router_interfaces.map(gateway => <React.Fragment key={gateway.node_id + gateway.interface}>
        <dt>网段接口</dt><dd>{place(gateway)} {gateway.interface} {gateway.interface_ip}（{gateway.subnet} · NI {gateway.vrf}）</dd>
      </React.Fragment>)}
    </dl>
    {result.locations.map((location, index) => <section key={index}>
      {result.locations.length > 1 && <h4>{location.mac ? `MAC ${location.mac}` : `候选 ${index + 1}`}</h4>}
      <dl>
        {location.device && <><dt>设备</dt><dd>{place(location.device)}</dd><dt>接口</dt><dd>{location.interface}{location.vrf ? ` · NI ${location.vrf}` : ''}</dd></>}
        {location.mac && result.locations.length === 1 && <><dt>MAC</dt><dd>{location.mac}</dd></>}
        {!!location.ips?.length && <><dt>IP</dt><dd>{location.ips.join(', ')}</dd></>}
        {location.ports?.map(port => <React.Fragment key={port.node_id + port.port}>
          <dt>接入位置</dt>
          <dd>{place(port)} {port.port}{port.vlan ? ` · VLAN ${port.vlan}` : ''}<br />
            <small>{port.basis === 'mac' ? '依据 MAC 表' : '依据 ARP 推断'} · 观测于 {when(port.observed_at)}</small>
            {port.evidence_id && <Button size="small" type="link" onClick={() => onEvidence(port.evidence_id)}>原始证据</Button>}</dd>
        </React.Fragment>)}
        {location.status === 'not_found' && !!location.arp?.length && <><dt>ARP 记录</dt><dd>{location.arp.map(row => `${row.device} ${row.interface}`).join('；')}（未找到接入端口，可能已老化）</dd></>}
        {!!location.evidence?.length && <><dt>依据</dt><dd>{location.evidence.map(line => <div key={line}>{line}</div>)}</dd></>}
      </dl>
    </section>)}
    {result.status === 'not_found' && !result.locations.length && <p>当前快照的 ARP 和 MAC 表中没有它。终端可能不在发现范围内，或记录已老化；让终端产生流量后重新发现再查。</p>}
  </div>
}
