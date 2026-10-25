// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import { Grid } from 'antd'

/** 与 index.css @media (max-width: 768px) 及 Ant Design md 断点对齐 */
export const MOBILE_BREAKPOINT_PX = 768

export function useBreakpoint() {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  return {
    isMobile,
    isDesktop: !!screens.md,
    screens,
  }
}

export function useIsMobile(): boolean {
  return useBreakpoint().isMobile
}
