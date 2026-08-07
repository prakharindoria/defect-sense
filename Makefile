# Thin delegating wrapper. The real implementation is tasks.py, because `make`
# is not present on every build machine. `make demo` and `python tasks.py demo`
# are the same thing.

PY ?= python

.PHONY: help install doctor probe lint fmt types test arch verify api web seed eval demo reset

help:      ; @$(PY) tasks.py help
install:   ; @$(PY) tasks.py install $(filter-out $@,$(MAKECMDGOALS))
doctor:    ; @$(PY) tasks.py doctor
probe:     ; @$(PY) tasks.py probe
lint:      ; @$(PY) tasks.py lint
fmt:       ; @$(PY) tasks.py fmt
types:     ; @$(PY) tasks.py types
test:      ; @$(PY) tasks.py test
arch:      ; @$(PY) tasks.py arch
verify:    ; @$(PY) tasks.py verify
api:       ; @$(PY) tasks.py api
web:       ; @$(PY) tasks.py web
seed:      ; @$(PY) tasks.py seed
eval:      ; @$(PY) tasks.py eval
demo:      ; @$(PY) tasks.py demo
reset:     ; @$(PY) tasks.py reset

# Swallow extra goals so `make install ai` passes "ai" through as an argument
# instead of make treating it as a second target.
%:
	@:
