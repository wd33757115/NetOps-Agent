import type { ComponentType, LazyExoticComponent } from 'react'

export interface AppFrontend {
  routes: { path: string; component: ComponentType | LazyExoticComponent<ComponentType> }[]
}
