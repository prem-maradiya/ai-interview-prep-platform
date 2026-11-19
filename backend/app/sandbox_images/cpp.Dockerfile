FROM alpine:3.20
RUN apk add --no-cache g++ python3
COPY harness.py /opt/runner/harness.py
