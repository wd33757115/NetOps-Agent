import React from 'react'
import { packagedApps } from '../../apps/generated'

/** How one request reaches a package, drawn from the packages this build actually contains. */
export function ArchitectureDiagram() {
  return (
    <figure className="handbook-figure" aria-label="平台进程与请求路径">
      <div className="arch-tier">
        <span className="arch-name">浏览器</span>
      </div>
      <div className="arch-link"><span className="arch-arrow">请求 /api/…</span></div>
      <div className="arch-tier">
        <span className="arch-name">React 工作台</span><span className="arch-port">:3000</span>
        <p>页面与静态资源；/api 代理到 Django</p>
      </div>
      <div className="arch-link"><span className="arch-arrow">原样转发</span></div>
      <div className="arch-tier arch-authority">
        <span className="arch-name">Django BFF</span><span className="arch-port">:8001</span>
        <p>唯一的认证来源：登录、用户与用户组、应用授权、应用安装状态</p>
      </div>
      <div className="arch-link arch-link-pair">
        <span className="arch-arrow">转发 /api/apps/&lt;id&gt;/…</span>
        <span className="arch-arrow arch-arrow-back">每个请求回问：有无使用权、代码摘要是否一致</span>
      </div>
      <div className="arch-tier">
        <span className="arch-name">FastAPI Gateway</span><span className="arch-port">:8000</span>
        <p>平台内核扫描 src/apps/*/app.yaml，把每个包挂到 /api/apps/&lt;id&gt;</p>
        <ul className="arch-packages" aria-label="本次构建包含的能力包">
          {packagedApps.map(app => <li key={app.id}>{app.name}</li>)}
        </ul>
      </div>
      <div className="arch-backing">
        <span>Celery Worker：每个包自己的队列</span>
        <span>Beat：周期任务</span>
        <span>PostgreSQL</span>
        <span>MinIO</span>
        <span>Redis / RabbitMQ</span>
      </div>
      <figcaption>404：应用未启用或没有使用权；503：代码已改动但还没升级。拿不到 Django 的答复时一律拒绝。</figcaption>
    </figure>
  )
}

const STATES = [
  { key: 'discovered', name: '已发现', note: '内核扫描到清单，还没有安装记录', response: '接口 404' },
  { key: 'installed', name: '已安装', note: '版本和代码摘要已登记，代码已归档', response: '接口 404' },
  { key: 'enabled', name: '已启用', note: '部署检查通过，准入打开', response: '接口 200；代码改动未升级时 503' },
  { key: 'draining', name: '排空中', note: '准入已关闭，等在途工作收尾', response: '接口 404' },
  { key: 'disabled', name: '已停用', note: '授权、安装记录和业务数据保留', response: '接口 404' },
] as const

const STEPS = ['app_packages install', 'app_packages enable', 'app_packages disable', '在途工作归零']

/** The states an application passes through, with the command between each and what users then get. */
export function LifecycleDiagram() {
  return (
    <figure className="handbook-figure lifecycle" aria-label="应用生命周期">
      <ol className="lifecycle-track">
        {STATES.map((state, index) => (
          <React.Fragment key={state.key}>
            <li className={`lifecycle-state lifecycle-${state.key}`}>
              <span className="lifecycle-name">{state.name}</span>
              <code>{state.key}</code>
              <span className="lifecycle-note">{state.note}</span>
              <span className="lifecycle-response">{state.response}</span>
            </li>
            {index < STEPS.length && (
              <li className="lifecycle-step" aria-hidden="true"><span>{STEPS[index]}</span></li>
            )}
          </React.Fragment>
        ))}
      </ol>
      <ul className="lifecycle-returns">
        <li><strong>恢复</strong>已停用 → <code>app_packages enable</code> → 已启用</li>
        <li><strong>升级</strong>已停用 → <code>app_packages upgrade</code>（或 <code>start.ps1 -UpgradeApps</code>）→ 已安装 → 启用</li>
        <li><strong>失败</strong>安装出错进入 <code>failed</code>，修复后重新 <code>install</code></li>
      </ul>
    </figure>
  )
}
