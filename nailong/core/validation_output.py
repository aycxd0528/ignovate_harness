"""Conservative command-output observations; never test execution or acceptance proof."""

import re


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_UNITTEST_RUN = re.compile(r"Ran (\d+) tests? in \d+(?:\.\d+)?s")
_UNITTEST_RESULT = re.compile(r"(OK|FAILED)(?: \(([^\r\n]+)\))?")
_UNITTEST_COUNT = re.compile(r"(failures|errors|skipped|expected failures|unexpected successes)=(\d+)")
_PYTEST_RESULT = re.compile(r"(.+) in \d+(?:\.\d+)?s(?: \(\d+:\d{2}:\d{2}\))?")
_PYTEST_COUNT = re.compile(r"(\d+) (failed|passed|errors?|skipped|deselected|xfailed|xpassed|warnings?)")
_PYTEST_PROGRAM = r"(?:python -m pytest|pytest|py\.test)"
_PYTEST_INVOCATION_ERROR = re.compile(
    rf"(?:ERROR: usage: {_PYTEST_PROGRAM} \[options\](?: .*)?|{_PYTEST_PROGRAM}: error: .+)")


def _counts(text, pattern, *, number_first=False):
    counts = {}
    for part in text.split(", ") if text else ():
        match = pattern.fullmatch(part)
        if not match:
            return None
        label, number = match.groups()[::-1] if number_first else match.groups()
        if label in counts:
            return None
        counts[label] = int(number)
    return counts


def _output_lines(output):
    # A Markdown example is still documentation when printed by a command.
    fence = None
    for line in _ANSI.sub("", output).splitlines():
        marker = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
        if marker:
            kind = marker[1][0]
            fence = None if fence == kind else kind if fence is None else fence
            yield "[documentation fence]"
        else:
            yield "[documentation]" if fence else line.rstrip()


def command_test_output(name: str, payload: dict) -> dict | None:
    """Describe standard terminal summaries without attesting how they were produced.

    Even an exact runner summary can be echoed or read from a saved log. A
    reported pass must never advance acceptance or prove tests ran on a version.
    Truncation here concerns the source payload, not a later UI preview.
    """
    if (name != "run_command" or not isinstance(payload, dict)
            or not isinstance(payload.get("output"), str)
            or type(payload.get("exit_code")) is not int or payload.get("started") is False):
        return None
    lines = list(_output_lines(payload["output"]))
    summaries = []
    incomplete = False
    invocation_error = False
    for index, line in enumerate(lines):
        if _PYTEST_INVOCATION_ERROR.fullmatch(line):
            invocation_error = True
            incomplete = True
            continue
        run = _UNITTEST_RUN.fullmatch(line)
        if run:
            following = index + 1
            while following < len(lines) and not lines[following].strip():
                following += 1
            result = _UNITTEST_RESULT.fullmatch(lines[following]) if following < len(lines) else None
            counts = _counts(result[2], _UNITTEST_COUNT) if result else None
            if result and counts is not None and (result[1] == "OK" or counts):
                tests = int(run[1])
                status = "failed" if result[1] == "FAILED" else "zero_tests" if tests == 0 else "passed"
                summaries.append({"framework": "unittest", "status": status,
                                  "tests_run": tests, "counts": counts})
            else:
                incomplete = True
            continue
        banner = re.fullmatch(r"=+ (.+) =+", line)
        result = _PYTEST_RESULT.fullmatch(banner[1] if banner else line)
        if not result:
            continue
        body = result[1]
        counts = {} if body == "no tests ran" else _counts(body, _PYTEST_COUNT, number_first=True)
        if counts is None:
            continue
        failed = any(counts.get(label, 0) for label in ("failed", "error", "errors"))
        status = ("failed" if failed else "zero_tests" if body == "no tests ran"
                  else "passed" if counts.get("passed", 0) else "unknown")
        summaries.append({"framework": "pytest", "status": status, "counts": counts})
    truncated = any(payload.get(key) for key in ("output_truncated", "result_truncated", "truncated"))
    failure = any(row["status"] == "failed" for row in summaries)
    zero_tests = any(row["status"] == "zero_tests" for row in summaries)
    unambiguous_pass = (len(summaries) == 1 and summaries[0]["status"] == "passed"
                        and not truncated and not incomplete
                        and payload.get("ok") is True and payload["exit_code"] == 0
                        and not payload.get("timed_out") and not payload.get("cancelled"))
    return {"source": "run_command.output", "execution_verified": False,
            "status": "reported_failure" if failure else "zero_tests" if zero_tests
                      else "reported_pass" if unambiguous_pass else "unknown",
            "failure_observed": failure, "zero_tests_observed": zero_tests,
            "invocation_error_observed": invocation_error,
            "summary_count": len(summaries), "ambiguous": len(summaries) > 1 or incomplete,
            "incomplete": incomplete,
            "truncated": bool(truncated), "summaries": summaries[-8:]}
