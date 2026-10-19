import React, { useEffect, useState } from 'react'
import { Alert, Button, Descriptions, Spin, Typography } from 'antd'
import { api } from '@netops/config/api'

interface Greeting { message: string; version: string; served_at: string; role?: string }

export default function HelloDemo() {
  const [greeting, setGreeting] = useState<Greeting | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  // The platform's api client sends the signed-in user's token; the Gateway checks the App grant.
  const load = () => {
    setLoading(true)
    setError('')
    api.get<Greeting>('/apps/hello-demo/greeting')
      .then(response => setGreeting(response.data))
      .catch(() => setError('读取失败：应用可能已停用，或当前账号没有使用权限。'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  return (
    <section style={{ padding: 32, maxWidth: 640 }}>
      <Typography.Title level={2}>演示应用</Typography.Title>
      <Typography.Paragraph type="secondary">
        平台上最小的应用：一个后端接口、一个前端页面。开发手册用它演示应用从开发到下线的完整过程。
      </Typography.Paragraph>
      {loading && <Spin />}
      {error && <Alert type="error" showIcon message={error} />}
      {greeting && !loading && (
        <Descriptions column={1} bordered size="small" style={{ marginBottom: 16 }}>
          <Descriptions.Item label="后端返回">{greeting.message}</Descriptions.Item>
          <Descriptions.Item label="应用版本">{greeting.version}</Descriptions.Item>
          {greeting.role && <Descriptions.Item label="当前角色">{greeting.role}</Descriptions.Item>}
          <Descriptions.Item label="响应时间">{new Date(greeting.served_at).toLocaleString()}</Descriptions.Item>
        </Descriptions>
      )}
      <Button onClick={load} disabled={loading}>重新请求</Button>
    </section>
  )
}
