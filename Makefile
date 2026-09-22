PYTHON ?= python3
ENGINE ?= podman
IMAGE ?= localhost/submerge:test
NGINX_IMAGE ?= docker.io/library/nginx:stable-alpine

.PHONY: check lint format test image test-image

check: lint test

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	bash -n scripts/test_formats.sh

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

test:
	$(PYTHON) -m unittest discover -s tests -v

image:
	$(ENGINE) build -f Containerfile -t $(IMAGE) .

test-image:
	$(ENGINE) pull $(NGINX_IMAGE)
	SUBMERGE_TEST_IMAGE=$(IMAGE) SUBMERGE_TEST_NGINX_IMAGE=$(NGINX_IMAGE) \
	CONTAINER_ENGINE=$(ENGINE) $(PYTHON) -m unittest -v tests.test_http
