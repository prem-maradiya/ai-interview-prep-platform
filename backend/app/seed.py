"""Global (shared) starter problems. All problems use stdin -> stdout I/O."""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Problem, TestCase

PY_READ = "import sys\n\ndef solve(data: str) -> str:\n    # your code here\n    return \"\"\n\nprint(solve(sys.stdin.read()))\n"
JS_READ = "const data = require('fs').readFileSync(0, 'utf8');\n\nfunction solve(data) {\n  // your code here\n  return '';\n}\n\nconsole.log(solve(data));\n"

PROBLEMS = [
    {
        "title": "Reverse a String",
        "difficulty": "easy",
        "tags": ["strings"],
        "description": "Read a single line `s` from standard input and print it reversed.\n\n**Example**\n\nInput: `hello`\nOutput: `olleh`",
        "tests": [("hello", "olleh", False), ("racecar", "racecar", False), ("ab cd", "dc ba", True), ("x", "x", True)],
    },
    {
        "title": "FizzBuzz",
        "difficulty": "easy",
        "tags": ["math", "loops"],
        "description": "Read an integer `n`. For each `i` from 1 to `n`, print `Fizz` if divisible by 3, `Buzz` if by 5, "
                       "`FizzBuzz` if by both, otherwise `i`. One value per line.",
        "tests": [("5", "1\n2\nFizz\n4\nBuzz", False), ("15", "\n".join(
            "FizzBuzz" if i % 15 == 0 else "Fizz" if i % 3 == 0 else "Buzz" if i % 5 == 0 else str(i) for i in range(1, 16)), True),
            ("1", "1", True)],
    },
    {
        "title": "Two Sum",
        "difficulty": "easy",
        "tags": ["arrays", "hash-map"],
        "description": "First line: space-separated integers `nums`. Second line: integer `target`.\n\n"
                       "Print the two indices `i j` (with `i < j`) such that `nums[i] + nums[j] == target`. "
                       "Exactly one answer exists.\n\nAim for O(n) time.",
        "tests": [("2 7 11 15\n9", "0 1", False), ("3 2 4\n6", "1 2", False), ("3 3\n6", "0 1", True),
                  ("1 5 9 13 21 4\n25", "4 5", True)],
    },
    {
        "title": "Valid Parentheses",
        "difficulty": "medium",
        "tags": ["stack", "strings"],
        "description": "Read a string containing only `()[]{}`. Print `true` if every bracket is closed by the same type "
                       "in the correct order, else `false`.",
        "tests": [("()[]{}", "true", False), ("(]", "false", False), ("([{}])", "true", True), ("((", "false", True),
                  ("){", "false", True)],
    },
    {
        "title": "Longest Substring Without Repeating Characters",
        "difficulty": "medium",
        "tags": ["sliding-window", "hash-map"],
        "description": "Read a string `s` (may be empty). Print the length of the longest substring without repeating characters.",
        "tests": [("abcabcbb", "3", False), ("bbbbb", "1", False), ("pwwkew", "3", True), ("", "0", True), ("dvdf", "3", True)],
    },
]


async def seed_global_problems(db: AsyncSession) -> None:
    if await db.scalar(select(func.count(Problem.id)).where(Problem.tenant_id.is_(None))):
        return
    for p in PROBLEMS:
        problem = Problem(
            tenant_id=None, title=p["title"], description=p["description"], difficulty=p["difficulty"],
            tags=p["tags"], starter_code={"python": PY_READ, "javascript": JS_READ},
        )
        problem.tests = [TestCase(input=i, expected_output=o, is_hidden=h, position=n) for n, (i, o, h) in enumerate(p["tests"])]
        db.add(problem)
    await db.commit()
