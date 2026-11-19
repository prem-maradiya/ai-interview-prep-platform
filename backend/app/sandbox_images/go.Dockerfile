FROM golang:1.23-alpine
RUN apk add --no-cache python3
# Pre-compile commonly used standard-library packages into a cache. Each run copies it into its
# writable /tmp, so programs don't recompile fmt/os/... from scratch inside the CPU limit.
# CGO_ENABLED / GOTOOLCHAIN must match the harness's environment, or the cache won't be reused.
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOCACHE=/opt/gocache
RUN go build bufio container/heap fmt io math math/big os sort strconv strings unicode \
    && chmod -R a+rX /opt/gocache
COPY harness.py /opt/runner/harness.py
