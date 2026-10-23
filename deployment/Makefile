PROJECT_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
COMPOSE := docker compose --env-file "$(PROJECT_ROOT)/.env" -f "$(PROJECT_ROOT)/deployment/docker-compose.yml" --profile core

.PHONY: help install up down down-v logs logs-app ps init-db django-migrate django-shell celery fastapi build build-only django react up-all

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

install: ## 安装Python依赖
	pip install --upgrade pip
	pip install -r "$(PROJECT_ROOT)/requirements.txt"

up: ## 启动必需中间件服务
	$(COMPOSE) up -d postgres redis rabbitmq minio

up-all: ## 启动核心应用服务（AI 使用 docker-compose.ai.yml 叠加配置）
	$(COMPOSE) up -d

build: ## 构建并启动所有服务
	$(COMPOSE) up -d --build

build-only: ## 仅构建镜像不启动
	$(COMPOSE) build

down: ## 停止所有服务
	$(COMPOSE) down

down-v: ## 停止服务并清理数据
	$(COMPOSE) down -v

logs: ## 查看所有服务日志
	$(COMPOSE) logs -f

logs-app: ## 仅查看应用服务日志
	$(COMPOSE) logs -f django react fastapi

ps: ## 查看服务状态
	$(COMPOSE) ps

init-db: ## 初始化数据库
	cd "$(PROJECT_ROOT)" && python -m src.platform.database.bootstrap apply

django-migrate: ## Django 数据库迁移
	$(COMPOSE) run --rm migrate

django-shell: ## 进入 Django Shell
	$(COMPOSE) exec django python manage.py shell

celery: ## 启动 Celery Worker
	cd "$(PROJECT_ROOT)" && celery -A src.core.celery_tasks.celery_app worker --loglevel=info -P solo -c 1

fastapi: ## 启动 FastAPI Gateway
	cd "$(PROJECT_ROOT)" && python -m src.gateway.main

django: ## 本地启动 Django（开发）
	cd "$(PROJECT_ROOT)/web/django_backend" && python manage.py runserver 8001

react: ## 本地启动 React（开发）
	cd "$(PROJECT_ROOT)/web/react_frontend" && npm run dev

