FROM alpine:3.20
RUN apk add --no-cache g++ python3
# Precompile <bits/stdc++.h> (what most competitive-programming solutions include) with the exact
# flags the harness uses. GCC then loads it instead of parsing ~100k lines on every compile,
# which cuts a typical compile from ~2.5 s to well under 1 s.
RUN for h in /usr/include/c++/*/*/bits/stdc++.h; do g++ -O2 -std=c++17 -x c++-header "$h" -o "$h.gch"; done
COPY harness.py testdata.py /opt/runner/
