FROM node:22-alpine
RUN apk add --no-cache python3
COPY harness.py /opt/runner/harness.py
