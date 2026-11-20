"""Seed data for function-style problems and for large "complexity check" tests.

Large tests are generator specs (see sandbox_images/testdata.py), expanded inside the sandbox.
Their expected outputs are computed here, once, by a known-good reference solution.
"""
import json
from functools import cache

from app.services.functions import testdata


def gen(*lines: dict) -> str:
    return testdata.GEN_PREFIX + json.dumps({"lines": list(lines)}, separators=(",", ":"))


def _ints(text: str) -> list[int]:
    return list(map(int, text.split()))


# ---------- reference solutions (input text -> expected output) ----------
def _two_sum(text: str) -> str:
    first, second = text.split("\n")[:2]
    nums, target, seen = _ints(first), int(second), {}
    for i, x in enumerate(nums):
        if target - x in seen:
            return f"{seen[target - x]} {i}"
        seen[x] = i
    raise ValueError("no answer")


def _max_subarray(text: str) -> str:
    a = _ints(text)
    best = cur = a[0]
    for x in a[1:]:
        cur = max(x, cur + x)
        best = max(best, cur)
    return str(best)


def _longest_unique(text: str) -> str:
    s = text.strip("\n")
    best = start = 0
    last: dict[str, int] = {}
    for i, ch in enumerate(s):
        if last.get(ch, -1) >= start:
            start = last[ch] + 1
        last[ch] = i
        best = max(best, i - start + 1)
    return str(best)


def _trap(text: str) -> str:
    h = _ints(text)
    lo, hi, lmax, rmax, water = 0, len(h) - 1, 0, 0, 0
    while lo < hi:
        if h[lo] < h[hi]:
            lmax = max(lmax, h[lo])
            water += lmax - h[lo]
            lo += 1
        else:
            rmax = max(rmax, h[hi])
            water += rmax - h[hi]
            hi -= 1
    return str(water)


def _contains_duplicate(text: str) -> str:
    nums = json.loads(text)
    return json.dumps(len(set(nums)) != len(nums))


def _max_profit(text: str) -> str:
    best, low = 0, None
    for p in json.loads(text):
        low = p if low is None else min(low, p)
        best = max(best, p - low)
    return str(best)


# Large tests for the classic stdin/stdout problems: (generator spec, reference solution).
STRESS = {
    "Two Sum": [(gen({"ints": 100_000, "min": -10**9, "max": 10**9, "mod": 4, "residue": 0, "seed": 11,
                      "insert": [[31_337, 493_829], [88_888, -9_378]]},
                     {"value": "484451"}), _two_sum)],
    "Maximum Subarray": [(gen({"ints": 200_000, "min": -10_000, "max": 10_000, "seed": 7}), _max_subarray)],
    "Longest Substring Without Repeating Characters": [
        (gen({"chars": 200_000, "alphabet": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "seed": 3}),
         _longest_unique)],
    "Trapping Rain Water": [(gen({"ints": 200_000, "min": 0, "max": 100_000, "seed": 5}), _trap)],
}


@cache  # expanding 200,000-element inputs takes a moment: do it once per process
def stress_tests(title: str) -> list[tuple[str, str, bool]]:
    """(input, expected, hidden) for a problem's large tests. Expected values are computed by the reference."""
    return [(spec, ref(testdata.expand(spec)), True) for spec, ref in STRESS.get(title, [])]


def _sig(function: str, params: list[tuple[str, str]], returns: str, compare: str = "exact") -> dict:
    return {"function": function, "params": [{"name": n, "type": t} for n, t in params], "returns": returns,
            "compare": compare}


def _j(*values) -> str:
    return "\n".join(json.dumps(v, separators=(",", ":")) for v in values)


# Function-style problems. Test input: one JSON value per line, one line per parameter.
FUNCTION_PROBLEMS = [
    {
        "title": "Contains Duplicate", "difficulty": "easy", "tags": ["arrays", "hash-map"],
        "companies": ["Amazon", "Apple", "Adobe"],
        "signature": _sig("containsDuplicate", [("nums", "int[]")], "bool"),
        "description": "Return `true` if any value appears at least twice in `nums`, and `false` if every element "
                       "is distinct.\n\nAim for O(n) time: the hidden tests include an array of 200,000 numbers.",
        "tests": [(_j([1, 2, 3, 1]), "true", False), (_j([1, 2, 3, 4]), "false", False),
                  (_j([1, 1, 1, 3, 3, 4, 3, 2, 4, 2]), "true", True), (_j([7]), "false", True)],
        "stress": [(gen({"ints": 200_000, "min": -10**9, "max": 10**9, "distinct": True, "seed": 21, "json": True}),
                    _contains_duplicate)],
    },
    {
        "title": "Best Time to Buy and Sell Stock", "difficulty": "easy", "tags": ["arrays", "greedy"],
        "companies": ["Amazon", "Meta", "Goldman Sachs"],
        "signature": _sig("maxProfit", [("prices", "int[]")], "int"),
        "description": "`prices[i]` is a stock's price on day `i`. Choose one day to buy and a later day to sell. "
                       "Return the maximum profit, or `0` if no profit is possible.\n\nAim for O(n) time.",
        "tests": [(_j([7, 1, 5, 3, 6, 4]), "5", False), (_j([7, 6, 4, 3, 1]), "0", False), (_j([2, 4, 1]), "2", True),
                  (_j([1]), "0", True), (_j([3, 3, 5, 0, 0, 3, 1, 4]), "4", True)],
        "stress": [(gen({"ints": 200_000, "min": 0, "max": 10_000, "seed": 8, "json": True}), _max_profit)],
    },
    {
        "title": "Valid Palindrome", "difficulty": "easy", "tags": ["strings", "two-pointers"],
        "companies": ["Meta", "Microsoft"],
        "signature": _sig("isPalindrome", [("s", "string")], "bool"),
        "description": "A phrase is a palindrome if, after lowercasing it and removing every non-alphanumeric "
                       "character, it reads the same forwards and backwards. Return whether `s` is one.",
        "tests": [(_j("A man, a plan, a canal: Panama"), "true", False), (_j("race a car"), "false", False),
                  (_j(" "), "true", True), (_j("0P"), "false", True), (_j("No 'x' in Nixon"), "true", True)],
    },
    {
        "title": "Longest Common Prefix", "difficulty": "easy", "tags": ["strings"], "companies": ["Google", "Adobe"],
        "signature": _sig("longestCommonPrefix", [("strs", "string[]")], "string"),
        "description": "Return the longest string that is a prefix of every string in `strs` (an empty string if "
                       "there is none).",
        "tests": [(_j(["flower", "flow", "flight"]), '"fl"', False), (_j(["dog", "racecar", "car"]), '""', False),
                  (_j(["a"]), '"a"', True), (_j(["ab", "ab"]), '"ab"', True), (_j(["", "b"]), '""', True)],
    },
    {
        "title": "Product of Array Except Self", "difficulty": "medium", "tags": ["arrays", "prefix-sums"],
        "companies": ["Amazon", "Meta", "Apple"],
        "signature": _sig("productExceptSelf", [("nums", "int[]")], "int[]"),
        "description": "Return an array where element `i` is the product of every element of `nums` except "
                       "`nums[i]`, without using division. Aim for O(n) time.",
        "tests": [(_j([1, 2, 3, 4]), "[24,12,8,6]", False), (_j([-1, 1, 0, -3, 3]), "[0,0,9,0,0]", False),
                  (_j([2, 3]), "[3,2]", True), (_j([0, 0]), "[0,0]", True)],
    },
    {
        "title": "Rotate Image", "difficulty": "medium", "tags": ["arrays", "matrix"], "companies": ["Microsoft", "Amazon"],
        "signature": _sig("rotate", [("matrix", "int[][]")], "int[][]"),
        "description": "Return the `n x n` matrix rotated 90 degrees clockwise.",
        "tests": [(_j([[1, 2, 3], [4, 5, 6], [7, 8, 9]]), "[[7,4,1],[8,5,2],[9,6,3]]", False),
                  (_j([[1]]), "[[1]]", False), (_j([[1, 2], [3, 4]]), "[[3,1],[4,2]]", True),
                  (_j([[5, 1, 9, 11], [2, 4, 8, 10], [13, 3, 6, 7], [15, 14, 12, 16]]),
                   "[[15,13,2,5],[14,3,4,1],[12,6,8,9],[16,7,10,11]]", True)],
    },
    {
        "title": "Top K Frequent Elements", "difficulty": "medium", "tags": ["hash-map", "heap", "sorting"],
        "companies": ["Amazon", "Meta", "Google"],
        "signature": _sig("topKFrequent", [("nums", "int[]"), ("k", "int")], "int[]", compare="unordered"),
        "description": "Return the `k` most frequent elements of `nums`, in any order. The answer is unique.",
        "tests": [(_j([1, 1, 1, 2, 2, 3], 2), "[1,2]", False), (_j([1], 1), "[1]", False),
                  (_j([4, 4, 5, 5, 5, 6], 2), "[5,4]", True), (_j([3, 0, 1, 0], 1), "[0]", True)],
    },
    {
        "title": "Median of Two Sorted Arrays", "difficulty": "hard", "tags": ["binary-search", "arrays"],
        "companies": ["Google", "Amazon", "Apple"],
        "signature": _sig("findMedianSortedArrays", [("nums1", "int[]"), ("nums2", "int[]")], "float"),
        "description": "Given two sorted arrays (either may be empty, but not both), return the median of all "
                       "their elements combined. Answers within 1e-6 are accepted.\n\nThe classic follow-up: "
                       "O(log(m + n)) time.",
        "tests": [(_j([1, 3], [2]), "2.0", False), (_j([1, 2], [3, 4]), "2.5", False), (_j([], [1]), "1.0", True),
                  (_j([0, 0], [0, 0]), "0.0", True), (_j([1, 2, 3, 4, 5], [6, 7, 8]), "4.5", True)],
    },
]


def function_problem_tests(spec: dict) -> list[tuple[str, str, bool]]:
    return list(spec["tests"]) + _function_stress(spec["title"])


@cache
def _function_stress(title: str) -> list[tuple[str, str, bool]]:
    spec = next(p for p in FUNCTION_PROBLEMS if p["title"] == title)
    return [(s, ref(testdata.expand(s)), True) for s, ref in spec.get("stress", [])]
