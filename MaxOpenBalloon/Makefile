.PHONY: validate python-check docker-up docker-dev docker-up-local-auth docker-dev-local-auth docker-down docker-logs

validate: python-check

python-check:
	python -m compileall services

docker-up:
	cd deploy/docker && docker compose up --build -d

docker-dev:
	cd deploy/docker && docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d

docker-up-local-auth:
	cd deploy/docker && docker compose --profile local-auth up --build -d

docker-dev-local-auth:
	cd deploy/docker && docker compose --profile local-auth -f docker-compose.yml -f docker-compose.dev.yml up --build -d

docker-down:
	cd deploy/docker && docker compose down

docker-logs:
	cd deploy/docker && docker compose logs -f --tail 200
