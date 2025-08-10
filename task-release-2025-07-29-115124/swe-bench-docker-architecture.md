# SWE-bench Docker Architecture

## Обзор

SWE-bench использует Docker для создания изолированной среды выполнения для каждого репозитория и коммита. Это обеспечивает воспроизводимость и изоляцию тестов.

## Основные компоненты

### 1. Base Images
- **Ubuntu-based images**: Основные образы на базе Ubuntu LTS
- **Python environments**: Образы с предустановленными версиями Python
- **System dependencies**: Системные пакеты и библиотеки

### 2. Environment Setup
```dockerfile
# Пример базового образа
FROM ubuntu:20.04

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    git \
    build-essential

# Создание пользователя для изоляции
RUN useradd -m -s /bin/bash swebench
USER swebench
WORKDIR /home/swebench
```

### 3. Repository Cloning
- Клонирование целевого репозитория
- Checkout на базовый коммит
- Установка зависимостей проекта

### 4. Patch Application
- Применение golden patch
- Валидация применения патча
- Откат при неудаче

### 5. Test Execution
- Запуск тестов в изолированной среде
- Сбор результатов и логов
- Анализ успешности выполнения

## Workflow

1. **Image Building**
   ```bash
   docker build -t swe-bench-env .
   ```

2. **Container Creation**
   ```bash
   docker run --rm -v $(pwd):/workspace swe-bench-env
   ```

3. **Environment Setup**
   - Установка зависимостей
   - Конфигурация окружения

4. **Test Execution**
   - Применение патча
   - Запуск тестов
   - Сбор результатов

## Ключевые особенности

### Изоляция
- Каждый тест запускается в отдельном контейнере
- Исключение влияния между тестами
- Чистое окружение для каждого запуска

### Воспроизводимость
- Фиксированные версии системных пакетов
- Контролируемое окружение Python
- Детерминированные результаты

### Масштабируемость
- Параллельный запуск контейнеров
- Оптимизация использования ресурсов
- Кэширование образов

## Команды Docker

### Основные операции
```bash
# Сборка образа
docker build -f Dockerfile -t swe-bench:latest .

# Запуск контейнера
docker run --rm -it \
  -v /path/to/repo:/workspace \
  swe-bench:latest /bin/bash

# Выполнение команды в контейнере
docker exec -it container_id python -m pytest
```

### Управление ресурсами
```bash
# Ограничение памяти
docker run --memory=4g swe-bench:latest

# Ограничение CPU
docker run --cpus=2.0 swe-bench:latest

# Таймаут выполнения
timeout 1800 docker run swe-bench:latest
```

## Конфигурация

### Environment Variables
- `PYTHONPATH`: Путь к Python модулям
- `REPO_PATH`: Путь к репозиторию
- `TEST_COMMAND`: Команда запуска тестов

### Volume Mounts
- `/workspace`: Рабочая директория
- `/tmp`: Временные файлы
- `/cache`: Кэш зависимостей

## Оптимизации

### Image Caching
- Многослойное кэширование
- Кэш зависимостей
- Переиспользование базовых образов

### Performance
- Предварительная сборка образов
- Параллельная обработка
- Оптимизация сетевых операций

## Безопасность

### Изоляция процессов
- Ограниченные права пользователя
- Изоляция файловой системы
- Контроль сетевого доступа

### Resource Limits
- Ограничение памяти
- Ограничение CPU
- Таймауты выполнения

## Заключение

Docker архитектура SWE-bench обеспечивает надежную, воспроизводимую и масштабируемую платформу для выполнения тестов программного обеспечения в изолированной среде.
