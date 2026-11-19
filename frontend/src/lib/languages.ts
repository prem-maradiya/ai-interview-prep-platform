/** Languages the judge supports. `monaco` is the editor's language id. Keep in sync with the backend's LANGUAGES. */
export const LANGUAGES = [
  { id: "python", label: "Python 3.12", monaco: "python" },
  { id: "javascript", label: "JavaScript (Node 22)", monaco: "javascript" },
  { id: "java", label: "Java 21", monaco: "java" },
  { id: "cpp", label: "C++17 (g++)", monaco: "cpp" },
  { id: "go", label: "Go 1.23", monaco: "go" },
] as const;

export type LangId = (typeof LANGUAGES)[number]["id"];

export const monacoLang = (id: string) => LANGUAGES.find((l) => l.id === id)?.monaco ?? "plaintext";

/** Fallback stdin/stdout templates for problems without their own starter code. */
export const TEMPLATES: Record<LangId, string> = {
  python: "import sys\n\ndata = sys.stdin.read()\n# your code here\nprint(data)\n",
  javascript: "const data = require('fs').readFileSync(0, 'utf8');\n// your code here\nconsole.log(data);\n",
  java: `import java.io.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws IOException {
        String data = new String(System.in.readAllBytes());
        // your code here
        System.out.println(data);
    }
}
`,
  cpp: `#include <bits/stdc++.h>
using namespace std;

int main() {
    string data((istreambuf_iterator<char>(cin)), istreambuf_iterator<char>());
    // your code here
    cout << data << endl;
}
`,
  go: `package main

import (
\t"fmt"
\t"io"
\t"os"
)

func main() {
\tinput, _ := io.ReadAll(os.Stdin)
\t// your code here
\tfmt.Println(string(input))
}
`,
};
