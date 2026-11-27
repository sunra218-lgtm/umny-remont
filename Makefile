.PHONY: up down logs reset test lint

up:      ## Запустить всё: сайт на http://localhost:3000
	docker compose up --build -d
	@echo "Готово: откройте http://localhost:3000"

down:    ## Остановить
	docker compose down

logs:    ## Логи backend (там же видны «СМС» в режиме разработки)
	docker compose logs -f backend

reset:   ## Остановить и удалить локальную базу данных
	docker compose down -v

test:    ## Тесты backend (нужен запущенный docker compose: берётся его Postgres)
	docker compose exec -T db psql -U umny -tc "SELECT 1 FROM pg_database WHERE datname='umny_test'" | grep -q 1 || \
		docker compose exec -T db psql -U umny -c "CREATE DATABASE umny_test"
	docker compose exec -T -e TEST_DATABASE_URL=postgresql+asyncpg://umny:umny@db:5432/umny_test backend pytest -q

lint:
	docker compose exec -T backend sh -c "ruff check . && ruff format --check ."
