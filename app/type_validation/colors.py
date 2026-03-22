from typing import Match, Optional

from config import ERROR_CODES, RE_ERROR_CODE, RE_MYPY_ERR_FORMAT_HEADER


# ANSI color codes for terminal output
class Colors:
    """ANSI color codes for beautiful terminal output."""

    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def red(cls, text: str) -> str:
        return f"{cls.RED}{text}{cls.RESET}"

    @classmethod
    def yellow(cls, text: str) -> str:
        return f"{cls.YELLOW}{text}{cls.RESET}"

    @classmethod
    def blue(cls, text: str) -> str:
        return f"{cls.BLUE}{text}{cls.RESET}"

    @classmethod
    def cyan(cls, text: str) -> str:
        return f"{cls.CYAN}{text}{cls.RESET}"

    @classmethod
    def bold(cls, text: str) -> str:
        return f"{cls.BOLD}{text}{cls.RESET}"


def colorize_mypy_line(line: str) -> str:
    """
    Add beautiful colors to a mypy error line.

    This function takes a plain mypy error line and transforms it into a
    colorized version that's much easier to read and visually parse.
    """
    match: Optional[Match[str]] = RE_MYPY_ERR_FORMAT_HEADER.match(line)
    if not match:
        return line

    file_path = match.group(1)
    line_number = match.group(2)
    error_type = match.group(3)
    message = match.group(4)

    if error_type == "error":
        colored_error_type = Colors.red(error_type)
    elif error_type == "warning":
        colored_error_type = Colors.yellow(error_type)
    elif error_type == "note":
        colored_error_type = Colors.blue(error_type)
    else:
        colored_error_type = error_type

    # Check if there's an error code
    error_code_match = RE_ERROR_CODE.search(message)
    if error_code_match:
        error_code = error_code_match.group(1)
        # Remove the error code from the message and color it separately
        message_without_code = message[: error_code_match.start()].rstrip()
        colored_error_code = Colors.red(error_code)
        colored_message = f"{message_without_code} {colored_error_code}"
    else:
        colored_message = message

    # Format: bold_file:line_number: colored_error_type: message [red_error_code]
    colored_line = (
        f"{Colors.bold(file_path)}:{Colors.cyan(line_number)}: "
        f"{colored_error_type}: {colored_message}"
    )

    return colored_line


def colorize_dynamic_error(line: str) -> str:
    """Colorize dynamic access error line similar to mypy errors."""
    # Parse the error format: "file.py:line: dynamic-check: message [dynamic-access]"
    parts = line.split(":")
    if len(parts) >= 4:
        file_part = parts[0]
        line_part = parts[1]
        error_type = parts[2]
        message = parts[3]

        # Extract error code if present
        error_code = ERROR_CODES.DYNAMIC.value
        colored_message = f"{message} {Colors.red(error_code)}"

        # Format similar to mypy
        colored_line = (
            f"{Colors.bold(file_part)}:{Colors.cyan(line_part)}: "
            f"{Colors.yellow('dynamic-check')}: {colored_message}"
        )
        return colored_line

    return line
