"""Assess task delivery from bound, current evidence; never execute verification.

The caller supplies a TaskStore-owned snapshot and an independently observed
current input fingerprint. This module does not authenticate a dict's producer,
read artifacts, infer coverage from prose, or upgrade acceptance declarations.
"""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath, PureWindowsPath

from nailong.core.safe_files import validate_windows_path


_ACCEPTANCE_KINDS = {"static", "test", "build", "run", "review", "manual"}
_ACCEPTANCE_STATES = {"pending", "passed", "failed", "stale", "waived"}
_EVIDENCE_KINDS = {"read", "edit", "test", "build", "run", "review", "manual"}
_UNKNOWN = {"", "unknown", "missing", "unavailable", "none", "null", "n/a", "?", "未知", "未获取"}
_SUPPORT_KINDS = {
    # Reading or writing a file establishes an operation, not an acceptance verdict.
    "static": {"review", "manual"},
    "test": {"test"},
    "build": {"build"},
    "run": {"run"},
    "review": {"review"},
    "manual": {"manual"},
}


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _known(value: object) -> bool:
    return _text(value) and value == value.strip() and value.casefold() not in _UNKNOWN


def _revision(value: object) -> bool:
    return type(value) is int and value >= 1


def _paths(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    result = []
    for path in value:
        if not _text(path) or "\0" in path:
            return None
        posix, windows = PurePosixPath(path), PureWindowsPath(path)
        if posix.is_absolute() or windows.drive or ".." in posix.parts or ".." in windows.parts:
            return None
        normalized = str(posix)
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _covers(parent: str, child: str) -> bool:
    return parent == "." or parent == child or child.startswith(parent + "/")


def _applicable(paths: tuple[str, ...], scope: tuple[str, ...]) -> bool:
    if not scope:
        return not paths
    return bool(paths) and all(
        any(_covers(target, path) or _covers(path, target) for target in scope)
        for path in paths
    )


def _identity_matches(evidence: dict, snapshot: dict) -> bool:
    # Evidence v1 is already bound by its containing TaskStore. Check explicit
    # identities too when a producer includes them, without inventing new fields.
    return all(key not in evidence or evidence[key] == snapshot[key]
               for key in ("task_id", "thread_id", "project_root"))


def _coverage_group(report: dict, *, configured: bool) -> dict:
    """Separate the TaskStore-reserved verify:* criteria from functional ones."""
    group = {"status": "unverified", "satisfied": [], "pending": [], "failed": [], "stale": [], "waived": []}
    required = {key: [] for key in ("satisfied", "pending", "failed", "stale", "waived")}
    for key in required:
        for row in report[key]:
            if row["id"].startswith("verify:") != configured:
                continue
            group[key].append(row["id"])
            if row["required"]:
                required[key].append(row)
    if required["failed"]:
        group["status"] = "failed"
    elif required["pending"] or required["stale"]:
        pass
    elif required["satisfied"]:
        group["status"] = ("verified" if any(row["kind"] in {"test", "build", "run", "manual"}
                                             for row in required["satisfied"]) else "reviewed")
    return group


def _unfinished_steps(value: object) -> list[dict]:
    if not isinstance(value, list):
        return [{"id": "invalid-steps", "title": "步骤列表无效", "state": "unknown", "dependencies": []}]
    ids = [row["id"] for row in value if isinstance(row, dict) and _text(row.get("id"))]
    duplicates = {key for key, count in Counter(ids).items() if count > 1}
    result = []
    for index, row in enumerate(value, 1):
        if (not isinstance(row, dict) or not _text(row.get("id")) or row["id"] in duplicates
                or not _text(row.get("title")) or row.get("state") not in ("todo", "doing", "done", "blocked")
                or not isinstance(row.get("dependencies"), list)
                or not all(_text(item) and item in ids and item != row["id"] for item in row["dependencies"])):
            result.append({"id": f"invalid-step-{index}", "title": "步骤或依赖无效",
                           "state": "unknown", "dependencies": []})
        elif row["state"] != "done":
            result.append({"id": row["id"], "title": row["title"], "state": row["state"],
                           "dependencies": list(row["dependencies"])})
    return result


def _assess_acceptance(item: dict, snapshot: dict, index: dict, duplicates: set,
                       scope: tuple[str, ...], fingerprint: str | None) -> dict:
    row = {"id": item["id"], "description": item["description"], "kind": item["kind"],
           "required": item["required"], "status": "pending", "evidence_ids": [], "reasons": []}
    ids = item.get("evidence_ids")
    if not isinstance(ids, list) or not all(_text(identifier) for identifier in ids):
        row["reasons"].append("验收证据引用无效。")
        return row
    passed, failed, stale, notes = [], [], [], []
    covered = set()
    for identifier in dict.fromkeys(ids):
        evidence = index.get(identifier)
        if identifier in duplicates or not isinstance(evidence, dict):
            notes.append(f"证据 {identifier} 缺失或 ID 不唯一。")
            continue
        if evidence.get("source") not in ("runtime", "user"):
            notes.append(f"证据 {identifier} 不来自可信运行时或用户。")
            continue
        if not _identity_matches(evidence, snapshot):
            notes.append(f"证据 {identifier} 不属于当前任务身份。")
            continue
        kind = evidence.get("kind")
        expected = {"manual"} if item["status"] == "waived" else _SUPPORT_KINDS[item["kind"]]
        if not isinstance(kind, str) or kind not in _EVIDENCE_KINDS or kind not in expected:
            notes.append(f"证据 {identifier} 的操作类型不能支撑此验收。")
            continue
        if item["status"] == "waived" and evidence.get("source") != "user":
            notes.append(f"证据 {identifier} 不能代替用户免除验收的决定。")
            continue
        revision = evidence.get("task_revision")
        if not _revision(revision):
            notes.append(f"证据 {identifier} 没有有效需求版本。")
            continue
        if revision != snapshot["revision"]:
            stale.append(identifier)
            notes.append(f"证据 {identifier} 的需求版本已不适用。")
            continue
        status = evidence.get("status")
        if status in ("denied", "interrupted"):
            # A refusal is reportable even when no input snapshot was collected;
            # it establishes non-execution, never a successful acceptance.
            notes.append(f"证据 {identifier} 对应操作被拒绝或中断，未完成验证。")
            continue
        observed = evidence.get("input_fingerprint")
        if not _known(fingerprint) or not _known(observed):
            notes.append(f"证据 {identifier} 或当前输入的指纹未知，无法确认有效性。")
            continue
        if observed != fingerprint:
            stale.append(identifier)
            notes.append(f"证据 {identifier} 的输入版本已过期。")
            continue
        paths = _paths(evidence.get("paths"))
        if paths is None or not _applicable(paths, scope):
            notes.append(f"证据 {identifier} 的路径无效或不适用于当前范围。")
            continue
        coverage = evidence.get("coverage")
        summary = evidence.get("summary")
        if not _text(summary) or summary.strip().casefold() in _UNKNOWN:
            notes.append(f"证据 {identifier} 缺少明确摘要。")
            continue
        # A concrete failure in a known subset is still a failure. Success
        # requires complete coverage, so the asymmetry never promotes a pass.
        if status == "failed" and coverage in ("complete", "partial"):
            failed.append(identifier)
        elif status == "passed" and coverage == "complete":
            passed.append(identifier)
            covered.update(paths)
        elif status == "failed":
            notes.append(f"证据 {identifier} 记录了执行失败，但覆盖未知，不能确定此验收的失败范围。")
        else:
            notes.append(f"证据 {identifier} 没有完整通过及覆盖证据。")
    complete = bool(passed) and all(any(_covers(path, target) for path in covered) for target in scope)
    if failed:
        row.update(status="failed", evidence_ids=failed)
        row["reasons"].append("存在当前版本的可信失败；成功执行不能抵消关联的失败证据。")
    elif item["status"] == "stale":
        row.update(status="stale", evidence_ids=stale)
        row["reasons"].append("验收仍标记为过期，需由状态所有者重新核对。")
    elif item["status"] in ("passed", "waived") and complete:
        row.update(status="waived" if item["status"] == "waived" else "passed", evidence_ids=passed)
        if item["status"] == "waived":
            row["reasons"].append("用户明确免除此验收；不计为验证通过。")
    elif stale and not passed:
        row.update(status="stale", evidence_ids=stale)
        row["reasons"].append("已有证据已过期，没有可用的当前证据。")
    else:
        if passed and not complete:
            row["reasons"].append("操作虽已通过，但关联证据未完整覆盖任务范围。")
        if item["status"] in ("pending", "failed"):
            row["reasons"].append("验收尚未确认通过，不能依据成功执行自动升级。")
        if not ids:
            row["reasons"].append("没有关联的验收证据。")
    row["reasons"].extend(notes)
    return row


def build_delivery_report(snapshot: dict | None, *, current_input_fingerprint: str | None = None) -> dict:
    """Return a JSON-compatible assessment without changing the task snapshot.

    Required acceptance controls the overall verdict. Optional unresolved items
    remain visible. A passed static/review-only task is ``reviewed``; ``verified``
    requires a satisfied functional test/build/run/manual criterion as well.
    TaskStore's reserved verify:* criteria establish configured execution, not
    functional goal coverage. Waivers never count as proof verification ran.
    """
    report = {"schema_version": 1, "status": "unverified", "satisfied": [], "pending": [],
              "failed": [], "stale": [], "waived": [], "reasons": [],
              "pending_verification": [], "blockers": [], "incomplete_steps": [],
              "configured_verification": {"status": "unverified"},
              "goal_coverage": {"status": "unverified"}}
    if not isinstance(snapshot, dict):
        report["reasons"].append("没有当前任务快照，无法判定验收完成。")
        return report
    if type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
        report["reasons"].append("任务快照版本无效或不支持。")
        return report
    if not all(_text(snapshot.get(key)) for key in ("task_id", "thread_id", "project_root")):
        report["reasons"].append("任务、会话或项目身份缺失。")
        return report
    root_value = snapshot["project_root"]
    root = PurePosixPath(root_value)
    if PureWindowsPath(root_value).drive or root_value.startswith(('\\', '//')):
        try:
            validate_windows_path(root_value)
            valid_root = True
        except ValueError:
            valid_root = False
    else:
        valid_root = root.is_absolute() and '\0' not in root_value and '..' not in root.parts
    if not valid_root or not _revision(snapshot.get("revision")):
        report["reasons"].append("项目根目录或需求版本无效。")
        return report
    report.update(task_id=snapshot["task_id"], thread_id=snapshot["thread_id"],
                  project_root=snapshot["project_root"], revision=snapshot["revision"])
    scope, changed = _paths(snapshot.get("scope")), _paths(snapshot.get("changed_paths", []))
    acceptance, evidence = snapshot.get("acceptance"), snapshot.get("evidence")
    lifecycle = snapshot.get("lifecycle")
    if (scope is None or changed is None or not isinstance(acceptance, list)
            or not isinstance(evidence, list) or lifecycle not in ("active", "paused", "blocked", "completed")):
        report["reasons"].append("任务范围、验收、证据或生命周期数据无效。")
        return report
    if any(not any(_covers(path, change) for path in scope) for change in changed):
        report["reasons"].append("存在任务范围外的修改路径，不能确认交付覆盖。")
        return report
    # Including concrete changed paths preserves them in the coverage check;
    # a directory declaration must not be satisfied merely by reading one file.
    applicable_scope = tuple(dict.fromkeys((*scope, *changed)))
    report["lifecycle"] = lifecycle
    report["incomplete_steps"] = _unfinished_steps(snapshot.get("steps", []))
    for field in ("pending_verification", "blockers"):
        values = snapshot.get(field, [])
        if not isinstance(values, list) or not all(_text(value) for value in values):
            report["reasons"].append(f"{field} 数据无效，无法确认交付。")
            return report
        report[field] = list(values)
    evidence_ids = [item["id"] for item in evidence if isinstance(item, dict) and _text(item.get("id"))]
    duplicate_evidence = {key for key, count in Counter(evidence_ids).items() if count > 1}
    evidence_index = {item["id"]: item for item in evidence if isinstance(item, dict) and _text(item.get("id"))}
    acceptance_ids = [item["id"] for item in acceptance if isinstance(item, dict) and _text(item.get("id"))]
    duplicate_acceptance = {key for key, count in Counter(acceptance_ids).items() if count > 1}
    for position, item in enumerate(acceptance, 1):
        if (not isinstance(item, dict) or not _text(item.get("id")) or item["id"] in duplicate_acceptance
                or not _text(item.get("description")) or not isinstance(item.get("kind"), str)
                or item["kind"] not in _ACCEPTANCE_KINDS or type(item.get("required")) is not bool
                or not isinstance(item.get("status"), str) or item["status"] not in _ACCEPTANCE_STATES):
            report["pending"].append({"id": f"invalid-{position}", "description": "验收项无效或 ID 不唯一",
                                      "kind": "unknown", "required": True, "status": "pending",
                                      "evidence_ids": [], "reasons": ["需修复验收契约，不能推断完成。"]})
            continue
        row = _assess_acceptance(item, snapshot, evidence_index, duplicate_evidence,
                                 applicable_scope, current_input_fingerprint)
        bucket = {"passed": "satisfied", "waived": "waived"}.get(row["status"], row["status"])
        report[bucket].append(row)
    report["configured_verification"] = _coverage_group(report, configured=True)
    report["goal_coverage"] = _coverage_group(report, configured=False)
    required_pending = any(row["required"] for name in ("pending", "stale") for row in report[name])
    required_failed = any(row["required"] for row in report["failed"])
    goal_status = report["goal_coverage"]["status"]
    if (lifecycle == "blocked" or report["blockers"]
            or any(row["state"] == "blocked" for row in report["incomplete_steps"])):
        report["status"] = "blocked"
        report["reasons"].append("当前任务存在明确阻塞；保留已满足与未满足的验收信息。")
    elif required_failed:
        report["status"] = "failed"
        report["reasons"].append("必需验收存在当前可信失败。")
    elif required_pending:
        report["reasons"].append("必需验收仍待确认或证据过期。")
    elif report["incomplete_steps"]:
        report["reasons"].append("任务还有未完成或无效的步骤，不能宣称目标已交付完成。")
    elif goal_status in {"verified", "reviewed"}:
        if goal_status == "verified" and report["pending_verification"]:
            report["reasons"].append("仍有待验证说明，需由状态所有者核对后才能宣称功能目标已验证。")
        else:
            report["status"] = goal_status
            report["reasons"].append("用户目标的必需验收有当前、完整且类型匹配的可信证据。")
        if report["status"] == "reviewed":
            report["reasons"].append("目标仅经过静态审查；配置流程的实际执行结果另列。")
    else:
        report["reasons"].append("没有以可信证据满足的用户目标必需验收；配置流程通过不能代替功能覆盖。")
    if any(not row["required"] for name in ("pending", "failed", "stale") for row in report[name]):
        report["reasons"].append("存在未满足的可选验收；整体状态仅针对必需验收。")
    if any(row.get('source') == 'runtime' and row.get('kind') == 'edit'
            and row.get('status') == 'passed' and row.get('outside_project_scope') is True
            for row in evidence if isinstance(row, dict)):
        report['reasons'].append('本任务包含项目验证范围外的文件修改；项目内验证不能证明完整交付。')
        if report['status'] in {'verified', 'reviewed'}:
            report['status'] = 'unverified'
        report['goal_coverage']['status'] = 'unverified'
    return report


def _display(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join("".join(character if ord(character) >= 32 and ord(character) != 127 else " "
                            for character in value).split())


def render_delivery_report(report: dict) -> str:
    """Render an assessment produced by build_delivery_report as plain text.

    Render only acceptance and generated reasons, never raw command output or
    evidence summaries. Normal session/API-key redaction still belongs to the
    caller; rendering an arbitrary external dict does not authenticate it.
    """
    if not isinstance(report, dict):
        return "交付状态：未验证\n没有有效交付报告。"
    labels = {"verified": "已验证（用户目标必需验收）", "unverified": "未验证", "failed": "验收失败",
              "blocked": "受阻", "reviewed": "已静态审查"}
    status = report.get("status")
    label = labels.get(status, "未验证") if isinstance(status, str) else "未验证"
    lines = ["交付状态：" + label]
    for key, title in (("configured_verification", "配置验证流程"), ("goal_coverage", "用户目标覆盖")):
        group = report.get(key)
        if isinstance(group, dict):
            state = group.get("status")
            group_label = labels.get(state, "未验证") if isinstance(state, str) else "未验证"
            if key == "configured_verification" and state == "verified":
                group_label = "配置执行条件已通过（不代表功能覆盖）"
            lines.append(title + "：" + group_label)
    for key, title in (("satisfied", "已满足"), ("pending", "待确认"), ("failed", "失败"),
                       ("stale", "过期"), ("waived", "用户免除（不计为验证通过）")):
        rows = report.get(key, [])
        if not isinstance(rows, list) or not rows:
            continue
        lines.append(title + "：")
        for row in rows:
            if not isinstance(row, dict):
                lines.append("- 无效验收记录。")
                continue
            identifier = _display(row.get("id"))
            description = _display(row.get("description")) or "描述未知"
            optional = "（可选）" if row.get("required") is False else ""
            lines.append(f"- {identifier} {description}{optional}".strip())
            reasons = row.get("reasons", [])
            if isinstance(reasons, list):
                lines.extend("  " + text for reason in reasons if (text := _display(reason)))
    steps = report.get("incomplete_steps", [])
    if isinstance(steps, list) and steps:
        lines.append("未完成步骤：")
        for row in steps:
            if not isinstance(row, dict):
                lines.append("- 无效步骤记录。")
                continue
            identifier = _display(row.get("id"))
            title = _display(row.get("title")) or "标题未知"
            state = _display(row.get("state")) or "状态未知"
            lines.append(f"- {identifier} {title}（{state}）")
            dependencies = row.get("dependencies", [])
            if isinstance(dependencies, list) and dependencies:
                lines.append("  依赖：" + "、".join(_display(item) for item in dependencies))
    for key, title in (("reasons", "说明"), ("pending_verification", "待验证说明"), ("blockers", "阻塞")):
        values = report.get(key, [])
        if isinstance(values, list):
            lines.extend(title + "：" + text for value in values if (text := _display(value)))
    return "\n".join(lines)
