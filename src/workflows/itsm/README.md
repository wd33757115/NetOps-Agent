<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# ITSM Workflow 插件开发指南

本页已由工作台“Skills 和连接器 → 开发指南 → Workflow 开发手册”取代。

本目录下的防火墙流程 `itsm-firewall-change` 通过防火墙策略应用的 Workflow 动作（`skill: app:firewall-policy/<动作>`）生成策略和变更工单。它可以在问答中按要求启动，也可以由 ITSM 系统经工作台 `/api/itsm/webhook/<route_key>/` 带平台账号的用户名和密码调用。写法、触发方式和实测记录见手册。
