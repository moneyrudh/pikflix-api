#!/usr/bin/env python3
import io
import subprocess
import sys
from re import Match
from subprocess import CompletedProcess
from typing import List, Optional, Set, Tuple

from colors import colorize_mypy_line
from config import RE_GIT_DIFF_HEADER, RE_MYPY_ERR_FORMAT_HEADER
from dynamic_parser import analyze_dynamic_access_in_file


def get_changed_lines(file_path: str) -> Set[int]:
    """Get line numbers that have been changed in the staged version of a file."""
    try:
        # Get the diff for staged changes
        result = subprocess.run(
            ["git", "diff", "--cached", "-U0", "--", file_path],
            capture_output=True,
            text=True,
            check=True,
        )

        changed_lines: Set[int] = set()
        for line in io.StringIO(result.stdout):
            # Look for lines like "@@ -10,5 +10,6 @@" which show line ranges
            match = RE_GIT_DIFF_HEADER.match(line.strip())
            if match:
                start_line = int(match.group(1))
                line_count = int(match.group(2)) if match.group(2) else 1
                for i in range(start_line, start_line + line_count):
                    changed_lines.add(i)

        return changed_lines
    except subprocess.CalledProcessError:
        print("A subprocess error was encountered. Retry pre-commit.")
        sys.exit(1)


def filter_mypy_output(
    mypy_output: str,
    all_changed_lines: Set[Tuple[str, int]],
) -> Tuple[List[str], Set[Tuple[str, int]]]:
    """Filter mypy output to only include errors on changed lines. Returns errors and lines with mypy errors."""
    filtered_errors = []
    lines_with_mypy_errors: Set[Tuple[str, int]] = set()

    for line in io.StringIO(mypy_output):
        line = line.strip()
        if not line:
            continue

        # Parse mypy error format: "file.py:line: error: message"
        match: Optional[Match[str]] = RE_MYPY_ERR_FORMAT_HEADER.match(line)
        if match:
            error_file = match.group(1)
            error_line = int(match.group(2))

            # Only include errors on changed lines
            if (error_file, error_line) in all_changed_lines:
                # Apply beautiful coloring to the error line
                colored_line = colorize_mypy_line(line)
                filtered_errors.append(colored_line)
                lines_with_mypy_errors.add((error_file, error_line))

    return filtered_errors, lines_with_mypy_errors


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage (internal): type_validator.py <python_files...>")
        sys.exit(1)

    files_to_check: List[str] = sys.argv[1:]

    # Run mypy on staged files
    try:
        result: CompletedProcess[str] = subprocess.run(
            ["mypy", "--config-file=mypy.ini"] + files_to_check,
            capture_output=True,
            text=True,
        )
        mypy_output: str = result.stdout + result.stderr
    except subprocess.CalledProcessError:
        print("A subprocess error was encountered. Retry pre-commit.")
        sys.exit(1)

    all_filtered_errors: List[str] = []

    # Collect all changed lines from all files
    all_changed_lines: Set[Tuple[str, int]] = set()
    file_changed_lines: dict[str, Set[int]] = {}

    for file_path in files_to_check:
        changed_lines = get_changed_lines(file_path)
        file_changed_lines[file_path] = changed_lines
        # Add to global set with file path
        for line_num in changed_lines:
            all_changed_lines.add((file_path, line_num))

    if all_changed_lines:
        # First, get mypy errors and track which lines have them
        filtered_errors, lines_with_mypy_errors = filter_mypy_output(
            mypy_output,
            all_changed_lines,
        )
        all_filtered_errors.extend(filtered_errors)

        # Then, check for dynamic access errors using file-level parsing
        for file_path in files_to_check:
            if file_path in file_changed_lines:
                changed_lines_for_file = file_changed_lines[file_path]

                # Filter out lines that already have mypy errors
                lines_to_check = set()
                for line_num in changed_lines_for_file:
                    if (file_path, line_num) not in lines_with_mypy_errors:
                        lines_to_check.add(line_num)

                if lines_to_check:
                    dynamic_errors = analyze_dynamic_access_in_file(
                        file_path,
                        lines_to_check,
                    )
                    all_filtered_errors.extend(dynamic_errors)

    if all_filtered_errors:
        for error in all_filtered_errors:
            print(error)
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
