"""Diagnose the TSG PM ontology and write a repair report (NeOn-GPT style validate → diagnose → repair loop).

Stages, each gating the next the way NeOn-GPT chains RDFLib → HermiT → OOPS!:
  1 语法       YAML parsing and the Pydantic metadata schema         (compiler phase 1)
  2 一致性     references, link keys vs cardinality, version registry, and — with --database —
               the data itself: primary keys, enum values, value types, dangling joins, policy invariants
  3 结构缺陷   modelling pitfalls from lint_ontology.py, graded critical / important / minor

The Markdown report is meant to be read by Claude and acted on: fix critical, then important,
re-run, and only touch minor findings when the fix cannot break consistency. Reports are kept in
<repo>/.ontology-diagnostics/ so each run is compared with the previous one (resolved / new / persisting).

Run from TSG-MVP/backend:
    .venv/bin/python ../.claude/skills/tsg-ontology/scripts/diagnose_ontology.py [--database data/xtcrm.sqlite3]
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path.cwd()))
try:
    from app.ontology.compiler import (
        DEFAULT_METADATA_DIR, OntologyMetadataError, checksum, compile_documents, load_documents,
    )
except ModuleNotFoundError:
    sys.exit("Run this script from TSG-MVP/backend with its virtualenv (.venv/bin/python).")

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[3]
SEVERITY_ORDER = {"critical": 0, "important": 1, "minor": 2}
SEVERITY_LABEL = {"critical": "严重", "important": "重要", "minor": "次要"}
STAGE_LABEL = {"syntax": "1 语法", "consistency": "2 一致性", "pitfall": "3 结构缺陷", "build": "4 构建包"}

# lint_ontology categories → severity. Unsafe exposure is critical; anything that changes what
# a reader or the runtime believes is important; documentation gaps are minor.
PITFALL_SEVERITY = {"安全": "critical", "写入": "important", "校验": "important", "关系": "important",
                    "命名": "important", "口径": "important", "类型或实例": "important", "溯源": "minor"}
MINOR_PITFALL_MESSAGES = ("孤立对象",)
# A link whose description contains this marker states that unmatched keys are expected.
DANGLING_DECLARATION = "允许悬空"


@dataclass
class Finding:
    stage: str
    severity: str
    code: str
    location: str
    problem: str
    why: str = ""
    fix: str = ""
    evidence: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return f"{self.code}|{self.location}"


# ----------------------------------------------------------------- stage 1 + 2a
def check_metadata(metadata_dir: Path) -> tuple[list[Finding], Any]:
    findings: list[Finding] = []
    try:
        documents = load_documents(metadata_dir)
    except OntologyMetadataError as exc:
        for error in exc.errors:
            findings.append(Finding(
                "syntax", "critical", "SYN-SCHEMA", error.split(":", 1)[0], error,
                "元数据无法解析时服务拒绝启动，后续检查全部无法进行。",
                "按报错位置修正 YAML 或字段名。行内映射里的说明文字不要用半角逗号；字段名以 metadata_schema.py 为准。"))
        return findings, None
    try:
        ontology = compile_documents(documents)
    except OntologyMetadataError as exc:
        for error in exc.errors:
            findings.append(Finding(
                "consistency", "critical", "REF-UNRESOLVED", error.split(":", 1)[0], error,
                "引用了不存在的对象、属性、值类型、权限或规则，本体无法编译。",
                "先确认被引用的东西是否应该存在：应该存在就补上定义或在 rules.py 注册，不应该存在就改掉引用。"))
        return findings, None
    findings.extend(check_link_cardinality(ontology))
    return findings, (ontology, checksum(documents))


def check_link_cardinality(ontology: Any) -> list[Finding]:
    """A join's keys must agree with its cardinality: the 'one' side is joined on its primary key."""
    findings = []
    types = {t.api_name: t for t in ontology.object_types}
    for link in ontology.link_types:
        if not link.source_key or not link.target_key:
            continue
        source, target = types[link.source_type], types[link.target_type]
        source_is_pk = link.source_key == source.primary_key
        target_is_pk = link.target_key == target.primary_key
        expected = {
            "one_to_many": source_is_pk, "many_to_one": target_is_pk,
            "one_to_one": source_is_pk or target_is_pk, "many_to_many": True,
        }[link.cardinality]
        if not expected:
            findings.append(Finding(
                "consistency", "important", "CON-CARDINALITY",
                f"{link.source_type}.{link.api_name} → {link.target_type}",
                f"基数声明为 {link.cardinality}，但连接键是 {link.source_key} → {link.target_key}，"
                "“一”的那一侧没有用主键连接。",
                "基数与连接键矛盾时，关系遍历会返回多于声明数量的对象，界面和函数会按错误的基数处理。",
                "要么改基数，要么改连接键，使“一”的一侧用主键；拿不准方向时，正反各读一遍关系名。"))
    return findings


def check_version_registry(ontology: Any, digest: str, database: Path | None) -> list[Finding]:
    if database is None:
        return []
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        try:
            rows = connection.execute("SELECT version, checksum FROM ontology_versions").fetchall()
        except sqlite3.OperationalError:
            return []
    registered = dict(rows)
    if ontology.version in registered and registered[ontology.version] != digest:
        return [Finding(
            "consistency", "critical", "CON-VERSION", f"ontology.yaml version {ontology.version}",
            "元数据内容变了，但版本号没有升：数据库里登记的同一版本指纹不同。",
            "服务启动时会拒绝这种情况；即使能启动，也无法从版本差异里看到这次改动。",
            "按改动程度升版本号：破坏性改动升主版本，新增升次版本，只改说明升修订号。")]
    newer = [v for v in registered if _semver(v) > _semver(ontology.version)]
    if newer:
        return [Finding(
            "consistency", "critical", "CON-VERSION", f"ontology.yaml version {ontology.version}",
            f"版本号 {ontology.version} 低于已登记的 {max(newer, key=_semver)}。",
            "版本号必须递增，否则服务拒绝启动。", "把版本号改为高于已登记的最高版本。")]
    return []


def _semver(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _semver_or_none(value: str) -> tuple[int, ...] | None:
    try:
        return _semver(value)
    except ValueError:
        return None


# --------------------------------------------------------------------- stage 2b
def check_data(ontology: Any, database: Path) -> list[Finding]:
    """Load every object type from a copy of the database and check it against the model (ABox vs TBox)."""
    from app.config import Settings
    from app.db import Database
    from app.ontology.runtime import OntologyService
    from app.project_repository import ProjectRepository
    from app.repository import SnapshotRepository

    findings: list[Finding] = []
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / database.name
        for suffix in ("", "-wal", "-shm"):
            if Path(f"{database}{suffix}").exists():
                shutil.copy(f"{database}{suffix}", f"{copy}{suffix}")
        db = Database(copy)
        db.initialize()
        settings = Settings.from_env()
        projects = ProjectRepository(db, settings.timezone, require_crm_project_match=settings.require_crm_project_match)
        service = OntologyService(ontology, db, projects, SnapshotRepository(db))
        ctx = service.context({"username": "diagnose", "is_admin": True, "permissions": []})

        objects: dict[str, list[dict[str, Any]]] = {}
        for object_type in ontology.object_types:
            try:
                objects[object_type.api_name] = ctx.objects(object_type.api_name)
            except Exception as exc:  # a loader that crashes is itself the finding
                findings.append(Finding(
                    "consistency", "critical", "DAT-LOADER", object_type.api_name,
                    f"加载对象时出错：{type(exc).__name__}: {exc}",
                    "加载器出错时，这类对象在页面、对象集和 PM Assistant 中全部不可用。",
                    f"检查 rules.py 中 datasource {object_type.datasource!r} 与当前表结构是否一致。"))
        for object_type in ontology.object_types:
            rows = objects.get(object_type.api_name)
            if rows is not None:
                findings.extend(_check_rows(object_type, rows))
        findings.extend(_check_joins(ontology, objects))
        findings.extend(_check_policy(ontology, ctx, objects))
    return findings


def _check_rows(object_type: Any, rows: list[dict[str, Any]]) -> list[Finding]:
    findings = []
    keys = Counter(str(row.get(object_type.primary_key)) for row in rows)
    duplicates = [key for key, count in keys.items() if count > 1]
    if duplicates:
        findings.append(Finding(
            "consistency", "critical", "DAT-PRIMARY-KEY", object_type.api_name,
            f"主键 {object_type.primary_key} 有 {len(duplicates)} 个重复值。",
            "主键重复时，按主键读取、关系遍历和动作写回都会命中错误的对象。",
            "检查加载器的聚合键；必要时换用真正唯一的字段做主键。", duplicates[:5]))
    for prop in object_type.properties:
        allowed = {value for value, _ in prop.enum_values}
        bad_enum, bad_type = [], []
        for row in rows:
            value = row.get(prop.api_name)
            if value is None:
                continue
            if allowed and value not in allowed:
                bad_enum.append(f"{row.get(object_type.primary_key)}: {value!r}")
            elif not _matches_type(prop.data_type, value):
                bad_type.append(f"{row.get(object_type.primary_key)}: {value!r}")
        where = f"{object_type.api_name}.{prop.api_name}"
        if bad_enum:
            findings.append(Finding(
                "consistency", "important", "DAT-ENUM", where,
                f"{len(bad_enum)} 个对象的取值不在值类型 {sorted(allowed)} 中。",
                "界面无法显示中文标签，按枚举过滤会漏掉这些对象。",
                "要么把新取值加入值类型（并确认业务含义），要么在加载器里把来源值映射到已有取值。", bad_enum[:5]))
        if bad_type:
            findings.append(Finding(
                "consistency", "important", "DAT-TYPE", where,
                f"{len(bad_type)} 个对象的取值不符合声明的类型 {prop.data_type}。",
                "对象集的比较、排序和聚合会出错或静默跳过这些对象。",
                "修正加载器的格式化，或把属性类型改成与真实数据一致。", bad_type[:5]))
    return findings


def _matches_type(data_type: str, value: Any) -> bool:
    try:
        if data_type == "date":
            date.fromisoformat(str(value))
        elif data_type == "datetime":
            datetime.fromisoformat(str(value))
        elif data_type in {"decimal", "money", "percent"}:
            return not isinstance(value, bool) and Decimal(str(value)).is_finite()
        elif data_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        elif data_type == "boolean":
            return isinstance(value, bool)
        elif data_type in {"string", "enum", "object_ref"}:
            return isinstance(value, str)
    except (ValueError, InvalidOperation):
        return False
    return True


def _check_joins(ontology: Any, objects: dict[str, list[dict[str, Any]]]) -> list[Finding]:
    """Dangling references: a key on the referencing side with no object on the referenced side."""
    findings = []
    for link in ontology.link_types:
        if not link.source_key or not link.target_key:
            continue
        sources, targets = objects.get(link.source_type), objects.get(link.target_type)
        if sources is None or targets is None:
            continue
        if link.cardinality == "one_to_many":
            referencing, key, referenced, ref_key, ref_type = targets, link.target_key, sources, link.source_key, link.source_type
        else:
            referencing, key, referenced, ref_key, ref_type = sources, link.source_key, targets, link.target_key, link.target_type
        available = {str(row.get(ref_key)) for row in referenced if row.get(ref_key) is not None}
        values = [str(row.get(key)) for row in referencing if row.get(key) not in (None, "")]
        orphans = sorted({value for value in values if value not in available})
        if orphans:
            share = sum(value in orphans for value in values) / len(values)
            declared = DANGLING_DECLARATION in link.description
            findings.append(Finding(
                "consistency", "minor" if declared or share <= 0.5 else "important", "DAT-DANGLING",
                f"{link.source_type}.{link.api_name} → {link.target_type}",
                f"{len(orphans)} 个键值在 {ref_type} 中找不到对应对象（占引用的 {share:.0%}）"
                + ("；关系说明已声明这是预期情况。" if declared else "。"),
                "沿这条关系遍历会得到空结果；没有声明时，读者和 PM Assistant 会以为这条关系总能找到对象。",
                "确认是数据缺失、连接键选错，还是业务上的预期；预期情况在关系的 description 里写"
                f"「{DANGLING_DECLARATION}：原因」。", orphans[:5]))
    return findings


def _check_policy(ontology: Any, ctx: Any, objects: dict[str, list[dict[str, Any]]]) -> list[Finding]:
    """The stored operating policy must satisfy the same criteria its update action enforces."""
    action = ontology.action_type("updateOperatingPolicy")
    if action is None:
        return []
    findings = []
    for policy in objects.get(action.object_type, []):
        for criterion in action.submission_criteria:
            reason = criterion.check(ctx, {}, policy)
            if reason:
                findings.append(Finding(
                    "consistency", "critical", "DAT-POLICY", f"{action.object_type}/{policy.get('configId')}",
                    f"当前保存的参数违反了「{criterion.description}」：{reason}",
                    "所有风险、健康和资源判定都在引用这组参数，参数自相矛盾时结论不可信。",
                    "通过「调整运营口径」动作把参数改回满足条件的取值；不要直接改数据库。"))
    return findings


# ----------------------------------------------------------------------- stage 3
def check_pitfalls(metadata_dir: Path) -> list[Finding]:
    spec = importlib.util.spec_from_file_location("lint_ontology", SCRIPT_DIR / "lint_ontology.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    findings = []
    with contextlib.redirect_stdout(io.StringIO()):  # lint prints its own header
        results = lint.lint(metadata_dir)
    for category, where, message in results:
        severity = "minor" if any(m in message for m in MINOR_PITFALL_MESSAGES) else PITFALL_SEVERITY.get(category, "important")
        findings.append(Finding("pitfall", severity, f"PIT-{category}", where, message,
                                fix="见 SKILL.md「评审本体变更」对应条目；属于有意设计的，在关系或对象的 description 中写明原因。"))
    return findings


# ----------------------------------------------------------------- build package
def check_build(path: Path, ontology: Any | None) -> tuple[list[Finding], dict[str, Any] | None]:
    """Every rule needs a basis and an acceptance criterion; an empty exception table fails the build."""
    import yaml

    where = str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)
    def fail(code: str, location: str, problem: str, fix: str, severity: str = "critical") -> Finding:
        return Finding("build", severity, code, location, problem, BUILD_WHY.get(code, ""), fix)

    try:
        package = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [fail("BLD-SCHEMA", where, f"构建包无法读取：{exc}", "按 references/build-package.md 的模板重写。")], None
    if not isinstance(package, dict) or package.get("kind") != "OntologyBuild":
        return [fail("BLD-SCHEMA", where, "不是 kind: OntologyBuild 文档。", "按 references/build-package.md 的模板重写。")], None
    findings: list[Finding] = []
    rules = package.get("rules") or []
    exceptions = package.get("exceptions") or []
    # A design-stage package describes a future version: references to ontology elements and tests
    # that do not exist yet are planned work, not defects. Basis, acceptance and exceptions are still mandatory.
    design = package.get("stage") == "design"
    # A package replaced by a later build (supersededBy) is history: it documents the version it was
    # delivered with, so the current-version check is skipped and elements a later build removed are minor.
    superseded = package.get("supersededBy")
    planned = "minor" if design or superseded else "important"
    if superseded:
        successor = path.parent / str(superseded)
        try:
            successor_version = str((yaml.safe_load(successor.read_text(encoding="utf-8")) or {}).get("ontologyVersion"))
        except (OSError, yaml.YAMLError):
            successor_version = None
        if successor_version is None or not successor.is_file():
            findings.append(fail("BLD-SUPERSEDED", where, f"supersededBy 指向的构建包 {superseded} 不存在。",
                                 "写同一目录下取代它的构建包文件名。", "important"))
        elif _semver_or_none(successor_version) is None or _semver_or_none(str(package.get("ontologyVersion"))) is None \
                or _semver_or_none(successor_version) < _semver_or_none(str(package.get("ontologyVersion"))):
            findings.append(fail("BLD-SUPERSEDED", where, f"取代它的构建包 {superseded} 的版本 {successor_version} 低于本构建包。",
                                 "只有同版本或更高版本的构建包才能取代它。", "important"))
    if package.get("stage") not in (None, "design", "release"):
        findings.append(fail("BLD-SCHEMA", where, f"stage 只能是 design 或 release，当前是 {package.get('stage')!r}。", "改为 design 或 release。"))
    if not rules:
        findings.append(fail("BLD-SCHEMA", where, "rules 为空：构建包至少要有一条规则卡。", "把这次构建涉及的每条规则写成规则卡。"))
    version = str(package.get("ontologyVersion"))
    if superseded:
        pass
    elif ontology is not None and design and _semver_or_none(version) is not None and _semver_or_none(version) <= _semver(ontology.version):
        findings.append(fail("BLD-VERSION", where, f"设计稿的目标版本 {version} 不高于当前本体 {ontology.version}。",
                             "设计稿的 ontologyVersion 写计划发布的版本。", "important"))
    elif ontology is not None and not design and version != ontology.version:
        findings.append(fail("BLD-VERSION", where, f"构建包对应版本 {version}，当前本体是 {ontology.version}。",
                             "构建包要与本次发布的本体版本一致。", "important"))
    known = _ontology_names(ontology) if ontology is not None else None
    test_names = _test_names()
    ids = set()
    for index, rule in enumerate(rules, 1):
        rid = str(rule.get("id") or f"#{index}")
        loc = f"{where} · {rid}"
        if rid in ids:
            findings.append(fail("BLD-SCHEMA", loc, "规则编号重复。", "每条规则用唯一编号。"))
        ids.add(rid)
        if not str(rule.get("statement") or "").strip():
            findings.append(fail("BLD-SCHEMA", loc, "缺少 statement（规则本身）。", "用一句话写出规则。"))
        if not str(rule.get("basis") or "").strip():
            findings.append(fail("BLD-BASIS", loc, "缺少依据（basis）。", "写明来源文档和章节、用户决定或法规条款。"))
        if not str(rule.get("acceptance") or "").strip():
            findings.append(fail("BLD-ACCEPTANCE", loc, "缺少验收口径（acceptance）。", "写出可观察的通过条件：输入什么、期望看到什么。"))
        for test in rule.get("verifiedBy") or []:
            if test not in test_names:
                findings.append(fail("BLD-VERIFIED-BY", loc, f"verifiedBy 引用的测试 {test} " + ("尚未实现（设计阶段计划）。" if design else "已不存在（构建包已被取代）。" if superseded else "不存在。"),
                                     "写上真实存在的测试函数名，或先补测试。", planned))
        if not rule.get("verifiedBy"):
            findings.append(fail("BLD-VERIFIED-BY", loc, "没有 verifiedBy：验收口径还没有自动化测试支撑。",
                                 "为验收口径写测试，并把测试函数名填进 verifiedBy。", "important"))
        for name in rule.get("ontology") or []:
            if known is not None and name not in known:
                findings.append(fail("BLD-ONTOLOGY-REF", loc, f"ontology 引用的 {name} " + ("尚不存在（设计阶段计划新增）。" if design else "已被后续构建移除。" if superseded else "在本体中不存在。"),
                                     "写对象类型、`对象.属性`、`对象.关系`、动作、函数或校验规则的 apiName。", planned))
    if not exceptions:
        findings.append(fail("BLD-EXCEPTIONS-EMPTY", where, "例外表为空：一条例外都没有，按规定直接判不合格。",
                             "逐条规则想清楚：哪些情形不适用、边界值怎么处理、数据缺失时怎么办、与其他规则冲突时谁优先。"))
    covered = Counter()
    for index, item in enumerate(exceptions, 1):
        loc = f"{where} · 例外 {index}"
        if item.get("rule") not in ids:
            findings.append(fail("BLD-EXCEPTION", loc, f"例外引用的规则 {item.get('rule')!r} 不在规则卡中。", "改成已有的规则编号。", "important"))
        else:
            covered[item["rule"]] += 1
        for key, label in (("case", "例外情形"), ("handling", "处理方式"), ("basis", "依据")):
            if not str(item.get(key) or "").strip():
                findings.append(fail("BLD-EXCEPTION", loc, f"缺少{label}（{key}）。", f"补上{label}。", "important"))
    summary = {"path": where, "topic": package.get("topic") + ("（设计稿）" if design else ""), "rules": rules, "exceptions": exceptions,
               "superseded": superseded,
               "uncovered": sorted(ids - set(covered))}
    return findings, summary


BUILD_WHY = {
    "BLD-BASIS": "没有依据的规则无法判断对错，也无法在口径争议时追溯。",
    "BLD-ACCEPTANCE": "没有验收口径就无法证明规则已正确实现。",
    "BLD-EXCEPTIONS-EMPTY": "没有例外说明边界情形没有被思考过，上线后才会以缺陷的形式出现。",
    "BLD-VERIFIED-BY": "验收口径需要能重复执行的证据。",
    "BLD-SUPERSEDED": "历史构建包要能追到取代它的构建，否则无法判断它的规则是否仍然有效。",
}


def _ontology_names(ontology: Any) -> set[str]:
    from app.ontology.rules import RULES

    names = {t.api_name for t in ontology.object_types}
    names |= {f"{t.api_name}.{p.api_name}" for t in ontology.object_types for p in t.properties}
    names |= {f"{l.source_type}.{l.api_name}" for l in ontology.link_types}
    names |= {f"{l.target_type}.{l.reverse_api_name}" for l in ontology.link_types}
    names |= {a.api_name for a in ontology.action_types} | {f.api_name for f in ontology.functions}
    names |= {v.api_name for v in ontology.value_types}
    names |= set(getattr(RULES, "criteria", {}) or {})
    names |= set(getattr(RULES, "derivations", {}) or {})
    return names


def _test_names() -> set[str]:
    import re
    tests = REPO_ROOT / "backend" / "tests"
    return {m for path in tests.glob("test_*.py") for m in re.findall(r"^def (test_\w+)", path.read_text(encoding="utf-8"), re.M)}


# ------------------------------------------------------------------------ report
def compare(findings: list[Finding], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not previous:
        return {"round": 1, "resolved": [], "new": [f.fingerprint for f in findings], "persisting": [], "stuck": []}
    before = {item["fingerprint"]: item for item in previous["findings"]}
    now = {f.fingerprint for f in findings}
    persisting = sorted(now & set(before))
    blocking = {f.fingerprint for f in findings if f.severity in {"critical", "important"}}
    stuck = [fp for fp in persisting if fp in blocking and before[fp].get("rounds", 1) + 1 >= 3]
    return {"round": previous["round"] + 1, "resolved": sorted(set(before) - now),
            "new": sorted(now - set(before)), "persisting": persisting, "stuck": stuck,
            "rounds": {fp: before[fp].get("rounds", 1) + 1 for fp in persisting}}


def render(findings: list[Finding], meta: dict[str, Any], delta: dict[str, Any], builds: list[dict[str, Any]]) -> str:
    counts = Counter((f.stage, f.severity) for f in findings)
    blocking = sum(1 for f in findings if f.severity in {"critical", "important"})
    lines = [
        "# 本体诊断报告", "",
        f"- 本体：{meta['ontology']}　版本 {meta['version']}　指纹 {meta['checksum'][:8]}",
        f"- 元数据：`{meta['metadata_dir']}`　数据库：{meta['database'] or '未检查数据（加 --database）'}",
        f"- 时间：{meta['generated_at']}　第 {delta['round']} 轮", "",
        f"**结论：{'不通过' if blocking else '通过'}**。严重 {sum(1 for f in findings if f.severity == 'critical')} 项，"
        f"重要 {sum(1 for f in findings if f.severity == 'important')} 项，次要 {sum(1 for f in findings if f.severity == 'minor')} 项。", "",
        "## 与上一轮相比", "",
        f"已解决 {len(delta['resolved'])} 项，新增 {len(delta['new'])} 项，仍存在 {len(delta['persisting'])} 项。",
    ]
    if delta["round"] > 1 and delta["new"]:
        lines.append("新增的问题可能是上一轮修复引入的，先处理它们。")
    if delta["stuck"]:
        lines += ["", "**以下问题已连续 3 轮未解决，停止自动修复，交给用户决定：**", ""]
        lines += [f"- {fp}" for fp in delta["stuck"]]
    lines += ["", "## 各阶段汇总", "", "| 阶段 | 严重 | 重要 | 次要 |", "| --- | --- | --- | --- |"]
    for stage, label in STAGE_LABEL.items():
        lines.append(f"| {label} | {counts[(stage, 'critical')]} | {counts[(stage, 'important')]} | {counts[(stage, 'minor')]} |")
    if meta.get("skipped"):
        lines += ["", f"> 跳过的阶段：{meta['skipped']}。前一阶段有严重问题时，后面的检查没有意义。"]
    if not builds:
        lines += ["", "> 没有检查构建包（--build）。每次建立或修改本体都要提交构建包：规则卡写明依据和验收口径，并附例外表。"]
    for build in builds:
        lines += ["", f"## 构建包：{build['topic']}", "", f"文件：`{build['path']}`", "", "### 规则卡", "",
                  "| 编号 | 规则 | 依据 | 验收口径 | 自动化验证 |", "| --- | --- | --- | --- | --- |"]
        for rule in build["rules"]:
            lines.append(f"| {rule.get('id', '')} | {_cell(rule.get('statement'))} | {_cell(rule.get('basis'))} | "
                         f"{_cell(rule.get('acceptance'))} | {_cell('、'.join(rule.get('verifiedBy') or []) or '无')} |")
        lines += ["", "### 例外表", ""]
        if build["exceptions"]:
            lines += ["| 规则 | 例外情形 | 处理方式 | 依据 |", "| --- | --- | --- | --- |"]
            lines += [f"| {e.get('rule', '')} | {_cell(e.get('case'))} | {_cell(e.get('handling'))} | {_cell(e.get('basis'))} |"
                      for e in build["exceptions"]]
        else:
            lines.append("**例外表为空，判定不合格。**")
        if build["uncovered"]:
            lines += ["", f"没有登记例外的规则：{'、'.join(build['uncovered'])}。确认它们真的没有例外，而不是没有想过。"]
    lines += ["", "## 问题清单", ""]
    if not findings:
        lines.append("没有发现问题。")
    persisting = set(delta["persisting"])
    for index, f in enumerate(sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.stage, f.code, f.location)), 1):
        tag = "（仍存在）" if f.fingerprint in persisting else "（新）" if delta["round"] > 1 else ""
        lines += [f"### {index}. [{SEVERITY_LABEL[f.severity]}] {f.code} · {f.location}{tag}", "",
                  f"- 问题：{f.problem}"]
        if f.why:
            lines.append(f"- 为什么重要：{f.why}")
        if f.fix:
            lines.append(f"- 修复建议：{f.fix}")
        if f.evidence:
            lines.append(f"- 样例：{'；'.join(f.evidence)}")
        lines.append("")
    lines += [
        "## 修复指引", "",
        "1. 按「严重 → 重要」的顺序修复。一轮只处理一个阶段的问题，修完立即重新运行本脚本。",
        "2. 次要问题只在修复不会破坏一致性时处理；拿不准就在 description 中说明原因并保留。",
        "3. 修复前先弄清问题背后的模式：它为什么会出现、以后怎样避免。改业务含义（口径、范围、关系方向）需要用户确认。",
        "4. 本轮结束后，把新学到的模式追加到 `references/lessons.md`；同类问题出现第二次，就把它升级为 lint 规则或技能原则。",
        "5. 连续 3 轮仍未解决的问题停止自动修复，把报告中的该项和你的分析交给用户。",
    ]
    return "\n".join(lines) + "\n"


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "／").replace("\n", " ").strip() or "—"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--metadata-dir", type=Path, default=DEFAULT_METADATA_DIR)
    parser.add_argument("--database", type=Path, help="SQLite file to check the data against the model (read via a copy)")
    parser.add_argument("--build", type=Path, action="append", default=[],
                        help="ontology build package (YAML) to check; repeat for several")
    parser.add_argument("--require-build", action="store_true",
                        help="this run checks an ontology build: no build package at all fails it")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / ".ontology-diagnostics", help="report directory")
    parser.add_argument("--strict", action="store_true", help="exit non-zero when critical or important findings remain")
    args = parser.parse_args()

    findings, compiled = check_metadata(args.metadata_dir)
    skipped = []
    meta = {"metadata_dir": str(args.metadata_dir), "database": str(args.database) if args.database else None,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "ontology": "—", "version": "—", "checksum": "—"}
    if compiled is None:
        skipped.append("2 一致性（数据）、3 结构缺陷")
    else:
        ontology, digest = compiled
        meta.update(ontology=ontology.api_name, version=ontology.version, checksum=digest)
        findings += check_version_registry(ontology, digest, args.database)
        if args.database:
            findings += check_data(ontology, args.database)
        if any(f.severity == "critical" for f in findings):
            skipped.append("3 结构缺陷")
        else:
            findings += check_pitfalls(args.metadata_dir)
    builds = []
    if args.require_build and not args.build:
        findings.append(Finding(
            "build", "critical", "BLD-MISSING", "--build", "这是一次本体构建，但没有提交构建包，也就没有规则卡和例外表。",
            BUILD_WHY["BLD-EXCEPTIONS-EMPTY"], "按 references/build-package.md 写构建包，用 --build 传入后重新诊断。"))
    for path in args.build:
        build_findings, summary = check_build(path.resolve(), compiled[0] if compiled else None)
        findings += build_findings
        if summary:
            builds.append(summary)
    meta["skipped"] = "、".join(skipped)

    args.out.mkdir(parents=True, exist_ok=True)
    latest = args.out / "latest.json"
    previous = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else None
    delta = compare(findings, previous)
    report = render(findings, meta, delta, builds)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (args.out / f"report-{stamp}.md").write_text(report, encoding="utf-8")
    (args.out / "latest.md").write_text(report, encoding="utf-8")
    rounds = delta.get("rounds", {})
    latest.write_text(json.dumps({
        "round": delta["round"], "meta": meta,
        "findings": [{**asdict(f), "fingerprint": f.fingerprint, "rounds": rounds.get(f.fingerprint, 1)} for f in findings],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)
    print(f"报告已写入 {args.out / 'latest.md'}")
    if args.strict and any(f.severity in {"critical", "important"} for f in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
