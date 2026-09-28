
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent.parent

# 统一加载仓库根目录 .env（与 FastAPI pydantic Settings 一致）
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BASE_DIR / ".env")  # 可选：django_backend 本地覆盖

# 允许 BFF 引用仓库 src/*（token_store、security 等）
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-default-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DJANGO_DEBUG', 'True').lower() in ('true', '1', 't')

ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Raw capability uploads are capped again by the BFF (20 MiB) and each App.
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024


# Application definition

INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'channels',
    'chat',
    'bff',
]

MIDDLEWARE = [
    'bff.middleware.BFFRequestIDMiddleware',
    'bff.middleware.BFFRateLimitMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'django_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'django_backend.wsgi.application'
ASGI_APPLICATION = 'django_backend.asgi.application'


# Database
if os.getenv("DJANGO_DATABASE_ENGINE", "postgresql").lower() == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("DJANGO_SQLITE_PATH", BASE_DIR / "db.sqlite3"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "netops_agent"),
            "USER": os.getenv("POSTGRES_USER", "netops"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "netops123456"),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            "OPTIONS": {
                "options": "-c search_path=django,public",
            },
        }
    }


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
LANGUAGE_CODE = 'zh-hans'

TIME_ZONE = os.getenv('TZ', 'Asia/Shanghai')

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
# React assets are served separately; the optional Django source directory is
# only registered when present. Django app/admin assets still use app finders.
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').is_dir() else []

# WhiteNoise 配置
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# 媒体文件
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'


# CORS 配置
CORS_ALLOWED_ORIGINS = os.getenv(
    'DJANGO_CORS_ALLOWED_ORIGINS',
    'http://localhost:3000,http://127.0.0.1:3000'
).split(',')

# 开发环境允许所有来源（可选）
if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True

# 允许的方法
CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]

# 允许的头部
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

CORS_ALLOW_CREDENTIALS = True


# CSRF 配置
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS


# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# SimpleJWT 配置
from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': False,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'SIGNING_KEY': os.getenv('JWT_SECRET_KEY', SECRET_KEY),
    'ALGORITHM': os.getenv('JWT_ALGORITHM', 'HS256'),
}


# FastAPI 后端地址
FASTAPI_BASE_URL = os.getenv('FASTAPI_BASE_URL', 'http://localhost:8000')

# BFF 鉴权：生产默认开启；开发环境 DEBUG=True 时默认关闭（可设 BFF_REQUIRE_AUTH=true 强制开启）
BFF_REQUIRE_AUTH = os.getenv('BFF_REQUIRE_AUTH', '').lower() in ('true', '1', 'yes') if os.getenv('BFF_REQUIRE_AUTH') else (not DEBUG)

# BFF 限流（Redis 优先；DEBUG 下默认关闭，设 BFF_RATE_LIMIT_IN_DEBUG=true 启用）
BFF_RATE_LIMIT_CHAT = int(os.getenv('BFF_RATE_LIMIT_CHAT', '30'))
BFF_RATE_LIMIT_DEFAULT = int(os.getenv('BFF_RATE_LIMIT_DEFAULT', '600'))
BFF_RATE_LIMIT_WINDOW = int(os.getenv('BFF_RATE_LIMIT_WINDOW', '60'))
BFF_RATE_LIMIT_IN_DEBUG = os.getenv('BFF_RATE_LIMIT_IN_DEBUG', '').lower() in ('true', '1', 'yes')


# Channels（WebSocket 代理）
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}


# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
