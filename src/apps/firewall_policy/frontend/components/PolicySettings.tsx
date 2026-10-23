import type { PolicySettingsValue } from '../services/firewallPolicy'

/** A policy's validity window (start, then end on its own line) and its long-connection state. */
export default function PolicySettings({ item }: { item: PolicySettingsValue }) {
  const [start, end] = (item.validity || '').split(' 至 ')
  const hours = item.long_link_hours
  return <div className="fw-address">
    <span>{!start ? '长期有效' : end ? `${start}\n至 ${end}` : start}</span>
    <small>{hours == null ? '未开启长连接' : hours === 0 ? '长连接永不老化' : `长连接 ${hours} 小时`}</small>
  </div>
}
