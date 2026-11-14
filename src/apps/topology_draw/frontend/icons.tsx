import React from 'react'

// 华为产品图标风格：浅色器件底、华为红描边、白色器件符号。
// SVG 以字符串为唯一来源：React 节点渲染和 .drawio 导出共用同一份，避免双份漂移。
export type Kind = 'router' | 'switch' | 'firewall' | 'server' | 'pc' | 'cloud' | 'generic'

const BODY = '#f7f8fa'
const LINE = '#c7000b'
const FACE = '#ffffff'

const frame = (inner: string, width = 96, height = 72) =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">${inner}</svg>`

const routerSvg = frame(
  `<rect x="6" y="14" width="84" height="44" rx="6" fill="${BODY}" stroke="${LINE}" stroke-width="3"/>` +
    `<rect x="14" y="22" width="68" height="28" rx="4" fill="${FACE}" stroke="${LINE}" stroke-width="2"/>` +
    [34, 46]
      .map(
        y =>
          `<g stroke="${LINE}" stroke-width="3" fill="none">` +
          `<line x1="24" y1="${y}" x2="52" y2="${y}"/>` +
          `<path d="M24 ${y - 5} L24 ${y + 5} M52 ${y - 5} L52 ${y + 5}"/>` +
          `<path d="M46 ${y - 5} L52 ${y} L46 ${y + 5}" fill="${LINE}"/>` +
          `<path d="M30 ${y - 5} L24 ${y} L30 ${y + 5}" fill="${LINE}"/></g>`,
      )
      .join(''),
)

const switchSvg = frame(
  `<rect x="4" y="20" width="102" height="32" rx="6" fill="${BODY}" stroke="${LINE}" stroke-width="3"/>` +
    `<rect x="12" y="26" width="86" height="20" rx="4" fill="${FACE}" stroke="${LINE}" stroke-width="2"/>` +
    `<g stroke="${LINE}" stroke-width="3" fill="none">` +
    `<line x1="24" y1="36" x2="52" y2="36"/><path d="M24 31 L24 41 M52 31 L52 41"/>` +
    `<path d="M46 31 L52 36 L46 41" fill="${LINE}"/><path d="M30 31 L24 36 L30 41" fill="${LINE}"/>` +
    `<line x1="58" y1="36" x2="86" y2="36"/><path d="M58 31 L58 41 M86 31 L86 41"/>` +
    `<path d="M80 31 L86 36 L80 41" fill="${LINE}"/><path d="M64 31 L58 36 L64 41" fill="${LINE}"/></g>`,
  110,
  72,
)

const firewallSvg = frame(
  `<rect x="8" y="14" width="80" height="44" rx="4" fill="${BODY}" stroke="${LINE}" stroke-width="3"/>` +
    [22, 34, 46].map(y => `<line x1="12" y1="${y}" x2="84" y2="${y}" stroke="${LINE}" stroke-width="2.5"/>`).join('') +
    [36, 60]
      .map(
        x =>
          `<g stroke="${LINE}" stroke-width="2.5">` +
          `<line x1="${x}" y1="14" x2="${x}" y2="22"/><line x1="${x + 12}" y1="22" x2="${x + 12}" y2="34"/>` +
          `<line x1="${x}" y1="34" x2="${x}" y2="46"/><line x1="${x + 12}" y1="46" x2="${x + 12}" y2="58"/></g>`,
      )
      .join(''),
)

const serverSvg = frame(
  `<rect x="30" y="10" width="36" height="52" rx="4" fill="${BODY}" stroke="${LINE}" stroke-width="3"/>` +
    [18, 30, 42]
      .map(
        y =>
          `<rect x="35" y="${y}" width="26" height="7" rx="2" fill="${FACE}" stroke="${LINE}" stroke-width="2"/>` +
          `<circle cx="56" cy="${y + 3.5}" r="2" fill="${LINE}"/>`,
      )
      .join(''),
)

const pcSvg = frame(
  `<rect x="22" y="14" width="52" height="34" rx="4" fill="${BODY}" stroke="${LINE}" stroke-width="3"/>` +
    `<rect x="28" y="20" width="40" height="22" rx="2" fill="${FACE}" stroke="${LINE}" stroke-width="2"/>` +
    `<line x1="48" y1="48" x2="48" y2="56" stroke="${LINE}" stroke-width="3"/>` +
    `<line x1="34" y1="58" x2="62" y2="58" stroke="${LINE}" stroke-width="4" stroke-linecap="round"/>`,
)

const cloudSvg = frame(
  `<path d="M28 54 a14 14 0 0 1 -2 -27 a18 18 0 0 1 34 -8 a14 14 0 0 1 12 24 a12 12 0 0 1 -4 11 z" fill="${BODY}" stroke="${LINE}" stroke-width="3"/>`,
)

const genericSvg = frame(
  `<rect x="8" y="14" width="80" height="44" rx="6" fill="${BODY}" stroke="${LINE}" stroke-width="3"/>` +
    `<circle cx="48" cy="36" r="9" fill="none" stroke="${LINE}" stroke-width="2.5"/>` +
    `<circle cx="48" cy="36" r="3" fill="${LINE}"/>` +
    [0, 60, 120, 180, 240, 300]
      .map(angle => {
        const x1 = (48 + 12 * Math.cos((angle * Math.PI) / 180)).toFixed(1)
        const y1 = (36 + 12 * Math.sin((angle * Math.PI) / 180)).toFixed(1)
        const x2 = (48 + 16 * Math.cos((angle * Math.PI) / 180)).toFixed(1)
        const y2 = (36 + 16 * Math.sin((angle * Math.PI) / 180)).toFixed(1)
        return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${LINE}" stroke-width="2.5"/>`
      })
      .join(''),
)

export const ICON_SVGS: Record<Kind, string> = {
  router: routerSvg,
  switch: switchSvg,
  firewall: firewallSvg,
  server: serverSvg,
  pc: pcSvg,
  cloud: cloudSvg,
  generic: genericSvg,
}

export function DeviceIcon({ kind }: { kind: Kind }) {
  return <span style={{ display: 'inline-flex' }} dangerouslySetInnerHTML={{ __html: ICON_SVGS[kind] }} />
}
