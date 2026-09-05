export const workbenchEnabled =
  import.meta.env.VITE_WORKBENCH_ENABLED !== 'false'
export const documentsEnabled = import.meta.env.VITE_OFFICIAL_DOCUMENTS_ENABLED !== 'false'
export const appRoutes = {
  workbench: '/workbench',
  documents: '/apps/official-documents',
  patrol: workbenchEnabled ? '/apps/patrol' : '/devices?tab=patrol',
  rules: workbenchEnabled ? '/apps/patrol-rules' : '/skills/parser',
}

/** Accept existing links as well as canonical ones, preserving evidence parameters. */
export function businessHref(href: string): string {
  if (!href.startsWith('/')) return href
  const url = new URL(href, 'https://netops.invalid')
  if (workbenchEnabled) {
    if (
      url.pathname === '/devices' &&
      url.searchParams.get('tab') === 'patrol'
    ) {
      url.pathname = '/apps/patrol'
      url.searchParams.delete('tab')
    } else if (
      url.pathname === '/skills/parser' ||
      url.pathname.startsWith('/skills/parser/')
    ) {
      url.pathname = url.pathname.replace(
        '/skills/parser',
        '/apps/patrol-rules',
      )
    }
  } else {
    if (url.pathname === '/apps/patrol') {
      url.pathname = '/devices'
      url.searchParams.set('tab', 'patrol')
    } else if (
      url.pathname === '/apps/patrol-rules' ||
      url.pathname.startsWith('/apps/patrol-rules/')
    ) {
      url.pathname = url.pathname.replace(
        '/apps/patrol-rules',
        '/skills/parser',
      )
    }
  }
  return url.pathname + url.search + url.hash
}
