import { useEffect } from 'react'
import { App as AntdApp } from 'antd'

type AppMessageApi = ReturnType<typeof AntdApp.useApp>['message']

let currentMessageApi: AppMessageApi | null = null

export const AppMessageBridge = () => {
  const { message } = AntdApp.useApp()

  useEffect(() => {
    currentMessageApi = message
    return () => {
      if (currentMessageApi === message) currentMessageApi = null
    }
  }, [message])

  return null
}

export const appMessage = {
  error(content: string) {
    return currentMessageApi?.error(content)
  },
}
