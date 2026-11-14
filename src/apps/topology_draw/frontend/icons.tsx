import React from 'react'

// 华为产品图标风格：浅色器件底、华为红描边、白色器件符号。
const BODY = '#f7f8fa'
const LINE = '#c7000b'
const FACE = '#ffffff'

const Frame = ({ children, width = 96, height = 72 }: { children: React.ReactNode; width?: number; height?: number }) => (
  <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img">
    {children}
  </svg>
)

const RouterIcon = () => (
  <Frame>
    <rect x="6" y="14" width="84" height="44" rx="6" fill={BODY} stroke={LINE} strokeWidth="3" />
    <rect x="14" y="22" width="68" height="28" rx="4" fill={FACE} stroke={LINE} strokeWidth="2" />
    {[34, 46].map(y => (
      <g key={y} stroke={LINE} strokeWidth="3" fill="none">
        <line x1="24" y1={y} x2="52" y2={y} />
        <path d={`M24 ${y - 5} L24 ${y + 5} M52 ${y - 5} L52 ${y + 5}`} />
        <path d={`M46 ${y - 5} L52 ${y} L46 ${y + 5}`} fill={LINE} />
        <path d={`M30 ${y - 5} L24 ${y} L30 ${y + 5}`} fill={LINE} />
      </g>
    ))}
  </Frame>
)

const SwitchIcon = () => (
  <Frame width={110} height={72}>
    <rect x="4" y="20" width="102" height="32" rx="6" fill={BODY} stroke={LINE} strokeWidth="3" />
    <rect x="12" y="26" width="86" height="20" rx="4" fill={FACE} stroke={LINE} strokeWidth="2" />
    <g stroke={LINE} strokeWidth="3" fill="none">
      <line x1="24" y1="36" x2="52" y2="36" />
      <path d="M24 31 L24 41 M52 31 L52 41" />
      <path d="M46 31 L52 36 L46 41" fill={LINE} />
      <path d="M30 31 L24 36 L30 41" fill={LINE} />
      <line x1="58" y1="36" x2="86" y2="36" />
      <path d="M58 31 L58 41 M86 31 L86 41" />
      <path d="M80 31 L86 36 L80 41" fill={LINE} />
      <path d="M64 31 L58 36 L64 41" fill={LINE} />
    </g>
  </Frame>
)

const FirewallIcon = () => (
  <Frame>
    <rect x="8" y="14" width="80" height="44" rx="4" fill={BODY} stroke={LINE} strokeWidth="3" />
    {[22, 34, 46].map(y => (
      <line key={y} x1="12" y1={y} x2="84" y2={y} stroke={LINE} strokeWidth="2.5" />
    ))}
    {[36, 60].map(x => (
      <g key={x} stroke={LINE} strokeWidth="2.5">
        <line x1={x} y1="14" x2={x} y2="22" />
        <line x1={x + 12} y1="22" x2={x + 12} y2="34" />
        <line x1={x} y1="34" x2={x} y2="46" />
        <line x1={x + 12} y1="46" x2={x + 12} y2="58" />
      </g>
    ))}
  </Frame>
)

const ServerIcon = () => (
  <Frame>
    <rect x="30" y="10" width="36" height="52" rx="4" fill={BODY} stroke={LINE} strokeWidth="3" />
    {[18, 30, 42].map(y => (
      <g key={y}>
        <rect x="35" y={y} width="26" height="7" rx="2" fill={FACE} stroke={LINE} strokeWidth="2" />
        <circle cx="56" cy={y + 3.5} r="2" fill={LINE} />
      </g>
    ))}
  </Frame>
)

const PcIcon = () => (
  <Frame>
    <rect x="22" y="14" width="52" height="34" rx="4" fill={BODY} stroke={LINE} strokeWidth="3" />
    <rect x="28" y="20" width="40" height="22" rx="2" fill={FACE} stroke={LINE} strokeWidth="2" />
    <line x1="48" y1="48" x2="48" y2="56" stroke={LINE} strokeWidth="3" />
    <line x1="34" y1="58" x2="62" y2="58" stroke={LINE} strokeWidth="4" strokeLinecap="round" />
  </Frame>
)

const CloudIcon = () => (
  <Frame>
    <path
      d="M28 54 a14 14 0 0 1 -2 -27 a18 18 0 0 1 34 -8 a14 14 0 0 1 12 24 a12 12 0 0 1 -4 11 z"
      fill={BODY}
      stroke={LINE}
      strokeWidth="3"
    />
  </Frame>
)

const GenericIcon = () => (
  <Frame>
    <rect x="8" y="14" width="80" height="44" rx="6" fill={BODY} stroke={LINE} strokeWidth="3" />
    <circle cx="48" cy="36" r="9" fill="none" stroke={LINE} strokeWidth="2.5" />
    <circle cx="48" cy="36" r="3" fill={LINE} />
    {[0, 60, 120, 180, 240, 300].map(angle => (
      <line
        key={angle}
        x1={48 + 12 * Math.cos((angle * Math.PI) / 180)}
        y1={36 + 12 * Math.sin((angle * Math.PI) / 180)}
        x2={48 + 16 * Math.cos((angle * Math.PI) / 180)}
        y2={36 + 16 * Math.sin((angle * Math.PI) / 180)}
        stroke={LINE}
        strokeWidth="2.5"
      />
    ))}
  </Frame>
)

export type Kind = 'router' | 'switch' | 'firewall' | 'server' | 'pc' | 'cloud' | 'generic'

export function DeviceIcon({ kind }: { kind: Kind }) {
  switch (kind) {
    case 'router':
      return <RouterIcon />
    case 'switch':
      return <SwitchIcon />
    case 'firewall':
      return <FirewallIcon />
    case 'server':
      return <ServerIcon />
    case 'pc':
      return <PcIcon />
    case 'cloud':
      return <CloudIcon />
    default:
      return <GenericIcon />
  }
}
