.PHONY: up down test verify

# Build and start the whole stack, and wait until the backend reports healthy.
up: .env
	docker compose up --build --detach --wait
	@echo "Frontend  http://localhost:3000"
	@echo "Backend   http://localhost:8000/health"

down:
	docker compose down

# First run: start from the example file, then put real API keys in it.
.env:
	cp .env.example .env
	@echo "Created .env from .env.example. Put your real API keys in LLM_API_KEYS."

# Backend tests (needs: pip install -r backend/requirements-dev.txt).
test:
	cd backend && python3 -m pytest -q

# Contract check against a running backend: make verify [URL=https://your-backend]
verify:
	python3 scripts/verify.py $(URL)
