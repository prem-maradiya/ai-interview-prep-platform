"""Global (shared) starter problems. All problems use stdin -> stdout I/O."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Problem, ProblemList, TestCase

PY_READ = "import sys\n\ndef solve(data: str) -> str:\n    # your code here\n    return \"\"\n\nprint(solve(sys.stdin.read()))\n"
JS_READ = "const data = require('fs').readFileSync(0, 'utf8');\n\nfunction solve(data) {\n  // your code here\n  return '';\n}\n\nconsole.log(solve(data));\n"
JAVA_READ = """import java.io.*;
import java.util.*;

public class Main {
    static String solve(String data) {
        // your code here
        return "";
    }

    public static void main(String[] args) throws IOException {
        String data = new String(System.in.readAllBytes());
        System.out.println(solve(data));
    }
}
"""
CPP_READ = """#include <bits/stdc++.h>
using namespace std;

string solve(const string& data) {
    // your code here
    return "";
}

int main() {
    ios::sync_with_stdio(false);
    string data((istreambuf_iterator<char>(cin)), istreambuf_iterator<char>());
    cout << solve(data) << endl;
}
"""
GO_READ = """package main

import (
\t"fmt"
\t"io"
\t"os"
)

func solve(data string) string {
\t// your code here
\treturn ""
}

func main() {
\tinput, _ := io.ReadAll(os.Stdin)
\tfmt.Println(solve(string(input)))
}
"""
STARTER_CODE = {"python": PY_READ, "javascript": JS_READ, "java": JAVA_READ, "cpp": CPP_READ, "go": GO_READ}

def _fizz(n: int) -> str:
    return "\n".join("FizzBuzz" if i % 15 == 0 else "Fizz" if i % 3 == 0 else "Buzz" if i % 5 == 0 else str(i)
                     for i in range(1, n + 1))


# `companies` lists where a problem (or a close variant) is commonly reported in interviews.
PROBLEMS = [
    {
        "title": "Reverse a String", "difficulty": "easy", "tags": ["strings"], "companies": ["Microsoft"],
        "description": "Read a single line `s` from standard input and print it reversed.\n\n**Example**\n\nInput: `hello`\nOutput: `olleh`",
        "tests": [("hello", "olleh", False), ("racecar", "racecar", False), ("ab cd", "dc ba", True), ("x", "x", True)],
    },
    {
        "title": "FizzBuzz", "difficulty": "easy", "tags": ["math", "loops"], "companies": ["Bloomberg"],
        "description": "Read an integer `n`. For each `i` from 1 to `n`, print `Fizz` if divisible by 3, `Buzz` if by 5, "
                       "`FizzBuzz` if by both, otherwise `i`. One value per line.",
        "tests": [("5", _fizz(5), False), ("15", _fizz(15), True), ("1", "1", True)],
    },
    {
        "title": "Two Sum", "difficulty": "easy", "tags": ["arrays", "hash-map"], "companies": ["Amazon", "Google", "Meta"],
        "description": "First line: space-separated integers `nums`. Second line: integer `target`.\n\n"
                       "Print the two indices `i j` (with `i < j`) such that `nums[i] + nums[j] == target`. "
                       "Exactly one answer exists.\n\nAim for O(n) time.",
        "tests": [("2 7 11 15\n9", "0 1", False), ("3 2 4\n6", "1 2", False), ("3 3\n6", "0 1", True),
                  ("1 5 9 13 21 4\n25", "4 5", True)],
    },
    {
        "title": "Valid Parentheses", "difficulty": "medium", "tags": ["stack", "strings"],
        "companies": ["Amazon", "Meta", "Microsoft"],
        "description": "Read a string containing only `()[]{}`. Print `true` if every bracket is closed by the same type "
                       "in the correct order, else `false`.",
        "tests": [("()[]{}", "true", False), ("(]", "false", False), ("([{}])", "true", True), ("((", "false", True),
                  ("){", "false", True)],
    },
    {
        "title": "Longest Substring Without Repeating Characters", "difficulty": "medium",
        "tags": ["sliding-window", "hash-map"], "companies": ["Amazon", "Google", "Bloomberg"],
        "description": "Read a string `s` (may be empty). Print the length of the longest substring without repeating characters.",
        "tests": [("abcabcbb", "3", False), ("bbbbb", "1", False), ("pwwkew", "3", True), ("", "0", True), ("dvdf", "3", True)],
    },
    {
        "title": "Binary Search", "difficulty": "easy", "tags": ["binary-search", "arrays"], "companies": ["Microsoft", "Amazon"],
        "description": "First line: integers sorted in ascending order (no duplicates). Second line: `target`.\n\n"
                       "Print the index of `target`, or `-1` if it isn't present. Aim for O(log n).",
        "tests": [("-1 0 3 5 9 12\n9", "4", False), ("-1 0 3 5 9 12\n2", "-1", False), ("5\n5", "0", True),
                  ("1 3 5 7 9 11\n1", "0", True), ("1 3 5 7 9 11\n11", "5", True)],
    },
    {
        "title": "Valid Anagram", "difficulty": "easy", "tags": ["hash-map", "strings"], "companies": ["Amazon", "Bloomberg"],
        "description": "Two lines: strings `s` and `t` (lowercase letters). Print `true` if `t` is an anagram of `s`, else `false`.",
        "tests": [("anagram\nnagaram", "true", False), ("rat\ncar", "false", False), ("a\na", "true", True),
                  ("ab\na", "false", True)],
    },
    {
        "title": "Climbing Stairs", "difficulty": "easy", "tags": ["dynamic-programming"], "companies": ["Adobe", "Amazon"],
        "description": "You climb a staircase of `n` steps, taking 1 or 2 steps at a time. Read `n` (1 <= n <= 45) and "
                       "print how many distinct ways there are to reach the top.",
        "tests": [("2", "2", False), ("3", "3", False), ("1", "1", True), ("5", "8", True), ("10", "89", True)],
    },
    {
        "title": "Maximum Subarray", "difficulty": "medium", "tags": ["arrays", "dynamic-programming"],
        "companies": ["Amazon", "LinkedIn", "Microsoft"],
        "description": "Read space-separated integers. Print the largest sum of any non-empty contiguous subarray. "
                       "Aim for O(n) (Kadane's algorithm).",
        "tests": [("-2 1 -3 4 -1 2 1 -5 4", "6", False), ("1", "1", False), ("5 4 -1 7 8", "23", True),
                  ("-3 -1 -2", "-1", True)],
    },
    {
        "title": "Merge Intervals", "difficulty": "medium", "tags": ["sorting", "intervals"], "companies": ["Google", "Meta", "Uber"],
        "description": "First line: `n`. Next `n` lines: `start end`. Merge all overlapping intervals (touching ones "
                       "count as overlapping) and print the result sorted by start, one `start end` per line.",
        "tests": [("4\n1 3\n2 6\n8 10\n15 18", "1 6\n8 10\n15 18", False), ("2\n1 4\n4 5", "1 5", False),
                  ("1\n1 4", "1 4", True), ("3\n1 4\n0 4\n6 7", "0 4\n6 7", True), ("2\n1 4\n2 3", "1 4", True)],
    },
    {
        "title": "Number of Islands", "difficulty": "medium", "tags": ["graphs", "bfs", "dfs"],
        "companies": ["Amazon", "Google", "Microsoft"],
        "description": "First line: `rows cols`. Then `rows` lines of `0`/`1` characters (1 = land). Print the number of "
                       "islands: groups of land connected horizontally or vertically.",
        "tests": [("4 5\n11110\n11010\n11000\n00000", "1", False), ("4 5\n11000\n11000\n00100\n00011", "3", False),
                  ("1 1\n0", "0", True), ("3 3\n101\n010\n101", "5", True)],
    },
    {
        "title": "Trapping Rain Water", "difficulty": "hard", "tags": ["two-pointers", "arrays"],
        "companies": ["Google", "Amazon", "Goldman Sachs"],
        "description": "Read space-separated non-negative bar heights. Print how many units of rain water are trapped "
                       "between the bars. Aim for O(n) time and O(1) extra space.",
        "tests": [("0 1 0 2 1 0 1 3 2 1 2 1", "6", False), ("4 2 0 3 2 5", "9", False), ("1", "0", True),
                  ("5 4 3 2 1", "0", True), ("2 0 2", "2", True)],
    },
    {
        "title": "Edit Distance", "difficulty": "hard", "tags": ["dynamic-programming", "strings"], "companies": ["Google", "Amazon"],
        "description": "Two lines: words `a` and `b` (lowercase, non-empty). Print the minimum number of single-character "
                       "insertions, deletions or replacements needed to turn `a` into `b`.",
        "tests": [("horse\nros", "3", False), ("intention\nexecution", "5", False), ("abc\nabc", "0", True),
                  ("kitten\nsitting", "3", True), ("a\nb", "1", True)],
    },
]

# Curated global lists, by problem title (ids differ between databases).
LISTS = [
    {"slug": "interview-warmup", "title": "Interview warm-up",
     "description": "Six quick problems to get comfortable with reading input, loops and hash maps.",
     "problems": ["Reverse a String", "FizzBuzz", "Two Sum", "Valid Anagram", "Climbing Stairs", "Binary Search"]},
    {"slug": "arrays-hashing", "title": "Arrays & hashing essentials",
     "description": "The patterns behind most array questions: hash maps, sliding windows and running sums.",
     "problems": ["Two Sum", "Valid Anagram", "Longest Substring Without Repeating Characters", "Maximum Subarray",
                  "Trapping Rain Water"]},
    {"slug": "big-tech-frequent", "title": "Frequently asked at big tech",
     "description": "Problems commonly reported in interviews at large tech companies.",
     "problems": ["Two Sum", "Valid Parentheses", "Merge Intervals", "Number of Islands",
                  "Longest Substring Without Repeating Characters", "Trapping Rain Water", "Edit Distance"]},
]


def _new_problem(p: dict) -> Problem:
    problem = Problem(tenant_id=None, title=p["title"], description=p["description"], difficulty=p["difficulty"],
                      tags=p["tags"], companies=p["companies"], starter_code=dict(STARTER_CODE))
    problem.tests = [TestCase(input=i, expected_output=o, is_hidden=h, position=n) for n, (i, o, h) in enumerate(p["tests"])]
    return problem


async def seed_global_problems(db: AsyncSession) -> None:
    """Idempotent: adds missing global problems and lists, and backfills fields added in later versions."""
    existing = {p.title: p for p in (await db.scalars(select(Problem).where(Problem.tenant_id.is_(None)))).all()}
    for spec in PROBLEMS:
        problem = existing.get(spec["title"])
        if problem is None:
            db.add(_new_problem(spec))
            continue
        missing = {k: v for k, v in STARTER_CODE.items() if k not in (problem.starter_code or {})}
        if missing:
            problem.starter_code = {**(problem.starter_code or {}), **missing}  # reassign so the JSON change is saved
        if not problem.companies:
            problem.companies = list(spec["companies"])
    have_lists = set((await db.scalars(select(ProblemList.slug).where(ProblemList.tenant_id.is_(None)))).all())
    for spec in LISTS:
        if spec["slug"] not in have_lists:
            db.add(ProblemList(tenant_id=None, slug=spec["slug"], title=spec["title"], description=spec["description"],
                               problem_titles=spec["problems"]))
    await db.commit()
