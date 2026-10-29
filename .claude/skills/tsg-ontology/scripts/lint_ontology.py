"""Quality lint for the TSG PM ontology: checks the compiler does not enforce.

The compiler already rejects malformed metadata (unknown fields, broken references).
This script reports modelling smells drawn from LLM ontology-construction research:
missing provenance, instance-looking types, duplicate or reversed links, manual
properties nobody can edit, unmarked sensitive data, and risky actions without checks.

Run from the backend directory:
    .venv/bin/python ../.claude/skills/tsg-ontology/scripts/lint_ontology.py [--strict]
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
try:
    from app.ontology import load_ontology
except ModuleNotFoundError:
    sys.exit("Run this script from TSG-MVP/backend with its virtualenv (.venv/bin/python).")

# Parameters and singletons are intentionally unlinked. They say so in their description
# ("独立对象：原因"), the same way an expected dangling link says 「允许悬空：原因」.
ISOLATED_MARKER = "独立对象："
# Short derived sources are fine when they are plain column references.
MIN_DERIVED_SOURCE_LENGTH = 6
INSTANCE_HINT = re.compile(r"\d|^[A-Z]{2,}\d")
# A judgement (situation awareness: an inference about relations) is a derived enum named like a level or
# status. It needs an evidence property on the same object so the judgement can be traced to its facts.
JUDGEMENT_NAME = re.compile(r"(Level|Status)$")
EVIDENCE_NAME = re.compile(r"reason|flag|evidence", re.I)


def lint(metadata_dir: Path | None = None) -> list[tuple[str, str, str]]:
    ontology = load_ontology(metadata_dir) if metadata_dir else load_ontology()
    findings: list[tuple[str, str, str]] = []

    def warn(category: str, where: str, message: str) -> None:
        findings.append((category, where, message))

    edited: dict[tuple[str, str], list[str]] = defaultdict(list)
    for action in ontology.action_types:
        for edit in action.edits:
            object_type, _, prop = edit.partition(".")
            edited[(object_type, prop)].append(action.api_name)

    # A derived enum that carries an imported or maintained status (possibly with extra values such as
    # "unscheduled") is a looked-up status, not a judgement.
    maintained_enums = [
        frozenset(value for value, _ in prop.enum_values) for t in ontology.object_types for prop in t.properties
        if prop.data_type == "enum" and prop.source_kind != "derived"
    ]

    for object_type in ontology.object_types:
        name = object_type.api_name
        evidence = [p.api_name for p in object_type.properties
                    if p.source_kind == "derived" and p.data_type == "json" and EVIDENCE_NAME.search(p.api_name)]
        if INSTANCE_HINT.search(name) or INSTANCE_HINT.search(object_type.display_name):
            warn("类型或实例", name, "类型名里有编号，像是一个具体实例；实例应当是对象或枚举值")
        if len(object_type.description) < 10:
            warn("溯源", name, "对象类型缺少能说明业务含义的描述")
        display_names: dict[str, str] = {}
        for prop in object_type.properties:
            where = f"{name}.{prop.api_name}"
            if prop.display_name in display_names:
                warn("命名", where, f"显示名「{prop.display_name}」与 {display_names[prop.display_name]} 重复，读者无法区分")
            display_names[prop.display_name] = prop.api_name
            if not prop.source.strip():
                warn("溯源", where, "缺少 source，无法追溯数据来源或口径")
            if prop.source_kind == "derived" and not prop.derived_from and len(prop.source) < MIN_DERIVED_SOURCE_LENGTH:
                warn("口径", where, "派生属性既没有 derivedFrom，source 也写不出计算方法")
            # Amounts are recognised by their declared dimension, not guessed from names (lessons L06, L15).
            if prop.unit == "yuan" and not prop.marking:
                warn("安全", where, "金额属性（量纲为元）没有 FINANCE 标记")
            if (prop.source_kind == "derived" and prop.data_type == "enum" and JUDGEMENT_NAME.search(prop.api_name)
                    and not any(values <= {v for v, _ in prop.enum_values} for values in maintained_enums)
                    and not evidence):
                warn("依据", where, "判定类派生属性没有依据属性（如 xxxReasons）：读者无法追溯它由哪些事实和规则推出")
            manual = prop.source_kind == "manual"
            is_key = prop.api_name in {object_type.primary_key, "projectNumber", "updatedAt"} or prop.data_type == "datetime"
            if manual and not is_key and not prop.edited_by and (name, prop.api_name) not in edited and (name, "*") not in edited:
                warn("写入", where, "人工维护属性没有任何动作能修改它，只能走维护接口或无法维护")

    pairs: dict[frozenset[str], list[str]] = defaultdict(list)
    linked: set[str] = set()
    for link in ontology.link_types:
        linked.update({link.source_type, link.target_type})
        key = frozenset({link.source_type, link.target_type})
        signature = f"{link.source_type}.{link.api_name} → {link.target_type}（{link.cardinality}）"
        pairs[key].append(signature)
        if link.source_key and link.target_key is None and link.resolver is None:
            warn("关系", signature, "只有 sourceKey 没有 targetKey")
        if link.api_name == link.reverse_api_name:
            warn("关系", signature, "正反名称相同，读不出方向")
        if re.search(r"is_?type_?of|isA$", link.api_name, re.I):
            warn("关系", signature, "含义含糊的类型关系；区分“实例属于类型”和“子类型”")
    for key, signatures in pairs.items():
        if len(signatures) > 1 and len(key) == 2:
            warn("关系", " / ".join(signatures), "同一对对象之间有多条关系，确认它们含义不同，而不是重复或方向相反")

    for object_type in ontology.object_types:
        if object_type.api_name not in linked and ISOLATED_MARKER not in (object_type.description or ""):
            warn("关系", object_type.api_name, "孤立对象：没有任何关系，确认是否遗漏；有意独立的在说明里写「独立对象：原因」")

    for action in ontology.action_types:
        numeric = [p for p in action.parameters if p.data_type in {"money", "decimal", "integer", "date"}]
        if action.tone == "danger" and not action.submission_criteria:
            warn("校验", action.api_name, "高风险动作没有提交校验条件")
        elif numeric and not action.submission_criteria and not action.creates_object:
            warn("校验", action.api_name, f"有数值或日期参数（{', '.join(p.api_name for p in numeric)}）但没有提交校验条件；确认校验已在处理逻辑中完成")
        if not action.required_permissions:
            warn("安全", action.api_name, "动作没有声明权限")

    for value_type in ontology.value_types:
        labels = [label for _, label in value_type.values]
        if len(labels) != len(set(labels)):
            warn("命名", value_type.api_name, "枚举中有重复的显示名")

    print(f"本体 {ontology.api_name} v{ontology.version}：{len(ontology.object_types)} 个对象类型、"
          f"{len(ontology.link_types)} 种关系、{len(ontology.action_types)} 个动作、{len(ontology.functions)} 个函数")
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="exit non-zero when there are findings")
    parser.add_argument("--metadata-dir", type=Path, help="lint a candidate metadata directory instead of the live one")
    args = parser.parse_args()
    findings = lint(args.metadata_dir)
    if not findings:
        print("没有发现问题。")
        return
    by_category: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for category, where, message in findings:
        by_category[category].append((where, message))
    for category, items in by_category.items():
        print(f"\n[{category}] {len(items)} 项")
        for where, message in items:
            print(f"  - {where}：{message}")
    print(f"\n共 {len(findings)} 项提示。提示不一定都要修改；属于有意设计的，在评审意见中说明原因。")
    if args.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
