FROM python:3.12-alpine
# The official image ships without .pyc files, and the sandbox's root filesystem is read-only, so
# every run would recompile each imported stdlib module from source (~0.4 s of CPU time for a few
# imports, counted against the candidate). Compile them once here instead.
RUN python3 -m compileall -q -j 0 /usr/local/lib/python3.12
COPY harness.py testdata.py /opt/runner/
