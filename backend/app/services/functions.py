"""Function-style problems: you write `twoSum(nums, target)` instead of reading stdin.

A problem's `signature` names the function, its typed parameters and return type. From it we
generate, per language, the starter code and a small *driver* that is added to the candidate's
code at run time: it reads the arguments (as tokens, see sandbox_images/testdata.py), calls the
function and prints the return value as JSON after a marker line. Anything the candidate prints
before that is shown as their own output. Results are compared as JSON values, so `[0, 1]`,
`[0,1]` and (for floats) `0.30000000000000004` vs `0.3` are all equal.

Supported types: int, long, float, bool, string, and 1-D / 2-D arrays of them (e.g. int[], string[][]).
"""
import importlib.util
import json
import math
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

_spec = importlib.util.spec_from_file_location(
    "prepforge_testdata", Path(__file__).resolve().parent.parent / "sandbox_images" / "testdata.py")
testdata = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(testdata)

RETURN_MARKER = "@@RETURN@@"
SCALARS = ("int", "long", "float", "bool", "string")
TYPE_RE = re.compile(r"^(int|long|float|bool|string)(\[\]){0,2}$")
NAME_RE = re.compile(r"^[a-z][A-Za-z0-9]{0,39}$")
# Names that are reserved in at least one supported language.
RESERVED = {
    "and", "as", "assert", "async", "await", "bool", "break", "byte", "case", "catch", "char", "class", "const",
    "continue", "def", "default", "del", "delete", "do", "double", "elif", "else", "enum", "except", "export",
    "extends", "false", "final", "finally", "float", "for", "from", "func", "function", "global", "go", "goto",
    "if", "import", "in", "instanceof", "int", "interface", "is", "lambda", "let", "long", "main", "map", "new",
    "nil", "none", "not", "null", "or", "package", "pass", "private", "protected", "public", "raise", "range",
    "return", "select", "short", "static", "string", "struct", "super", "switch", "this", "throw", "throws",
    "true", "try", "type", "typeof", "var", "void", "while", "with", "yield", "len", "print", "solution",
}


class Param(BaseModel):
    name: str
    type: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v) or v.lower() in RESERVED or v.startswith("pf"):
            raise ValueError(f"'{v}' can't be used as a parameter name (use camelCase letters and digits, not a keyword)")
        return v

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if not TYPE_RE.match(v):
            raise ValueError(f"unsupported type '{v}' (use int, long, float, bool, string, or T[] / T[][])")
        return v


class Signature(BaseModel):
    function: str
    params: list[Param] = Field(min_length=1, max_length=6)
    returns: str
    compare: str = "exact"  # "unordered": a returned list may be in any order

    @field_validator("function")
    @classmethod
    def _function(cls, v: str) -> str:
        if not NAME_RE.match(v) or v.lower() in RESERVED or v.startswith("pf"):
            raise ValueError(f"'{v}' can't be used as a function name (use camelCase, e.g. twoSum)")
        return v

    @field_validator("returns")
    @classmethod
    def _returns(cls, v: str) -> str:
        if not TYPE_RE.match(v):
            raise ValueError(f"unsupported return type '{v}'")
        return v

    @field_validator("compare")
    @classmethod
    def _compare(cls, v: str) -> str:
        if v not in ("exact", "unordered"):
            raise ValueError("compare must be 'exact' or 'unordered'")
        return v

    @property
    def types(self) -> list[str]:
        return [p.type for p in self.params]


def _base(t: str) -> tuple[str, int]:
    return t.replace("[]", ""), t.count("[]")


# ---------- validation of test data ----------
def check_test(sig: Signature, test_input: str, expected: str) -> None:
    """Raise ValueError if a test doesn't match the signature (generated inputs are checked when run)."""
    if not testdata.is_generated(test_input):
        testdata.parse_args(test_input, sig.types)
    try:
        value = json.loads(expected)
    except json.JSONDecodeError:
        raise ValueError(f"expected output must be a JSON value of type {sig.returns}")
    testdata._check(value, sig.returns)


# ---------- comparing results ----------
def _equal(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, int) and isinstance(b, int):
            return a == b
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    return a == b


def _sort_key(v) -> str:
    return json.dumps(v, sort_keys=True)


def results_match(sig: Signature, returned: str, expected: str) -> bool:
    try:
        got, want = json.loads(returned), json.loads(expected)
    except json.JSONDecodeError:
        return False
    if sig.compare == "unordered" and isinstance(got, list) and isinstance(want, list):
        got, want = sorted(got, key=_sort_key), sorted(want, key=_sort_key)
    return _equal(got, want)


def split_output(stdout: str) -> tuple[str, str | None]:
    """(what the candidate printed, the returned JSON or None if the function never returned)."""
    idx = stdout.rfind(RETURN_MARKER)
    if idx == -1:
        return stdout, None
    returned = stdout[idx + len(RETURN_MARKER):].strip().splitlines()
    printed = stdout[:idx].replace("\r\n", "\n").rstrip("\r\n")
    return printed, (returned[0].strip() if returned else "")


def format_args(sig: Signature, test_input: str) -> str:
    """Readable form of a test input, e.g. 'nums = [2,7,11,15], target = 9'."""
    if testdata.is_generated(test_input):
        return describe_generated(test_input)
    lines = [ln for ln in test_input.strip().splitlines() if ln.strip()]
    if len(lines) != len(sig.params):
        return test_input
    return ", ".join(f"{p.name} = {line.strip()}" for p, line in zip(sig.params, lines))


def describe_generated(test_input: str) -> str:
    try:
        spec = json.loads(test_input[len(testdata.GEN_PREFIX):])
        parts = []
        for p in spec["lines"]:
            if "ints" in p:
                parts.append(f"{int(p['ints']):,} integers")
            elif "chars" in p:
                parts.append(f"a {int(p['chars']):,}-character string")
        return "Large generated input: " + (", ".join(parts) or "custom")
    except (ValueError, KeyError, TypeError):
        return "Large generated input"


# ---------- per-language type names ----------
def _py(t: str) -> str:
    base, depth = _base(t)
    name = {"int": "int", "long": "int", "float": "float", "bool": "bool", "string": "str"}[base]
    for _ in range(depth):
        name = f"list[{name}]"
    return name


def _js(t: str) -> str:
    base, depth = _base(t)
    return {"int": "number", "long": "number", "float": "number", "bool": "boolean", "string": "string"}[base] + "[]" * depth


def _java(t: str) -> str:
    base, depth = _base(t)
    return {"int": "int", "long": "long", "float": "double", "bool": "boolean", "string": "String"}[base] + "[]" * depth


def _cpp(t: str) -> str:
    base, depth = _base(t)
    name = {"int": "int", "long": "long long", "float": "double", "bool": "bool", "string": "string"}[base]
    for _ in range(depth):
        name = f"vector<{name}>"
    return name


def _go(t: str) -> str:
    base, depth = _base(t)
    return "[]" * depth + {"int": "int", "long": "int64", "float": "float64", "bool": "bool", "string": "string"}[base]


def _java_default(t: str) -> str:
    base, depth = _base(t)
    if depth:
        return f"new {_java(base)}[0]" + "[]" * (depth - 1)
    return {"int": "0", "long": "0L", "float": "0.0", "bool": "false", "string": '""'}[base]


def _cpp_default(t: str) -> str:
    base, depth = _base(t)
    if depth:
        return "{}"
    return {"int": "0", "long": "0", "float": "0.0", "bool": "false", "string": '""'}[base]


def _go_default(t: str) -> str:
    base, depth = _base(t)
    if depth:
        return "nil"
    return {"int": "0", "long": "0", "float": "0", "bool": "false", "string": '""'}[base]


# ---------- starter code ----------
def starter_code(sig: Signature) -> dict[str, str]:
    f, ps = sig.function, sig.params
    py_params = ", ".join(f"{p.name}: {_py(p.type)}" for p in ps)
    js_doc = "".join(f" * @param {{{_js(p.type)}}} {p.name}\n" for p in ps)
    java_params = ", ".join(f"{_java(p.type)} {p.name}" for p in ps)
    cpp_params = ", ".join(f"{_cpp(p.type)}{'&' if _base(p.type)[1] or p.type == 'string' else ''} {p.name}" for p in ps)
    go_params = ", ".join(f"{p.name} {_go(p.type)}" for p in ps)
    return {
        "python": f"def {f}({py_params}) -> {_py(sig.returns)}:\n    # your code here\n    pass\n",
        "javascript": (f"/**\n{js_doc} * @return {{{_js(sig.returns)}}}\n */\nfunction {f}({', '.join(p.name for p in ps)}) {{\n"
                       "  // your code here\n}\n"),
        "java": (f"import java.util.*;\n\nclass Solution {{\n    public {_java(sig.returns)} {f}({java_params}) {{\n"
                 f"        // your code here\n        return {_java_default(sig.returns)};\n    }}\n}}\n"),
        "cpp": (f"#include <bits/stdc++.h>\nusing namespace std;\n\nclass Solution {{\npublic:\n    {_cpp(sig.returns)} {f}({cpp_params}) {{\n"
                f"        // your code here\n        return {_cpp_default(sig.returns)};\n    }}\n}};\n"),
        "go": (f"package main\n\nfunc {f}({go_params}) {_go(sig.returns)} {{\n\t// your code here\n"
               f"\treturn {_go_default(sig.returns)}\n}}\n"),
    }


# ---------- drivers ----------
_PY_DRIVER = '''

# ---- PrepForge driver (added when your code runs) ----
def _pf_main():
    import sys as _sys, json as _json
    _it = iter(_sys.stdin.read().split())
    def _rd(t):
        if t.endswith("[]"):
            return [_rd(t[:-2]) for _ in range(int(next(_it)))]
        tok = next(_it)
        if t in ("int", "long"):
            return int(tok)
        if t == "float":
            return float(tok)
        if t == "bool":
            return tok == "1"
        return bytes.fromhex(tok[1:]).decode("utf-8")
    _fn = globals().get(__FUNC__)
    if _fn is None and "Solution" in globals():
        _fn = getattr(globals()["Solution"](), __FUNC__)
    _result = _fn(*[_rd(t) for t in __TYPES__])
    _sys.stdout.flush()
    _sys.stdout.write("\\n@@RETURN@@" + _json.dumps(_result, separators=(",", ":")) + "\\n")
_pf_main()
'''

_JS_DRIVER = '''

// ---- PrepForge driver (added when your code runs) ----
;(() => {
  const toks = require("fs").readFileSync(0, "utf8").split(/\\s+/).filter(Boolean);
  let i = 0;
  const rd = (t) => {
    if (t.endsWith("[]")) { const n = Number(toks[i++]); const a = []; for (let k = 0; k < n; k++) a.push(rd(t.slice(0, -2))); return a; }
    const tok = toks[i++];
    if (t === "bool") return tok === "1";
    if (t === "string") return Buffer.from(tok.slice(1), "hex").toString("utf8");
    return Number(tok);
  };
  const result = __FUNC__(...__TYPES__.map(rd));
  process.stdout.write("\\n@@RETURN@@" + JSON.stringify(result === undefined ? null : result) + "\\n");
})();
'''

_JAVA_DRIVER = '''

// ---- PrepForge driver (added when your code runs) ----
public class Main {
    static byte[] pfBuf; static int pfPos = 0;
    static String pfTok() {
        while (pfPos < pfBuf.length && pfBuf[pfPos] <= ' ') pfPos++;
        int s = pfPos;
        while (pfPos < pfBuf.length && pfBuf[pfPos] > ' ') pfPos++;
        return new String(pfBuf, s, pfPos - s, java.nio.charset.StandardCharsets.US_ASCII);
    }
    static int rInt() { return Integer.parseInt(pfTok()); }
    static long rLong() { return Long.parseLong(pfTok()); }
    static double rFloat() { return Double.parseDouble(pfTok()); }
    static boolean rBool() { return pfTok().equals("1"); }
    static String rString() {
        String h = pfTok().substring(1); byte[] b = new byte[h.length() / 2];
        for (int k = 0; k < b.length; k++) b[k] = (byte) Integer.parseInt(h.substring(2 * k, 2 * k + 2), 16);
        return new String(b, java.nio.charset.StandardCharsets.UTF_8);
    }
__READERS__
    static String enc(int v) { return Integer.toString(v); }
    static String enc(long v) { return Long.toString(v); }
    static String enc(double v) { return Double.isFinite(v) ? Double.toString(v) : "null"; }
    static String enc(boolean v) { return v ? "true" : "false"; }
    static String enc(String v) {
        if (v == null) return "null";
        StringBuilder sb = new StringBuilder("\\"");
        for (char c : v.toCharArray()) {
            if (c == '"' || c == '\\\\') sb.append('\\\\').append(c);
            else if (c < 0x20 || c > 0x7e) sb.append(String.format("\\\\u%04x", (int) c));
            else sb.append(c);
        }
        return sb.append('"').toString();
    }
    static String enc(Object a) {
        if (a == null) return "null";
        StringBuilder sb = new StringBuilder("[");
        int n = java.lang.reflect.Array.getLength(a);
        for (int k = 0; k < n; k++) {
            if (k > 0) sb.append(',');
            Object x = java.lang.reflect.Array.get(a, k);
            if (x instanceof Integer) sb.append(enc((int) (Integer) x));
            else if (x instanceof Long) sb.append(enc((long) (Long) x));
            else if (x instanceof Double) sb.append(enc((double) (Double) x));
            else if (x instanceof Boolean) sb.append(enc((boolean) (Boolean) x));
            else if (x instanceof String) sb.append(enc((String) x));
            else sb.append(enc(x));
        }
        return sb.append(']').toString();
    }
    public static void main(String[] args) throws Exception {
        pfBuf = System.in.readAllBytes();
__CALL__
        System.out.flush();
        System.out.print("\\n@@RETURN@@" + enc(pfResult) + "\\n");
        System.out.flush();
    }
}
'''

_CPP_DRIVER = '''

// ---- PrepForge driver (added when your code runs) ----
#include <bits/stdc++.h>
using namespace std;
static long long pf_int() { long long v; cin >> v; return v; }
static double pf_float() { string s; cin >> s; return stod(s); }
static bool pf_bool() { string s; cin >> s; return s == "1"; }
static string pf_string() {
    string h; cin >> h; string out;
    for (size_t k = 1; k + 1 < h.size(); k += 2) out.push_back((char) stoi(h.substr(k, 2), nullptr, 16));
    return out;
}
template <class T> static vector<T> pf_vec(T (*one)()) { long long n = pf_int(); vector<T> v; v.reserve(n); for (long long k = 0; k < n; k++) v.push_back(one()); return v; }
__READERS__
static string pf_enc(long long v) { return to_string(v); }
static string pf_enc(int v) { return to_string(v); }
static string pf_enc(double v) { if (!isfinite(v)) return "null"; char b[64]; snprintf(b, sizeof b, "%.17g", v); return b; }
static string pf_enc(bool v) { return v ? "true" : "false"; }
static string pf_enc(const string& v) {
    string o = "\\"";
    for (unsigned char c : v) {
        if (c == '"' || c == '\\\\') { o += '\\\\'; o += (char) c; }
        else if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\\\u%04x", c); o += b; }
        else o += (char) c;
    }
    return o + "\\"";
}
static string pf_enc(const vector<bool>& v) { string o = "["; for (size_t k = 0; k < v.size(); k++) { if (k) o += ','; o += v[k] ? "true" : "false"; } return o + "]"; }
template <class T> static string pf_enc(const vector<T>& v) { string o = "["; for (size_t k = 0; k < v.size(); k++) { if (k) o += ','; o += pf_enc(v[k]); } return o + "]"; }
int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
__CALL__
    cout.flush();
    cout << "\\n@@RETURN@@" << pf_enc(pf_result) << "\\n";
    return 0;
}
'''

_GO_DRIVER = '''package main

// ---- PrepForge driver (added when your code runs) ----

import (
\t"bufio"
\t"encoding/hex"
\t"encoding/json"
\t"fmt"
\t"os"
\t"reflect"
\t"strconv"
\t"strings"
)

var pfIn = bufio.NewScanner(os.Stdin)

func pfTok() string { pfIn.Scan(); return pfIn.Text() }
func pfInt() int     { v, _ := strconv.Atoi(pfTok()); return v }
func pfLong() int64  { v, _ := strconv.ParseInt(pfTok(), 10, 64); return v }
func pfFloat() float64 { v, _ := strconv.ParseFloat(pfTok(), 64); return v }
func pfBool() bool   { return pfTok() == "1" }
func pfString() string { b, _ := hex.DecodeString(strings.TrimPrefix(pfTok(), "s")); return string(b) }
__READERS__
func pfEnc(v reflect.Value) string {
\tswitch v.Kind() {
\tcase reflect.Slice, reflect.Array:
\t\tparts := make([]string, v.Len())
\t\tfor i := range parts {
\t\t\tparts[i] = pfEnc(v.Index(i))
\t\t}
\t\treturn "[" + strings.Join(parts, ",") + "]"
\tcase reflect.String:
\t\tb, _ := json.Marshal(v.String())
\t\treturn string(b)
\tcase reflect.Bool:
\t\tif v.Bool() {
\t\t\treturn "true"
\t\t}
\t\treturn "false"
\tcase reflect.Float32, reflect.Float64:
\t\treturn strconv.FormatFloat(v.Float(), 'g', -1, 64)
\tdefault:
\t\treturn strconv.FormatInt(v.Int(), 10)
\t}
}

func main() {
\tpfIn.Buffer(make([]byte, 1024*1024), 64*1024*1024)
\tpfIn.Split(bufio.ScanWords)
__CALL__
\tos.Stdout.Sync()
\tfmt.Print("\\n@@RETURN@@" + pfEnc(reflect.ValueOf(pfResult)) + "\\n")
}
'''


def _java_reader(t: str) -> str:
    base, depth = _base(t)
    one = f"r{base.capitalize()}"
    if depth == 0:
        return one + "()"
    if depth == 1:
        return f"rArr_{base}()"
    return f"rArr2_{base}()"


def _java_readers() -> str:
    out = []
    for base in SCALARS:
        jt = _java(base)
        one = f"r{base.capitalize()}()"
        out.append(f"    static {jt}[] rArr_{base}() {{ int n = rInt(); {jt}[] a = new {jt}[n]; "
                   f"for (int k = 0; k < n; k++) a[k] = {one}; return a; }}")
        out.append(f"    static {jt}[][] rArr2_{base}() {{ int n = rInt(); {jt}[][] a = new {jt}[n][]; "
                   f"for (int k = 0; k < n; k++) a[k] = rArr_{base}(); return a; }}")
    return "\n".join(out)


def _cpp_reader(t: str) -> str:
    base, depth = _base(t)
    one = {"int": "pf_int_i", "long": "pf_int", "float": "pf_float", "bool": "pf_bool", "string": "pf_string"}[base]
    if depth == 0:
        return f"{one}()"
    if depth == 1:
        return f"pf_vec<{_cpp(base)}>({one})"
    return f"pf_vec2_{base}()"


def _cpp_readers() -> str:
    out = ["static int pf_int_i() { return (int) pf_int(); }"]
    for base in SCALARS:
        one = {"int": "pf_int_i", "long": "pf_int", "float": "pf_float", "bool": "pf_bool", "string": "pf_string"}[base]
        ct = _cpp(base)
        out.append(f"static vector<{ct}> pf_vec1_{base}() {{ return pf_vec<{ct}>({one}); }}")
        out.append(f"static vector<vector<{ct}>> pf_vec2_{base}() {{ return pf_vec<vector<{ct}>>(pf_vec1_{base}); }}")
    return "\n".join(out)


def _go_reader(t: str) -> str:
    base, depth = _base(t)
    one = f"pf{base.capitalize()}"
    if depth == 0:
        return one + "()"
    return f"pfArr{depth}{base.capitalize()}()"


def _go_readers() -> str:
    out = []
    for base in SCALARS:
        gt, one = _go(base), f"pf{base.capitalize()}"
        out.append(f"func pfArr1{base.capitalize()}() []{gt} {{ n := pfInt(); a := make([]{gt}, n); "
                   f"for i := range a {{ a[i] = {one}() }}; return a }}")
        out.append(f"func pfArr2{base.capitalize()}() [][]{gt} {{ n := pfInt(); a := make([][]{gt}, n); "
                   f"for i := range a {{ a[i] = pfArr1{base.capitalize()}() }}; return a }}")
    return "\n".join(out)


def prepare(sig: Signature, language: str, code: str) -> tuple[str, dict[str, str]]:
    """The program to run: candidate code + driver. Returns (main file source, extra files)."""
    f, ps = sig.function, sig.params
    types = json.dumps(sig.types)
    if language == "python":
        return code + _PY_DRIVER.replace("__FUNC__", json.dumps(f)).replace("__TYPES__", types), {}
    if language == "javascript":
        return code + _JS_DRIVER.replace("__FUNC__", f).replace("__TYPES__", types), {}
    if language == "java":
        lines = [f"        {_java(p.type)} pfA{k} = {_java_reader(p.type)};" for k, p in enumerate(ps)]
        lines.append(f"        {_java(sig.returns)} pfResult = new Solution().{f}("
                     + ", ".join(f"pfA{k}" for k in range(len(ps))) + ");")
        driver = _JAVA_DRIVER.replace("__READERS__", _java_readers()).replace("__CALL__", "\n".join(lines))
        return code + driver, {}
    if language == "cpp":
        lines = [f"    {_cpp(p.type)} pf_a{k} = {_cpp_reader(p.type)};" for k, p in enumerate(ps)]
        lines.append(f"    {_cpp(sig.returns)} pf_result = Solution().{f}("
                     + ", ".join(f"pf_a{k}" for k in range(len(ps))) + ");")
        driver = _CPP_DRIVER.replace("__READERS__", _cpp_readers()).replace("__CALL__", "\n".join(lines))
        return code + driver, {}
    if language == "go":
        lines = [f"\tpfA{k} := {_go_reader(p.type)}" for k, p in enumerate(ps)]
        lines.append(f"\tpfResult := {f}(" + ", ".join(f"pfA{k}" for k in range(len(ps))) + ")")
        driver = _GO_DRIVER.replace("__READERS__", _go_readers()).replace("__CALL__", "\n".join(lines))
        return code, {"pf_driver.go": driver}
    raise ValueError(f"unsupported language {language}")
