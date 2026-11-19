FROM eclipse-temurin:21-jdk-alpine
RUN apk add --no-cache python3
COPY harness.py /opt/runner/harness.py
